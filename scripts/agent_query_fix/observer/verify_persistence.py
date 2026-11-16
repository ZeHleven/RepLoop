"""Post-run synthetic DB verification; no worker, model calls, or timing score."""
import asyncio
from datetime import date
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select

assert os.environ["DATABASE_URL"].split("@", 1)[-1] in {"fitness-experience-20261108-pg:5432/" + name for name in ("query_fix_core_test", "query_fix_extra_test", "query_fix_c2_core_test", "query_fix_c2_extra_test")}
from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.main import app
from app.models.agent import AgentProposal, AgentRun
from app.models.exercise import Exercise
from app.models.meal import MealLog, MealItem
from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession
from app.services.auth import create_access_token

settings.AGENT_ASYNC_WORKER_ENABLED = False
settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED = True


async def forbid_network(*args, **kwargs):
    raise AssertionError("external_HTTP_forbidden_in_post_run_verification")


httpx.AsyncHTTPTransport.handle_async_request = forbid_network


async def snapshot(uid):
    async with AsyncSessionLocal() as db:
        plans = (await db.scalars(select(WorkoutPlan).where(WorkoutPlan.user_id == uid).order_by(WorkoutPlan.id))).all()
        rows = []
        for plan in plans:
            exercises = (await db.execute(select(Exercise.name_zh, PlannedExercise.sets, PlannedExercise.reps,
                PlannedExercise.recommended_weight_kg).join(PlannedExercise, PlannedExercise.exercise_id == Exercise.id)
                .where(PlannedExercise.plan_id == plan.id).order_by(PlannedExercise.order_index))).all()
            rows.append({"id": plan.id, "active": plan.is_active, "exercises": [list(r) for r in exercises]})
        meals = (await db.scalars(select(MealLog).where(MealLog.user_id == uid).order_by(MealLog.logged_at))).all()
        meal_rows = []
        for meal in meals:
            items = (await db.execute(select(MealItem.food_name, MealItem.amount_g, MealItem.calories,
                MealItem.protein_g, MealItem.carbs_g, MealItem.fat_g).where(MealItem.meal_id == meal.id)
                .order_by(MealItem.food_name))).all()
            meal_rows.append({"id": meal.id, "date": str(meal.logged_at), "type": meal.meal_type, "items": [list(r) for r in items]})
        sessions = (await db.execute(select(WorkoutSession.trained_at, WorkoutSession.status, WorkoutSession.duration_min)
            .where(WorkoutSession.user_id == uid).order_by(WorkoutSession.trained_at))).all()
        return {"plans": rows, "meals": meal_rows, "workouts": [[str(r[0]), *r[1:]] for r in sessions]}


async def main():
    base = Path("/out")
    assert not (base / "persistence-verification.json").exists()
    raw = json.loads((base / "result.json").read_text())
    result = {"scope": "post-run synthetic DB reads and stale-proposal rejection via ASGI; no model or performance measurements", "tasks": {}}
    for task in raw["tasks"]:
        before = await snapshot(task["user_id"])
        item = {"snapshot": before}
        if task["case_id"] == "W01":
            active = [p for p in before["plans"] if p["active"]]
            assert len(active) == 1 and active[0]["exercises"] == [["卧推", 5, "8", 20.0], ["深蹲", 3, "10", 30.0]]
            old = task["turns"][0]["response"]["proposal"]
            async with AsyncSessionLocal() as db:
                proposal = await db.get(AgentProposal, old["id"])
                assert proposal.status == "stale"
                current_version = proposal.version
            item["stale_confirm_attempts"] = []
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated-test",
                headers={"Authorization": "Bearer " + create_access_token(task["user_id"])}) as client:
                for version in (old["version"], current_version):
                    response = await client.post(f"/api/v1/proposals/{old['id']}/confirm", json={
                        "expected_version": version, "client_request_id": uuid4().hex})
                    item["stale_confirm_attempts"].append({"version": version, "status": response.status_code, "body": response.json()})
                    assert response.status_code == 409
            item["unchanged_after_rejected_confirmations"] = before == await snapshot(task["user_id"])
            assert item["unchanged_after_rejected_confirmations"]
        if task["case_id"] == "W02":
            lunches = [m for m in before["meals"] if m["date"] == raw["business_date"] and m["type"] == "午餐"]
            assert len(lunches) == 1
            items = sorted(lunches[0]["items"], key=lambda r: r[0])
            assert items == sorted([["米饭", 150.0, 195.0, 4.5, 42.0, 1.5], ["鸡胸肉", 100.0, 165.0, 31.0, 0.0, 3.6]])
        result["tasks"][task["id"]] = item
    async with AsyncSessionLocal() as db:
        result["pending_runs"] = await db.scalar(select(func.count(AgentRun.id)).where(AgentRun.status.in_(("queued", "running"))))
    assert result["pending_runs"] == 0
    (base / "persistence-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    await engine.dispose()
    print(json.dumps({"verified_tasks": len(result["tasks"]), "stale_confirm_rejections": sum(len(t.get("stale_confirm_attempts", [])) for t in result["tasks"].values()), "external_HTTP": 0, "pending_runs": 0}))


asyncio.run(main())
