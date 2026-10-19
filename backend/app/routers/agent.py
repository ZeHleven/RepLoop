from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models.agent import AgentConversation, AgentMessage, AgentRun
from app.models.user import User
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentArtifactReference,
    AgentCard,
    AgentConversationResponse,
    AgentMessageResponse,
    AgentProposalReference,
    AgentRunCreateRequest,
    AgentRunCreateResponse,
    AgentRunResponse,
)
from app.schemas.agent_plan_adjustment_proposal_api import (
    PlanAdjustmentProposalDecisionRequest,
    PlanAdjustmentProposalDecisionResponse,
    PlanAdjustmentProposalReadResponse,
)
from app.services.agent_jobs import AgentIdempotencyConflict, enqueue_agent_run
from app.services.agent_plan_adjustment_proposal_decisions import (
    PlanAdjustmentProposalDecisionServiceResult,
    decide_plan_adjustment_proposal,
    proposal_business_error_http_status,
    proposal_business_error_result,
    read_owned_plan_adjustment_proposal,
)
from app.services.agent_plan_adjustment_proposal_execution import (
    apply_confirmed_plan_adjustment_atomically,
)
from app.services.agent_runtime import run_agent_chat
from app.services.agent_run_order import message_order_desc


router = APIRouter(prefix="/agent", tags=["agent"])


RUN_ERROR_MESSAGES = {
    "ai_service_error": "训练搭子暂时无法连接 AI 服务，请稍后重试。",
    "agent_runtime_error": "训练搭子暂时无法完成请求，请稍后重试。",
    "worker_runtime_error": "后台处理暂时失败，请稍后重新发送。",
    "worker_attempts_exhausted": "后台处理多次中断，请重新发送。",
    "invalid_queued_run": "请求状态不完整，请重新发送。",
    "legacy_run_interrupted": "旧版同步请求已中断，请重新发送。",
    "migration_superseded_run": "旧版重复请求已结束，请重新发送。",
}


def _proposal_error_response(
    result: PlanAdjustmentProposalDecisionServiceResult,
) -> JSONResponse:
    if result.error is None:
        raise ValueError("proposal error response requires an error")
    return JSONResponse(
        status_code=proposal_business_error_http_status(result.error.code),
        content=result.error.model_dump(mode="json"),
    )


async def _finish_proposal_decision(
    db: AsyncSession,
    result: PlanAdjustmentProposalDecisionServiceResult,
) -> PlanAdjustmentProposalDecisionResponse | JSONResponse:
    if result.state_changed:
        await db.commit()
    if result.error is not None:
        return _proposal_error_response(result)
    if result.response is None:
        raise ValueError("proposal decision completed without a response")
    return result.response


async def _owned_conversation(
    db: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
) -> AgentConversation | None:
    return await db.scalar(
        select(AgentConversation).where(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
    )


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(
    body: AgentChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.conversation_id:
        conversation = await _owned_conversation(
            db,
            conversation_id=body.conversation_id,
            user_id=current_user.id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Agent 会话不存在")
    else:
        conversation = AgentConversation(
            user_id=current_user.id,
            title=body.message[:50],
        )
        db.add(conversation)
        await db.flush()
        await db.commit()

    result = await run_agent_chat(
        db,
        user_id=current_user.id,
        conversation=conversation,
        user_message=body.message,
        artifact_action=(
            body.artifact_action.model_dump(mode="json")
            if body.artifact_action is not None
            else None
        ),
        clarification_action=(
            body.clarification_action.model_dump(mode="json")
            if body.clarification_action is not None
            else None
        ),
    )
    return AgentChatResponse(
        reply=result.reply,
        conversation_id=conversation.id,
        run_id=result.run_id,
        cards=result.cards,
        proposal=result.proposal,
        artifact=result.artifact,
    )


@router.post(
    "/runs",
    response_model=AgentRunCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_agent_run(
    body: AgentRunCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation: AgentConversation | None = None
    if body.conversation_id:
        conversation = await _owned_conversation(
            db,
            conversation_id=body.conversation_id,
            user_id=current_user.id,
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Agent 会话不存在")

    try:
        enqueued = await enqueue_agent_run(
            db,
            user_id=current_user.id,
            user_message=body.message,
            client_request_id=body.client_request_id,
            conversation=conversation,
            artifact_action=(
                body.artifact_action.model_dump(mode="json")
                if body.artifact_action is not None
                else None
            ),
            clarification_action=(
                body.clarification_action.model_dump(mode="json")
                if body.clarification_action is not None
                else None
            ),
        )
    except AgentIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentRunCreateResponse(
        run_id=enqueued.run.id,
        conversation_id=enqueued.conversation.id,
        status=enqueued.run.status,
        poll_after_ms=(
            0 if enqueued.run.status in {"completed", "failed"} else 800
        ),
    )


@router.get(
    "/conversations",
    response_model=list[AgentConversationResponse],
)
async def list_agent_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return list((await db.execute(
        select(AgentConversation)
        .where(AgentConversation.user_id == current_user.id)
        .order_by(AgentConversation.updated_at.desc())
        .limit(limit)
    )).scalars().all())


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[AgentMessageResponse],
)
async def get_agent_messages(
    conversation_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation = await _owned_conversation(
        db,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Agent 会话不存在")
    rows = list((await db.execute(
        select(AgentMessage)
        .outerjoin(AgentRun, and_(
            AgentMessage.run_id == AgentRun.id,
            AgentRun.conversation_id == conversation.id,
            AgentRun.user_id == current_user.id,
        ))
        .where(AgentMessage.conversation_id == conversation.id)
        .order_by(*message_order_desc())
        .limit(limit)
    )).scalars().all())
    rows.reverse()
    return rows


@router.get("/runs/{run_id}/attempts")
async def get_agent_attempts(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.agent_attempt_journal import read_attempt_history
    run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == current_user.id))
    if run is None:
        raise HTTPException(status_code=404, detail="Agent 运行记录不存在")
    return await read_attempt_history(db, run)


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    run = await db.scalar(
        select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.user_id == current_user.id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Agent 运行记录不存在")
    assistant_message = await db.scalar(
        select(AgentMessage).where(
            AgentMessage.run_id == run.id,
            AgentMessage.role == "assistant",
        )
    )
    cards: list[AgentCard] = []
    proposal: AgentProposalReference | None = None
    artifact: AgentArtifactReference | None = None
    reply: str | None = None
    if assistant_message is not None:
        reply = assistant_message.content
        raw_cards = assistant_message.content_data.get("cards", [])
        if isinstance(raw_cards, list):
            cards = [
                AgentCard.model_validate(item)
                for item in raw_cards
                if isinstance(item, dict)
            ]
        raw_proposal = assistant_message.content_data.get("proposal")
        if isinstance(raw_proposal, dict):
            try:
                proposal = AgentProposalReference.model_validate(raw_proposal)
            except ValueError:
                proposal = None
        raw_artifact = assistant_message.content_data.get("artifact")
        if isinstance(raw_artifact, dict):
            try:
                artifact = AgentArtifactReference.model_validate(raw_artifact)
            except ValueError:
                artifact = None

    response = AgentRunResponse.model_validate(run)
    return response.model_copy(update={
        "reply": reply,
        "cards": cards,
        "proposal": proposal,
        "artifact": artifact,
        "error_message": RUN_ERROR_MESSAGES.get(run.error_code),
        "poll_after_ms": 800 if run.status in {"queued", "running"} else None,
    })


@router.get(
    "/proposals/{proposal_id}",
    response_model=PlanAdjustmentProposalReadResponse,
    response_model_exclude_unset=True,
)
async def get_plan_adjustment_proposal(
    proposal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    response = await read_owned_plan_adjustment_proposal(
        db,
        user_id=current_user.id,
        proposal_id=proposal_id,
        now=datetime.now(timezone.utc),
    )
    if response is None:
        return _proposal_error_response(
            proposal_business_error_result("proposal_not_found")
        )
    return response


@router.post(
    "/proposals/{proposal_id}/confirm",
    response_model=PlanAdjustmentProposalDecisionResponse,
)
async def confirm_plan_adjustment_proposal(
    proposal_id: str,
    body: PlanAdjustmentProposalDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await apply_confirmed_plan_adjustment_atomically(
        db,
        enabled=settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED,
        user_id=current_user.id,
        proposal_id=proposal_id,
        request=body,
        now=datetime.now(timezone.utc),
    )
    return await _finish_proposal_decision(db, result)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=PlanAdjustmentProposalDecisionResponse,
)
async def reject_plan_adjustment_proposal(
    proposal_id: str,
    body: PlanAdjustmentProposalDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await decide_plan_adjustment_proposal(
        db,
        enabled=settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED,
        user_id=current_user.id,
        proposal_id=proposal_id,
        action="reject",
        request=body,
        now=datetime.now(timezone.utc),
    )
    return await _finish_proposal_decision(db, result)
