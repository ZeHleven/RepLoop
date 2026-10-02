"""One ordering predicate for queued runs, task snapshots and conversation history."""
from sqlalchemy import and_, or_, literal, BigInteger

def precedes(candidate, current):
    # current may be a mapped instance or a SQL alias in the worker subquery.
    position = current.queue_position
    if position is None or isinstance(position, int):
        position = literal(position, type_=BigInteger())
    return or_(
        and_(candidate.queue_position.is_not(None), position.is_not(None), candidate.queue_position < position),
        and_(or_(candidate.queue_position.is_(None), position.is_(None)),
             or_(candidate.queued_at < current.queued_at,
                 and_(candidate.queued_at == current.queued_at, candidate.id < current.id))),
    )
