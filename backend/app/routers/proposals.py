from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models.agent import AgentProposal
from app.models.user import User
from app.schemas.agent_plan_adjustment_proposal_api import (
    PlanAdjustmentProposalDecisionRequest,
)
from app.schemas.plan_management_proposal import GenericProposalDecisionRequest
from app.services.agent_plan_adjustment_proposal_decisions import (
    PlanAdjustmentProposalDecisionServiceResult,
    decide_plan_adjustment_proposal,
    proposal_business_error_http_status,
    read_owned_plan_adjustment_proposal,
)
from app.services.agent_plan_adjustment_proposal_execution import (
    apply_confirmed_plan_adjustment_atomically,
)
from app.services.plan_management_proposals import (
    PLAN_MANAGEMENT_TYPES,
    PlanProposalError,
    decide_manual_plan_proposal,
    read_owned_manual_proposal,
)
from app.services.agent_domain_proposals import (
    DOMAIN_PROPOSAL_TYPES,
    decide_agent_domain_proposal,
    read_owned_domain_proposal,
)


router = APIRouter(prefix="/proposals", tags=["proposals"])


def _manual_error(exc: PlanProposalError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message},
    )


async def _finish_v1(
    db: AsyncSession,
    result: PlanAdjustmentProposalDecisionServiceResult,
):
    if result.state_changed:
        await db.commit()
    if result.error is not None:
        return JSONResponse(
            status_code=proposal_business_error_http_status(result.error.code),
            content=result.error.model_dump(mode="json"),
        )
    return result.response


async def _owned_type(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
) -> str | None:
    return await db.scalar(select(AgentProposal.proposal_type).where(
        AgentProposal.id == proposal_id,
        AgentProposal.user_id == user_id,
    ))


@router.get("/{proposal_id}", response_model=None)
async def get_proposal(
    proposal_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    proposal_type = await _owned_type(
        db, user_id=current_user.id, proposal_id=proposal_id
    )
    if proposal_type == "plan_adjustment_v1":
        response = await read_owned_plan_adjustment_proposal(
            db,
            user_id=current_user.id,
            proposal_id=proposal_id,
            now=datetime.now(timezone.utc),
        )
        return response or JSONResponse(
            status_code=404,
            content={"code": "proposal_not_found", "message": "提案不存在"},
        )
    if proposal_type in PLAN_MANAGEMENT_TYPES:
        try:
            response = await read_owned_manual_proposal(
                db,
                user_id=current_user.id,
                proposal_id=proposal_id,
            )
            return response
        except PlanProposalError as exc:
            return _manual_error(exc)
    if proposal_type in DOMAIN_PROPOSAL_TYPES:
        try:
            response = await read_owned_domain_proposal(
                db,
                user_id=current_user.id,
                proposal_id=proposal_id,
            )
            return response or JSONResponse(
                status_code=404,
                content={"code": "proposal_not_found", "message": "提案不存在"},
            )
        except PlanProposalError as exc:
            return _manual_error(exc)
    return JSONResponse(
        status_code=404,
        content={"code": "proposal_not_found", "message": "提案不存在"},
    )


async def _decide(
    *,
    proposal_id: str,
    action: str,
    body: GenericProposalDecisionRequest,
    current_user: User,
    db: AsyncSession,
):
    proposal_type = await _owned_type(
        db, user_id=current_user.id, proposal_id=proposal_id
    )
    if proposal_type == "plan_adjustment_v1":
        request = PlanAdjustmentProposalDecisionRequest.model_validate(
            body.model_dump()
        )
        if action == "confirm":
            result = await apply_confirmed_plan_adjustment_atomically(
                db,
                enabled=settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED,
                user_id=current_user.id,
                proposal_id=proposal_id,
                request=request,
                now=datetime.now(timezone.utc),
            )
        else:
            result = await decide_plan_adjustment_proposal(
                db,
                enabled=settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED,
                user_id=current_user.id,
                proposal_id=proposal_id,
                action="reject",
                request=request,
                now=datetime.now(timezone.utc),
            )
        return await _finish_v1(db, result)
    if proposal_type in PLAN_MANAGEMENT_TYPES:
        try:
            return await decide_manual_plan_proposal(
                db,
                user_id=current_user.id,
                proposal_id=proposal_id,
                action="confirm" if action == "confirm" else "reject",
                request=body,
            )
        except PlanProposalError as exc:
            return _manual_error(exc)
    if proposal_type in DOMAIN_PROPOSAL_TYPES:
        try:
            return await decide_agent_domain_proposal(
                db,
                user_id=current_user.id,
                proposal_id=proposal_id,
                action="confirm" if action == "confirm" else "reject",
                request=body,
            )
        except PlanProposalError as exc:
            return _manual_error(exc)
    return JSONResponse(
        status_code=404,
        content={"code": "proposal_not_found", "message": "提案不存在"},
    )


@router.post("/{proposal_id}/confirm", response_model=None)
async def confirm_proposal(
    proposal_id: str,
    body: GenericProposalDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _decide(
        proposal_id=proposal_id,
        action="confirm",
        body=body,
        current_user=current_user,
        db=db,
    )


@router.post("/{proposal_id}/reject", response_model=None)
async def reject_proposal(
    proposal_id: str,
    body: GenericProposalDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _decide(
        proposal_id=proposal_id,
        action="reject",
        body=body,
        current_user=current_user,
        db=db,
    )
