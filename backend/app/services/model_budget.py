"""Shared model transport: reserve before send, settle once, fail closed.

The ledger uses the published peak/cache-miss tariff (2026-10-22), so it is
deliberately conservative, not the provider invoice. Text-only prompts are
bounded by four times the complete JSON byte length plus 8192 framing tokens.
Unknown models, endpoints, images, server tools and streaming fail closed.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import AsyncSessionLocal
from app.models.model_budget import ModelBudget, ModelCharge
from app.services.ai_client import AIServiceError

LIMIT_NANO = 5_000_000_000
PRICING_VERSION = "deepseek-2026-10-22-peak-cache-miss"
RATES = {
    "deepseek-v4-flash": (2000, 8000),
    "deepseek-flash": (2000, 8000),
    "deepseek-v4-pro": (9000, 27000),
}
MAX_OUTPUT = 4096
MAX_CONTEXT = 1_000_000
EXHAUSTED = "你的5元体验额度已用完，或剩余额度不足以完成本次请求。"
UNCERTAIN = "模型费用暂时无法核实，已暂停调用以保护你的体验额度。请联系管理员。"


class ModelBudgetError(AIServiceError):
    def __init__(self, message=EXHAUSTED, code="model_budget_exhausted"):
        self.code = code
        super().__init__(message)


@dataclass
class BillingScope:
    user_id: str
    failure: ModelBudgetError | None = None


_scope: ContextVar[BillingScope | None] = ContextVar("model_billing_scope", default=None)


@contextmanager
def billing_user(user_id: str):
    scope = BillingScope(user_id)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def deny(message=EXHAUSTED, code="model_budget_exhausted"):
    error = ModelBudgetError(message, code)
    scope = _scope.get()
    if scope is not None:
        scope.failure = error
    raise error


async def budget_status(user_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        row = await db.get(ModelBudget, user_id)
        spent = row.spent_nano if row else 0
        reserved = row.reserved_nano if row else 0
        return {"limit_yuan": "5.00", "spent_yuan": f"{spent / 1e9:.9f}",
                "reserved_yuan": f"{reserved / 1e9:.9f}",
                "remaining_yuan": f"{max(0, LIMIT_NANO - spent - reserved) / 1e9:.9f}",
                "blocked": row.blocked if row else False,
                "period": "lifetime", "pricing_version": PRICING_VERSION}


async def reserve(user_id: str, model: str, amount: int) -> str:
    if type(amount) is not int or amount <= 0:
        deny(UNCERTAIN, "model_budget_unavailable")
    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(insert(ModelBudget).values(user_id=user_id).on_conflict_do_nothing())
        row = await db.scalar(select(ModelBudget).where(
            ModelBudget.user_id == user_id).with_for_update())
        if row.blocked:
            deny(UNCERTAIN, "model_budget_unavailable")
        if row.spent_nano + row.reserved_nano + amount > LIMIT_NANO:
            deny()
        row.reserved_nano += amount
        charge = ModelCharge(user_id=user_id, model=model, reserved_nano=amount,
                             pricing_version=PRICING_VERSION, status="reserved")
        db.add(charge)
        await db.flush()
        return charge.id


def usage_cost(body: Any, model: str) -> tuple[int, int, int]:
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        raise ValueError("missing usage")
    prompt, output, total = (usage.get(k) for k in (
        "prompt_tokens", "completion_tokens", "total_tokens"))
    if any(type(n) is not int or n < 0 for n in (prompt, output, total)):
        raise ValueError("invalid usage")
    if prompt + output != total:
        raise ValueError("inconsistent usage")
    actual_model = body.get("model")
    # Provider aliases are acceptable only inside the same price family.
    allowed = ({"deepseek-flash", "deepseek-v4-flash", "deepseek-v4.1-flash-1017"}
               if "flash" in model else {"deepseek-v4-pro", "deepseek-v4-pro-0813"})
    if not isinstance(actual_model, str) or actual_model.lower() not in allowed:
        raise ValueError("unverified response model")
    input_rate, output_rate = RATES[model]
    return prompt * input_rate + output * output_rate, prompt, output


async def settle(charge_id: str, result: tuple[int, int, int] | None) -> bool:
    async with AsyncSessionLocal() as db, db.begin():
        charge = await db.scalar(select(ModelCharge).where(
            ModelCharge.id == charge_id).with_for_update())
        if charge.status != "reserved":
            return charge.status == "settled"
        row = await db.scalar(select(ModelBudget).where(
            ModelBudget.user_id == charge.user_id).with_for_update())
        if result is None:
            charge.status = "uncertain"
            row.blocked = True
            return False  # Retain the full reservation, including after restart.
        cost, prompt, output = result
        charge.actual_nano = cost
        charge.prompt_tokens = prompt
        charge.completion_tokens = output
        row.reserved_nano -= charge.reserved_nano
        row.spent_nano += cost
        charge.status = "settled"
        if cost > charge.reserved_nano:
            row.blocked = True
            charge.status = "overrun"
            return False
        return True


async def record_http_error(charge_id: str):
    """Keep the entire upper bound charged; permit the existing bounded fallback."""
    async with AsyncSessionLocal() as db, db.begin():
        charge = await db.scalar(select(ModelCharge).where(
            ModelCharge.id == charge_id).with_for_update())
        if charge.status != "reserved":
            return
        row = await db.scalar(select(ModelBudget).where(
            ModelBudget.user_id == charge.user_id).with_for_update())
        row.reserved_nano -= charge.reserved_nano
        row.spent_nano += charge.reserved_nano
        charge.status = "http_error_estimate"


def prepare_request(request: httpx.Request) -> tuple[httpx.Request, str, int]:
    if (request.url.scheme != "https" or request.url.host != "api.deepseek.com"
            or request.url.path not in {"/chat/completions", "/v1/chat/completions", "/beta/chat/completions"}
            or request.method != "POST"):
        deny("此模型服务尚未配置费用保护，暂不可用。", "model_budget_unavailable")
    try:
        payload = json.loads(request.content)
        model = payload["model"]
        if model not in RATES or payload.get("stream") or payload.get("n", 1) != 1:
            raise ValueError("unsupported model or mode")
        if not isinstance(payload.get("messages"), list):
            raise ValueError("missing messages")
        # No remote/media content or server-side metered tools.
        if any(not isinstance(m, dict) or not isinstance(m.get("content", ""), (str, type(None)))
               for m in payload["messages"]):
            raise ValueError("non-text message")
        if any(t.get("type") != "function" for t in payload.get("tools", [])):
            raise ValueError("server tool")
        output = payload.pop("max_completion_tokens", payload.get("max_tokens", MAX_OUTPUT))
        if type(output) is not int or output <= 0:
            raise ValueError("invalid output limit")
        payload["max_tokens"] = min(output, MAX_OUTPUT)
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        prompt_bound = len(content) * 4 + 8192
        if prompt_bound + payload["max_tokens"] > MAX_CONTEXT:
            raise ValueError("prompt too large")
        in_rate, out_rate = RATES[model]
        amount = prompt_bound * in_rate + payload["max_tokens"] * out_rate
        headers = dict(request.headers)
        headers.pop("content-length", None)
        bounded = httpx.Request(request.method, request.url, headers=headers, content=content,
                                extensions=request.extensions)
        return bounded, model, amount
    except (ValueError, KeyError, TypeError, AttributeError):
        deny("当前请求无法安全核算模型费用，请缩短内容或联系管理员。", "model_budget_unavailable")


class BudgetTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self.inner = inner if inner is not None else httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        scope = _scope.get()
        if scope is None:
            deny("模型请求缺少计费账号，已停止调用。", "model_budget_unavailable")
        if scope.failure is not None:
            raise scope.failure
        bounded, model, amount = prepare_request(request)
        try:
            charge_id = await reserve(scope.user_id, model, amount)
        except ModelBudgetError:
            raise
        except Exception:
            deny(UNCERTAIN, "model_budget_unavailable")
        try:
            response = await self.inner.handle_async_request(bounded)
            await response.aread()
            if not response.is_success:
                await record_http_error(charge_id)
                return response
            result = usage_cost(response.json(), model)
            ok = await settle(charge_id, result)
        except BaseException as exc:
            # Cancellation/process death leaves a durable reservation. No automatic refund.
            try:
                await settle(charge_id, None)
            except BaseException:
                pass
            error = ModelBudgetError(UNCERTAIN, "model_budget_unavailable")
            scope.failure = error
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise error
        if not ok:
            deny(UNCERTAIN, "model_budget_unavailable")
        return response

    async def aclose(self):
        await self.inner.aclose()


def model_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=BudgetTransport(), follow_redirects=False)
