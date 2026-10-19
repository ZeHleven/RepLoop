"""One ordering contract for queued runs, task snapshots and history."""
from sqlalchemy import BigInteger, and_, case, func, literal, or_, select

from app.models.agent import AgentConversation, AgentMessage, AgentRun


def message_order_desc(run=AgentRun):
    """Latest bounded window, reversed by callers for causal display/history.

    Numbered runs survive wall-clock reversal. Legacy rows use their existing
    timestamp/id order; the role tie-break keeps an answer after its own input.
    """
    return (
        run.queue_position.desc().nulls_last(),
        func.coalesce(run.queued_at, AgentMessage.created_at).desc(),
        run.id.desc().nulls_last(),
        case((AgentMessage.role == 'assistant', 1), else_=0).desc(),
        AgentMessage.created_at.desc(),
        AgentMessage.id.desc(),
    )


async def allocate_queue_position(db, *, conversation_id: str, user_id: str) -> int:
    # Both async submissions and the synchronous compatibility endpoint use this
    # lock; their new rows therefore share one durable conversation sequence.
    await db.execute(select(AgentConversation.id).where(
        AgentConversation.id == conversation_id,
        AgentConversation.user_id == user_id,
    ).with_for_update())
    previous = await db.scalar(select(func.max(AgentRun.queue_position)).where(
        AgentRun.conversation_id == conversation_id,
        AgentRun.user_id == user_id,
    ))
    return (previous or 0) + 1


def precedes(candidate, current):
    # NULL rows belong to the legacy era. Comparing a NULL row by timestamp to
    # numbered rows could form a cycle when transaction timestamps go backwards.
    # Deployment must stop legacy writers before numbered requests are accepted.
    position = current.queue_position
    if position is None or isinstance(position, int):
        position = literal(position, type_=BigInteger())
    return or_(
        and_(candidate.queue_position.is_(None), position.is_not(None)),
        and_(candidate.queue_position.is_not(None), position.is_not(None),
             candidate.queue_position < position),
        and_(candidate.queue_position.is_(None), position.is_(None),
             or_(candidate.queued_at < current.queued_at,
                 and_(candidate.queued_at == current.queued_at, candidate.id < current.id))),
    )
