from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.services.agent_model_usage import observed_post


class AIServiceError(RuntimeError):
    """A safe, user-facing error raised when the configured AI service fails."""


class StructuredAIServiceError(AIServiceError):
    """Provider failure with safe diagnostics for one structured invocation."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        duration_ms: int = 0,
        mode: str = "deepseek_structured",
        retry_after_seconds: float | None = None,
    ) -> None:
        self.category = category
        self.duration_ms = duration_ms
        self.mode = mode
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


StructuredCompletionMode = Literal[
    "deepseek_strict_tool",
    "deepseek_json_mode",
]


@dataclass(frozen=True)
class StructuredCompletionResult:
    payload: dict[str, Any] | None
    raw_output: str
    mode: StructuredCompletionMode
    finish_reason: str | None
    duration_ms: int
    output_chars: int
    parse_error: str | None = None
    fallback_reason: str | None = None


def _deepseek_structured_urls(base_url: str) -> tuple[str | None, str]:
    """Return optional official strict URL plus the stable chat URL."""

    normalized = base_url.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.hostname != "api.deepseek.com":
        return None, f"{normalized}/chat/completions"

    root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    if path not in {"", "/beta"}:
        return None, f"{normalized}/chat/completions"
    return (
        f"{root}/beta/chat/completions",
        f"{root}/chat/completions",
    )


def _strict_capability_error(status_code: int, body: Any) -> bool:
    if status_code not in {400, 404, 422}:
        return False
    if status_code == 404:
        return True
    detail = json.dumps(body, ensure_ascii=False, default=str).lower()
    return any(
        marker in detail
        for marker in (
            "strict",
            "tool",
            "schema",
            "beta",
            "unsupported",
            "not support",
        )
    )


def _structured_http_error(
    exc: httpx.HTTPStatusError,
    *,
    duration_ms: int,
    mode: str,
) -> StructuredAIServiceError:
    status_code = exc.response.status_code
    retry_after_seconds: float | None = None
    if status_code == 429:
        try:
            parsed_retry_after = float(exc.response.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            parsed_retry_after = -1
        if 0 <= parsed_retry_after <= 60:
            retry_after_seconds = parsed_retry_after
    return StructuredAIServiceError(
        f"AI 服务请求失败（HTTP {status_code}）",
        category=f"http_{status_code}",
        duration_ms=duration_ms,
        mode=mode,
        retry_after_seconds=retry_after_seconds,
    )


async def structured_chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    function_name: str,
    function_description: str,
    json_schema: dict[str, Any],
    json_example: dict[str, Any],
    timeout_seconds: float | None = None,
) -> StructuredCompletionResult:
    """Invoke DeepSeek with a strict tool schema and a safe JSON fallback.

    Strict mode is attempted only on DeepSeek's official endpoint. A fallback
    is allowed solely for capability incompatibility or a non-conforming tool
    envelope. Provider authentication, throttling, timeout, and server errors
    surface directly and are never hidden by a second transport.
    """

    if not settings.DEEPSEEK_API_KEY:
        raise StructuredAIServiceError(
            "AI 服务尚未配置，请先设置 DEEPSEEK_API_KEY",
            category="api_key_missing",
        )

    strict_url, json_url = _deepseek_structured_urls(
        settings.DEEPSEEK_BASE_URL
    )
    headers = {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"}
    started = time.perf_counter()
    fallback_reason: str | None = (
        "custom_gateway"
        if strict_url is None
        else None
    )

    async def post(
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        *,
        mode: StructuredCompletionMode,
    ) -> tuple[httpx.Response, dict[str, Any]]:
        try:
            response = await observed_post(
                client, url, source="structured",
                headers=headers,
                json=payload,
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else settings.AGENT_TIMEOUT_SECONDS
                ),
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise StructuredAIServiceError(
                "AI 服务请求超时，请稍后重试",
                category="request_timeout",
                duration_ms=round((time.perf_counter() - started) * 1000),
                mode=mode,
            ) from exc
        except httpx.HTTPStatusError:
            raise
        except httpx.RequestError as exc:
            raise StructuredAIServiceError(
                "暂时无法连接 AI 服务，请稍后重试",
                category="request_error",
                duration_ms=round((time.perf_counter() - started) * 1000),
                mode=mode,
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise StructuredAIServiceError(
                "AI 服务返回了无法识别的数据",
                category="response_not_json",
                duration_ms=round((time.perf_counter() - started) * 1000),
                mode=mode,
            ) from exc
        if not isinstance(body, dict):
            raise StructuredAIServiceError(
                "AI 服务返回了无法识别的数据",
                category="response_not_object",
                duration_ms=round((time.perf_counter() - started) * 1000),
                mode=mode,
            )
        return response, body

    async with httpx.AsyncClient() as client:
        if strict_url is not None:
            strict_payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "thinking": {"type": "disabled"},
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": function_name,
                        "description": function_description,
                        "strict": True,
                        "parameters": json_schema,
                    },
                }],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": function_name},
                },
            }
            try:
                _, strict_body = await post(
                    client,
                    strict_url,
                    strict_payload,
                    mode="deepseek_strict_tool",
                )
            except httpx.HTTPStatusError as exc:
                try:
                    error_body = exc.response.json()
                except ValueError:
                    error_body = exc.response.text
                if not _strict_capability_error(
                    exc.response.status_code,
                    error_body,
                ):
                    raise _structured_http_error(
                        exc,
                        duration_ms=round(
                            (time.perf_counter() - started) * 1000
                        ),
                        mode="deepseek_strict_tool",
                    ) from exc
                fallback_reason = f"strict_http_{exc.response.status_code}"
            else:
                choices = strict_body.get("choices")
                choice = choices[0] if isinstance(choices, list) and choices else {}
                message = choice.get("message") if isinstance(choice, dict) else {}
                tool_calls = (
                    message.get("tool_calls")
                    if isinstance(message, dict)
                    else None
                )
                selected = next((
                    item for item in (tool_calls or [])
                    if isinstance(item, dict)
                    and isinstance(item.get("function"), dict)
                    and item["function"].get("name") == function_name
                ), None)
                arguments = (
                    selected["function"].get("arguments", "")
                    if selected is not None
                    else ""
                )
                if isinstance(arguments, str) and arguments.strip():
                    try:
                        parsed_arguments = json.loads(arguments)
                    except ValueError:
                        fallback_reason = "strict_arguments_invalid_json"
                    else:
                        if isinstance(parsed_arguments, dict):
                            return StructuredCompletionResult(
                                payload=parsed_arguments,
                                raw_output=arguments,
                                mode="deepseek_strict_tool",
                                finish_reason=(
                                    choice.get("finish_reason")
                                    if isinstance(choice, dict)
                                    else None
                                ),
                                duration_ms=round(
                                    (time.perf_counter() - started) * 1000
                                ),
                                output_chars=len(arguments),
                            )
                        fallback_reason = "strict_payload_not_object"
                else:
                    fallback_reason = "strict_tool_call_missing"

        json_instruction = (
            "你必须只输出一个 JSON 对象，不得使用 Markdown 代码块或增加解释。"
            "输出必须遵守以下 JSON Schema："
            f"{json.dumps(json_schema, ensure_ascii=False, separators=(',', ':'))}"
            "。正确输出示例："
            f"{json.dumps(json_example, ensure_ascii=False, separators=(',', ':'))}"
        )
        json_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": json_instruction},
                *messages,
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        }
        try:
            _, json_body = await post(
                client,
                json_url,
                json_payload,
                mode="deepseek_json_mode",
            )
        except httpx.HTTPStatusError as exc:
            raise _structured_http_error(
                exc,
                duration_ms=round((time.perf_counter() - started) * 1000),
                mode="deepseek_json_mode",
            ) from exc

    choices = json_body.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    raw_output = content if isinstance(content, str) else ""
    finish_reason = (
        choice.get("finish_reason")
        if isinstance(choice, dict)
        else None
    )
    parsed_payload: dict[str, Any] | None = None
    parse_error: str | None = None
    if not raw_output.strip():
        parse_error = "empty_content"
    else:
        try:
            decoded = json.loads(raw_output)
        except ValueError:
            parse_error = "invalid_json"
        else:
            if isinstance(decoded, dict):
                parsed_payload = decoded
            else:
                parse_error = "payload_not_object"
    if finish_reason == "length" and parse_error is None:
        parse_error = "output_truncated"
        parsed_payload = None
    return StructuredCompletionResult(
        payload=parsed_payload,
        raw_output=raw_output,
        mode="deepseek_json_mode",
        finish_reason=finish_reason,
        duration_ms=round((time.perf_counter() - started) * 1000),
        output_chars=len(raw_output),
        parse_error=parse_error,
        fallback_reason=fallback_reason,
    )


async def chat_completion(
    messages: list[dict],
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
    thinking: bool = False,
) -> str:
    if not settings.DEEPSEEK_API_KEY:
        raise AIServiceError("AI 服务尚未配置，请先设置 DEEPSEEK_API_KEY")

    payload: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    try:
        async with httpx.AsyncClient() as client:
            response = await observed_post(
                client, url, source="chat",
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise AIServiceError(
            f"AI 服务请求失败（HTTP {exc.response.status_code}）"
        ) from exc
    except httpx.RequestError as exc:
        raise AIServiceError("暂时无法连接 AI 服务，请稍后重试") from exc

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIServiceError("AI 服务返回了无法识别的数据") from exc
    if not isinstance(content, str) or not content.strip():
        raise AIServiceError("AI 服务未返回有效内容")
    return content
