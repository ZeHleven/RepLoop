"""Read isolated SQL state without model credentials or business writes."""
import asyncio
from collections import Counter
import json
import os
from pathlib import Path

assert os.environ['DATABASE_URL'].split('@', 1)[-1] in {
    '127.0.0.1:5432/inference_live_tasks_test'}
assert not os.environ.get('DEEPSEEK_API_KEY') and not os.environ.get('EMBEDDING_API_KEY')


async def main():
    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal, engine
    from app.models.agent import AgentRun, AgentProposal, AgentToolCall, AgentMessage
    from app.models.meal import MealLog
    from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession
    from app.models.model_budget import ModelBudget, ModelCharge

    def fields(item):
        return {col.name: getattr(item, col.name) for col in item.__table__.columns}

    async with AsyncSessionLocal() as db:
        runs = (await db.scalars(select(AgentRun).order_by(AgentRun.queued_at))).all()
        charges = (await db.scalars(select(ModelCharge).order_by(ModelCharge.created_at))).all()
        budgets = (await db.scalars(select(ModelBudget))).all()
        failed = [r for r in runs if r.status != 'completed']
        ids = [r.id for r in failed]
        report = {'run_statuses': dict(Counter(r.status for r in runs)),
            'pending_runs': sum(r.status in {'queued', 'running', 'processing'} for r in runs),
            'meal_rows': await db.scalar(select(func.count(MealLog.id))),
            'proposal_rows': await db.scalar(select(func.count(AgentProposal.id))),
            'plans': await db.scalar(select(func.count(WorkoutPlan.id))),
            'sessions': await db.scalar(select(func.count(WorkoutSession.id))),
            'charges': [fields(c) for c in charges],
            'budgets': {'spent_nano': sum(b.spent_nano for b in budgets),
                'reserved_nano': sum(b.reserved_nano for b in budgets), 'blocked_users': sum(b.blocked for b in budgets)},
            'failed_runs': [fields(r) for r in failed],
            'failed_run_tools': [fields(t) for t in (await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id.in_(ids)))).all()] if ids else [],
            'failed_run_messages': [fields(m) for m in (await db.scalars(select(AgentMessage).where(AgentMessage.run_id.in_(ids)))).all()] if ids else []}
        report['session_mapping'] = [dict(row._mapping) for row in (await db.execute(select(
            WorkoutSession.user_id, WorkoutSession.plan_id, WorkoutSession.plan_family_id,
            WorkoutSession.week_start, WorkoutSession.day_of_week, WorkoutSession.trained_at,
            WorkoutSession.status, WorkoutSession.duration_min))).all()]
        report['planned_rest'] = [dict(row._mapping) for row in (await db.execute(select(
            WorkoutPlan.user_id, PlannedExercise.exercise_id, PlannedExercise.rest_seconds,
            PlannedExercise.order_index).join(PlannedExercise, PlannedExercise.plan_id == WorkoutPlan.id))).all()]
    await engine.dispose()
    Path('/out/sql-postcheck.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['run_statuses', 'pending_runs', 'meal_rows', 'proposal_rows', 'plans', 'sessions', 'budgets']}))


asyncio.run(main())
