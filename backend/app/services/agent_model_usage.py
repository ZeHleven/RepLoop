"""Observe client, transport and response boundaries without changing requests.

Transport entry does not prove provider receipt. No prompt, URL, headers or
exception text is persisted. Worker execution checkpoints these observations
in its independent attempt journal; the Run snapshot remains attempt scoped.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar

import httpx
from app.services.agent_attempt_journal import checkpoint_model, AttemptJournalUnavailable, assert_journal_available

from app.schemas.agent_model_usage import AgentModelUsage, ModelClientObservation, ModelHTTPResponse


_HTTPX_CLIENT = httpx.AsyncClient
_CURRENT: ContextVar["ModelUsageLedger | None"] = ContextVar("agent_model_usage", default=None)
_CALL: ContextVar["_CallScope | None"] = ContextVar("agent_model_usage_call", default=None)


def _tokens(value):
    return value if type(value) is int and value >= 0 else None


def _response_usage(response):
    result = ModelHTTPResponse(http_status=response.status_code)
    # Never read an unconsumed stream for telemetry.
    try:
        body = response.json()
    except (ValueError, httpx.ResponseNotRead):
        body = None
    usage = body.get("usage") if isinstance(body, dict) else None
    if isinstance(usage, dict):
        result.input_tokens = _tokens(usage.get("prompt_tokens"))
        result.output_tokens = _tokens(usage.get("completion_tokens"))
        details = usage.get("prompt_tokens_details")
        cached = _tokens(usage.get("prompt_cache_hit_tokens", details.get("cached_tokens") if isinstance(details, dict) else None))
        if cached is not None and result.input_tokens is not None and cached <= result.input_tokens:
            result.cached_input_tokens = cached
    return result


class _CallScope:
    def __init__(self, ledger, call):
        self.ledger = ledger
        self.call = call
        self.responses: list[httpx.Response] = []

    def response(self, response):
        if not any(r is response for r in self.responses):
            self.responses.append(response)
        self.refresh()

    def refresh(self):
        self.call.responses = [_response_usage(r) for r in self.responses]
        if self.call.responses:
            last = self.call.responses[-1]
            for field in ("http_status", "input_tokens", "output_tokens", "cached_input_tokens"):
                setattr(self.call, field, getattr(last, field))


class _ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner):
        self.inner = inner

    async def handle_async_request(self, request):
        scope = _CALL.get()
        if scope is None or not scope.ledger.active:
            return await self.inner.handle_async_request(request)
        scope.call.transport_attempts += 1
        await checkpoint_model(scope.ledger, new_action=True)
        try:
            response = await self.inner.handle_async_request(request)
        except Exception:
            scope.call.transport_errors += 1
            raise
        scope.response(response)
        await checkpoint_model(scope.ledger)
        return response

    async def aclose(self):
        await self.inner.aclose()

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.inner.__aexit__(exc_type, exc_value, traceback)


def _instrument_client(client):
    """Preserve existing client, proxy selection and custom transports.

    httpx has no public post-construction transport observer. This small adapter
    wraps its default/mounted transports; unsupported custom clients remain
    explicitly unobserved. Reconstructing the client would alter auth/proxies.
    """
    if type(client) not in {_HTTPX_CLIENT, ObservedAsyncClient}:
        return False
    transport = getattr(client, "_transport", None)
    mounts = getattr(client, "_mounts", None)
    if not isinstance(transport, httpx.AsyncBaseTransport) or not isinstance(mounts, dict):
        return False
    if any(t is not None and not isinstance(t, httpx.AsyncBaseTransport) for t in mounts.values()):
        return False
    wrapped = {}

    def wrap(item):
        if item is None or isinstance(item, _ObservedTransport):
            return item
        if id(item) not in wrapped:
            wrapped[id(item)] = _ObservedTransport(item)
        return wrapped[id(item)]

    client._transport = wrap(transport)
    client._mounts = {pattern: wrap(item) for pattern, item in mounts.items()}
    return True


class ModelUsageLedger:
    def __init__(self, worker_attempt: int):
        self.worker_attempt = worker_attempt
        self.calls: list[ModelClientObservation] = []
        self.clients: dict[str, ObservedAsyncClient] = {}
        self.active = True

    def begin(self, source):
        if not self.active:
            return None
        call = ModelClientObservation(sequence=len(self.calls) + 1, source=source, outcome="pending")
        self.calls.append(call)
        return call

    def snapshot(self, *, complete: bool) -> AgentModelUsage:
        responses = [r for c in self.calls for r in c.responses]
        known = [r for r in responses if r.input_tokens is not None and r.output_tokens is not None]
        incoming = sum(r.input_tokens or 0 for r in responses)
        outgoing = sum(r.output_tokens or 0 for r in responses)
        pending = sum(c.outcome in {"pending", "cancelled"} for c in self.calls)
        requests = sum(max(c.transport_attempts, len(c.responses)) for c in self.calls)
        uncertain = any(c.outcome in {"client_error", "transport_error"} for c in self.calls)
        full = complete and not pending and not uncertain and len(known) == requests and self.worker_attempt <= 1
        return AgentModelUsage(
            worker_attempt=self.worker_attempt, prior_attempts_unobserved=self.worker_attempt > 1,
            observation_complete=complete and not pending,
            client_attempts=len(self.calls), transport_attempts=sum(c.transport_attempts for c in self.calls),
            http_requests=requests, http_responses=len(responses),
            http_successes=sum(200 <= r.http_status < 300 for r in responses),
            http_errors=sum(not 200 <= r.http_status < 300 for r in responses),
            transport_errors=sum(c.transport_errors for c in self.calls),
            local_errors=sum(c.outcome == "local_error" for c in self.calls),
            unclassified_client_errors=sum(c.outcome == "client_error" for c in self.calls),
            pending_or_cancelled=pending, usage_known_requests=len(known),
            known_input_tokens=incoming, known_output_tokens=outgoing,
            input_tokens=incoming if full else None, output_tokens=outgoing if full else None,
            calls=[c.model_copy(deep=True) for c in self.calls],
        )


class ObservedAsyncClient(httpx.AsyncClient):
    def __init__(self, *, source, **kwargs):
        super().__init__(**kwargs)
        self.usage_source = source

    async def send(self, request, **kwargs):
        return await _observed_http(self, self.usage_source, super().send(request, **kwargs))


async def observed_post(client, url, *, source, **kwargs):
    # Keep the application's original AsyncClient constructor/mock injection.
    if isinstance(client, ObservedAsyncClient):
        return await client.post(url, **kwargs)
    return await _observed_http(client, source, client.post(url, **kwargs))


async def _observed_http(client, source, operation):
    ledger = _CURRENT.get()
    call = ledger.begin(source) if ledger is not None else None
    if call is None:
        return await operation
    scope = _CallScope(ledger, call)
    call.transport_boundary_observed = _instrument_client(client)
    token = _CALL.set(scope)
    started = False
    try:
        await checkpoint_model(ledger, new_action=True)
        started = True
        response = await operation
        scope.response(response)
        call.outcome = "http_success" if 200 <= response.status_code < 300 else "http_error"
        return response
    except AttemptJournalUnavailable:
        raise
    except BaseException as exc:
        if not isinstance(exc, Exception):
            call.outcome = "cancelled"
        elif call.transport_errors:
            call.outcome = "transport_error"
        elif call.transport_attempts and isinstance(exc, httpx.TransportError):
            # Body reading occurs after the transport returns response headers.
            call.transport_errors += 1
            call.outcome = "transport_error"
        elif call.transport_boundary_observed and not call.transport_attempts and not scope.responses:
            call.outcome = "local_error"
        else:
            call.outcome = "client_error"
        raise
    finally:
        scope.refresh()
        _CALL.reset(token)
        if not started:
            operation.close()
        await checkpoint_model(ledger)


def model_http_options(source="agent"):
    ledger = _CURRENT.get()
    if ledger is None or not ledger.active:
        return {}
    if source not in ledger.clients:
        ledger.clients[source] = ObservedAsyncClient(source=source)
    return {"http_async_client": ledger.clients[source]}


def usage_values(*, complete: bool):
    # A caught SDK error must not let a broken durable journal report success.
    assert_journal_available()
    ledger = _CURRENT.get()
    if ledger is None:
        return {}
    usage = ledger.snapshot(complete=complete)
    return {"model_usage": usage.model_dump(mode="json"), "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}


def apply_usage(run, *, complete: bool):
    for name, value in usage_values(complete=complete).items():
        setattr(run, name, value)


@asynccontextmanager
async def observe_model_usage(worker_attempt: int):
    ledger = ModelUsageLedger(worker_attempt)
    token = _CURRENT.set(ledger)
    try:
        await checkpoint_model(ledger)
        yield ledger
    finally:
        ledger.active = False
        _CURRENT.reset(token)
        for client in ledger.clients.values():
            await client.aclose()
