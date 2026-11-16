"""Eval-only observer below the production budget transport. No auth headers are saved."""
from __future__ import annotations

from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import time

import httpx

RUN_CONTEXT = ContextVar("experience_run", default=None)


class ProviderCapture:
    def __init__(self, directory: Path, *, mode, max_requests=160, max_cny=5, max_seconds=1200):
        self.directory, self.mode = directory, mode
        self.max_requests, self.limit_nano, self.max_seconds = max_requests, int(max_cny * 1e9), max_seconds
        self.calls = []
        self.reserved_nano = 0
        self.spent_nano = 0
        self.fatal = None
        self.started = time.monotonic()

    def emit(self, event):
        with (self.directory / "provider-events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def begin(self, request, amount):
        context = RUN_CONTEXT.get()
        if not context:
            raise RuntimeError("unattributed_model_request")
        if self.fatal or time.monotonic() - self.started > self.max_seconds:
            self.fatal = self.fatal or "elapsed_budget"
            raise RuntimeError(self.fatal)
        if len(self.calls) >= self.max_requests or self.spent_nano + self.reserved_nano + amount > self.limit_nano:
            self.fatal = "request_or_cost_budget"
            raise RuntimeError(self.fatal)
        # No await between checking and reserving: all coroutines share this event loop.
        self.reserved_nano += amount
        row = {"id": f"http-{len(self.calls)+1}", **context, "reserved_nano": amount,
               "request": json.loads(request.content), "request_sha256": hashlib.sha256(request.content).hexdigest(),
               "started_mono": time.monotonic(), "started_wall": time.time(), "complete": False,
               "usage_complete": False, "cost_nano": None, "error_type": None}
        self.calls.append(row)
        self.emit({"event": "start", **row})
        return row

    def finish(self, row, response=None, error=None):
        from app.services.model_budget import usage_cost
        row["elapsed_ms"] = (time.monotonic() - row["started_mono"]) * 1000
        row["clock_deviation_ms"] = (time.time() - row["started_wall"]) * 1000 - row["elapsed_ms"]
        row["complete"] = True
        row["error_type"] = type(error).__name__ if error else None
        if response is not None:
            row["status_code"] = response.status_code
            row["raw_response"] = response.content.decode("utf-8", errors="replace")
            try:
                payload = response.json()
                row["response"] = payload
                if response.is_success:
                    cost, prompt, output = usage_cost(payload, row["request"]["model"])
                    row.update(cost_nano=cost, input_tokens=prompt, output_tokens=output,
                               cache_hit_tokens=(payload.get("usage") or {}).get("prompt_cache_hit_tokens"),
                               response_model=payload.get("model"), usage_complete=True)
                    self.reserved_nano -= row["reserved_nano"]
                    self.spent_nano += cost
                    if cost > row["reserved_nano"]:
                        self.fatal = "reservation_exceeded"
            except (ValueError, TypeError, KeyError):
                row["usage_error"] = "missing_or_unverifiable_usage"
        if self.mode == "live" and not row["usage_complete"]:
            # Unknown usage is reserved, not treated as free. Stop starting new requests.
            self.fatal = self.fatal or "provider_error_or_unknown_usage"
        self.emit({"event": "finish", **row})

    def install(self):
        from app.services import model_budget
        observer = self
        original = model_budget.BudgetTransport.__init__
        class CapturedTransport(httpx.AsyncBaseTransport):
            def __init__(self, inner):
                self.inner = inner
            async def handle_async_request(self, request):
                _, _, amount = model_budget.prepare_request(request)
                row = observer.begin(request, amount)
                try:
                    if observer.mode == "offline":
                        response = httpx.Response(503, json={"error": "offline_transport_probe"}, request=request)
                    else:
                        response = await self.inner.handle_async_request(request)
                        await response.aread()
                    observer.finish(row, response=response)
                    return response
                except BaseException as exc:
                    if not row["complete"]:
                        observer.finish(row, error=exc)
                    raise
            async def aclose(self):
                await self.inner.aclose()
        def init(instance, inner=None):
            original(instance, inner)
            instance.inner = CapturedTransport(instance.inner)
        model_budget.BudgetTransport.__init__ = init
