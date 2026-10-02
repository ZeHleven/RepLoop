import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import AsyncSessionLocal
from app.routers import (
    agent, auth, chat, exercises, foods, meals, profile, proposals, workouts,
)
from app.deps import get_current_user
from app.models.user import User
from app.services.ai_client import AIServiceError
from app.services.agent_jobs import agent_worker_loop
from app.startup_diagnostics import log_agent_startup_diagnostic


logging.getLogger("app").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log_agent_startup_diagnostic()
    stop_event = asyncio.Event()
    worker_task: asyncio.Task | None = None
    if settings.AGENT_ASYNC_WORKER_ENABLED:
        worker_task = asyncio.create_task(
            agent_worker_loop(stop_event),
            name="agent-durable-worker",
        )
    try:
        yield
    finally:
        stop_event.set()
        if worker_task is not None:
            try:
                await asyncio.wait_for(worker_task, timeout=5)
            except TimeoutError:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)


app = FastAPI(title="Fitness Agent API", version="0.5.46", lifespan=lifespan)


@app.exception_handler(AIServiceError)
async def ai_service_error_handler(
    request: Request, exc: AIServiceError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})

app.include_router(auth.router, prefix="/api/v1")
app.include_router(profile.router, prefix="/api/v1")
app.include_router(exercises.router, prefix="/api/v1")
app.include_router(foods.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(workouts.router, prefix="/api/v1")
app.include_router(meals.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
app.include_router(proposals.router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/__tcb_probe__", include_in_schema=False)
async def cloudbase_probe():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(
                db.execute(text(
                    "SELECT r.idempotency_key, r.lease_expires_at, r.queue_position, "
                    "r.attempt_count, r.resolved_query, r.references, "
                    "c.pending_clarification, p.origin, "
                    "p.creation_client_request_id, p.target_kind, "
                    "p.target_id, p.result_data, f.source_name, "
                    "f.source_reference, f.browse_category, f.browse_aliases, "
                    "f.source_info, fa.normalized_alias "
                    "FROM agent_runs AS r "
                    "CROSS JOIN agent_conversations AS c "
                    "CROSS JOIN agent_proposals AS p "
                    "CROSS JOIN foods AS f "
                    "CROSS JOIN food_aliases AS fa LIMIT 0"
                )),
                timeout=3,
            )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable"},
        )
    return {
        "status": "ready",
        "agent_worker_enabled": settings.AGENT_ASYNC_WORKER_ENABLED,
    }


@app.get("/api/v1/me")
async def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email}
