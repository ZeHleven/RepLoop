from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import settings
from app.services.ai_client import (
    AIServiceError,
    StructuredAIServiceError,
    chat_completion,
    structured_chat_completion,
)


STRICT_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


async def _structured_call(*, timeout_seconds=None):
    return await structured_chat_completion(
        [{"role": "user", "content": "produce a value"}],
        model="deepseek-v4-flash",
        max_tokens=100,
        temperature=0,
        function_name="submit_value",
        function_description="Submit a value",
        json_schema=STRICT_SCHEMA,
        json_example={"value": "ok"},
        timeout_seconds=timeout_seconds,
    )


@pytest.mark.asyncio
async def test_chat_completion_requires_api_key():
    with patch.object(settings, "DEEPSEEK_API_KEY", ""):
        with pytest.raises(AIServiceError, match="DEEPSEEK_API_KEY"):
            await chat_completion(
                [{"role": "user", "content": "hello"}],
                model="deepseek-v4-flash",
                max_tokens=100,
                temperature=0.2,
            )


@pytest.mark.asyncio
async def test_chat_completion_uses_v4_payload_and_official_endpoint():
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [{"message": {"content": "ok"}}]
    }
    client = AsyncMock()
    client.post.return_value = response

    with patch.object(settings, "DEEPSEEK_API_KEY", "test-key"), \
         patch.object(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com"), \
         patch("app.services.ai_client.httpx.AsyncClient") as client_class:
        client_class.return_value.__aenter__.return_value = client
        client_class.return_value.__aexit__.return_value = False
        result = await chat_completion(
            [{"role": "user", "content": "hello"}],
            model="deepseek-v4-pro",
            max_tokens=100,
            temperature=0.2,
            json_mode=True,
            thinking=True,
        )

    assert result == "ok"
    call = client.post.call_args
    assert call.args[0] == "https://api.deepseek.com/chat/completions"
    assert call.kwargs["json"]["model"] == "deepseek-v4-pro"
    assert call.kwargs["json"]["response_format"] == {"type": "json_object"}
    assert call.kwargs["json"]["thinking"] == {"type": "enabled"}


@pytest.mark.asyncio
async def test_structured_completion_prefers_official_strict_tool_call():
    request = httpx.Request("POST", "https://api.deepseek.com/beta/chat/completions")
    response = httpx.Response(
        200,
        request=request,
        json={
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "submit_value",
                            "arguments": '{"value":"ok"}',
                        },
                    }],
                },
            }],
        },
    )
    client = AsyncMock()
    client.post.return_value = response

    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        patch("app.services.ai_client.httpx.AsyncClient") as client_class,
    ):
        client_class.return_value.__aenter__.return_value = client
        client_class.return_value.__aexit__.return_value = False
        result = await _structured_call(timeout_seconds=14)

    assert result.payload == {"value": "ok"}
    assert result.mode == "deepseek_strict_tool"
    assert result.finish_reason == "tool_calls"
    assert client.post.call_count == 1
    call = client.post.call_args
    assert call.args[0] == "https://api.deepseek.com/beta/chat/completions"
    assert call.kwargs["timeout"] == 14
    function = call.kwargs["json"]["tools"][0]["function"]
    assert function["strict"] is True
    assert function["parameters"] == STRICT_SCHEMA
    assert call.kwargs["json"]["tool_choice"]["function"]["name"] == "submit_value"


@pytest.mark.asyncio
async def test_structured_completion_falls_back_only_for_strict_capability():
    strict_request = httpx.Request(
        "POST", "https://api.deepseek.com/beta/chat/completions"
    )
    strict_response = httpx.Response(
        400,
        request=strict_request,
        json={"error": {"message": "strict tool schema is unsupported"}},
    )
    json_request = httpx.Request(
        "POST", "https://api.deepseek.com/chat/completions"
    )
    json_response = httpx.Response(
        200,
        request=json_request,
        json={
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"value":"fallback"}'},
            }],
        },
    )
    client = AsyncMock()
    client.post.side_effect = [strict_response, json_response]

    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        patch("app.services.ai_client.httpx.AsyncClient") as client_class,
    ):
        client_class.return_value.__aenter__.return_value = client
        client_class.return_value.__aexit__.return_value = False
        result = await _structured_call()

    assert result.payload == {"value": "fallback"}
    assert result.mode == "deepseek_json_mode"
    assert result.fallback_reason == "strict_http_400"
    assert client.post.call_count == 2
    fallback_call = client.post.call_args_list[1]
    assert fallback_call.args[0] == "https://api.deepseek.com/chat/completions"
    assert fallback_call.kwargs["json"]["response_format"] == {
        "type": "json_object"
    }
    instruction = fallback_call.kwargs["json"]["messages"][0]["content"]
    assert "JSON Schema" in instruction
    assert '"value":"ok"' in instruction


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 429, 500])
async def test_structured_completion_does_not_fallback_for_provider_errors(
    status_code,
):
    request = httpx.Request(
        "POST", "https://api.deepseek.com/beta/chat/completions"
    )
    response = httpx.Response(
        status_code,
        request=request,
        headers={"Retry-After": "0.25"} if status_code == 429 else None,
        json={"error": {"message": "provider failure"}},
    )
    client = AsyncMock()
    client.post.return_value = response

    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        patch("app.services.ai_client.httpx.AsyncClient") as client_class,
    ):
        client_class.return_value.__aenter__.return_value = client
        client_class.return_value.__aexit__.return_value = False
        with pytest.raises(StructuredAIServiceError) as raised:
            await _structured_call()

    assert raised.value.category == f"http_{status_code}"
    assert raised.value.retry_after_seconds == (
        0.25 if status_code == 429 else None
    )
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_structured_completion_does_not_fallback_after_timeout():
    request = httpx.Request(
        "POST", "https://api.deepseek.com/beta/chat/completions"
    )
    client = AsyncMock()
    client.post.side_effect = httpx.ReadTimeout("timed out", request=request)

    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        patch("app.services.ai_client.httpx.AsyncClient") as client_class,
    ):
        client_class.return_value.__aenter__.return_value = client
        client_class.return_value.__aexit__.return_value = False
        with pytest.raises(StructuredAIServiceError) as raised:
            await _structured_call()

    assert raised.value.category == "request_timeout"
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_structured_completion_custom_gateway_uses_json_mode_directly():
    request = httpx.Request("POST", "https://gateway.example/v1/chat/completions")
    response = httpx.Response(
        200,
        request=request,
        json={
            "choices": [{
                "finish_reason": "length",
                "message": {"content": '{"value":"cut"}'},
            }],
        },
    )
    client = AsyncMock()
    client.post.return_value = response

    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(settings, "DEEPSEEK_BASE_URL", "https://gateway.example/v1"),
        patch("app.services.ai_client.httpx.AsyncClient") as client_class,
    ):
        client_class.return_value.__aenter__.return_value = client
        client_class.return_value.__aexit__.return_value = False
        result = await _structured_call()

    assert result.payload is None
    assert result.parse_error == "output_truncated"
    assert result.fallback_reason == "custom_gateway"
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_structured_completion_reports_empty_json_mode_content():
    request = httpx.Request("POST", "https://gateway.example/chat/completions")
    response = httpx.Response(
        200,
        request=request,
        json={
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": ""},
            }],
        },
    )
    client = AsyncMock()
    client.post.return_value = response

    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(settings, "DEEPSEEK_BASE_URL", "https://gateway.example"),
        patch("app.services.ai_client.httpx.AsyncClient") as client_class,
    ):
        client_class.return_value.__aenter__.return_value = client
        client_class.return_value.__aexit__.return_value = False
        result = await _structured_call()

    assert result.payload is None
    assert result.parse_error == "empty_content"
    assert result.output_chars == 0
