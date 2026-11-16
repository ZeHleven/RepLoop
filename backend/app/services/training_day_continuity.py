"""Identify uninterrupted weekday arrangements across confirmed plan revisions.

Plan family alone is too broad after a weekday is removed and reintroduced.
Archived plan rows and applied proposal links are retained evidence, so existing
records can be matched without a migration or rewriting training history.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentProposal
from app.models.workout import PlannedExercise, WorkoutPlan, WorkoutSession


@dataclass
class TrainingDayContinuity:
    families: dict[str, str]
    days: dict[str, set[int]]
    parents: dict[str, str]
    _cache: dict[tuple[str, int], str | None] = field(default_factory=dict)

    def _occurrence(self, plan_id: str | None, day: int) -> str | None:
        path: list[tuple[str, int]] = []
        visited: set[str] = set()
        occurrence = None
        while plan_id in self.families and plan_id not in visited:
            key = (plan_id, day)
            if key in self._cache:
                occurrence = self._cache[key]
                break
            visited.add(plan_id)
            path.append(key)
            if day not in self.days[plan_id]:
                break
            if plan_id == self.families[plan_id]:
                occurrence = plan_id
                break
            parent = self.parents.get(plan_id)
            if parent is None:
                # Incomplete legacy lineage must not unlock a completed day.
                break
            if day not in self.days[parent]:
                occurrence = plan_id
                break
            plan_id = parent
        for key in path:
            self._cache[key] = occurrence
        return occurrence

    def matches(self, plan: WorkoutPlan, session: WorkoutSession) -> bool:
        day = session.day_of_week
        if (session.user_id != plan.user_id or session.plan_family_id != plan.family_id
                or day not in self.days.get(plan.id, set())):
            return False
        if (session.plan_id in self.days
                and day not in self.days[session.plan_id]):
            return False
        current = self._occurrence(plan.id, day)
        previous = self._occurrence(session.plan_id, day)
        # Preserve the pre-existing family/week guard if ancestry is incomplete.
        # Only proven, distinct occurrences may release a completed weekday.
        return current is None or previous is None or current == previous


async def load_training_day_continuity(
    db: AsyncSession, *, user_id: str, family_ids: set[str],
) -> TrainingDayContinuity:
    context = TrainingDayContinuity({}, {}, {})
    if not family_ids:
        return context
    rows = await db.execute(
        select(WorkoutPlan.id, WorkoutPlan.family_id, PlannedExercise.day_of_week)
        .outerjoin(PlannedExercise, PlannedExercise.plan_id == WorkoutPlan.id)
        .where(WorkoutPlan.user_id == user_id, WorkoutPlan.family_id.in_(family_ids))
    )
    # Include hidden/archived revisions: hiding a card is not deleting history.
    for plan_id, family_id, day in rows:
        context.families[plan_id] = family_id
        context.days.setdefault(plan_id, set())
        if day is not None:
            context.days[plan_id].add(day)
    if not context.families:
        return context
    links = await db.execute(
        select(AgentProposal.result_plan_id, AgentProposal.base_plan_id)
        .where(
            AgentProposal.user_id == user_id,
            AgentProposal.status == 'applied',
            AgentProposal.proposal_type.in_(['plan_adjustment_v1', 'plan_adjustment_v2']),
            AgentProposal.result_plan_id.in_(context.families),
        )
    )
    candidates: dict[str, set[str | None]] = {}
    for result_id, base_id in links:
        candidates.setdefault(result_id, set()).add(base_id)
    for result_id, bases in candidates.items():
        if len(bases) != 1:
            continue
        base_id = next(iter(bases))
        if (base_id in context.families and base_id != result_id
                and context.families[base_id] == context.families[result_id]):
            context.parents[result_id] = base_id
    return context
