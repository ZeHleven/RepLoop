"""One business day per Agent request; wall-clock expiry is deliberately separate."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
BUSINESS_DATE: ContextVar[date | None] = ContextVar("agent_business_date", default=None)


def calendar_date(moment: datetime) -> date:
    # SQLite round-trips timezone-aware UTC columns as naive values.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(SHANGHAI).date()


def business_today() -> date:
    return BUSINESS_DATE.get() or calendar_date(datetime.now(timezone.utc))


def wall_today() -> date:
    """Fresh day for write-time eligibility; never freeze expiry to an old Run."""
    return calendar_date(datetime.now(timezone.utc))


def day_end(day: date) -> datetime:
    """Exclusive upper bound of a Shanghai calendar day, including DST-free UTC conversion."""
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=SHANGHAI)


@contextmanager
def request_day(queued_at: datetime | None):
    # An explicit enclosing date is used by deterministic replay. In production
    # every worker starts with an empty ContextVar and uses durable queued_at.
    day = BUSINESS_DATE.get() or calendar_date(queued_at or datetime.now(timezone.utc))
    token = BUSINESS_DATE.set(day)
    try:
        yield day
    finally:
        BUSINESS_DATE.reset(token)
