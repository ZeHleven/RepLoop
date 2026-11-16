"""Frozen source, real TCP/API/worker/PostgreSQL. Observers live outside product code."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from agent_eval.capture import ProviderCapture, RUN_CONTEXT
from agent_eval.metrics import clock_interval, summarize
from agent_eval import source_manifest as shared_observer_manifest


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def source_manifest(source):
    return {str(p.relative_to(source)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((source / "backend").rglob("*")) if p.is_file()
            and p.suffix in {".py", ".json", ".txt", ".ini", ".sh"} and "__pycache__" not in p.parts}


async def main(args):
    assert args.out.is_dir() and not (args.out / "result.json").exists(), "new output directory required"
    expected_db = "inference_live_fixture_test" if args.mode == "fixture" else "inference_live_tasks_test"
    assert os.environ["DATABASE_URL"].split("@", 1)[-1] == f"127.0.0.1:5432/{expected_db}"
    assert os.environ["DATABASE_URL"].endswith("_test")
    sys.path.insert(0, str(args.source / "backend"))
    from sqlalchemy import func, select, text
    import httpx
    import uvicorn
    from app.config import settings
    from app.database import Base, engine, AsyncSessionLocal
    from app.main import app
    from app import models
    from app.models.agent import AgentConversation, AgentRun, AgentToolCall, AgentProposal, AgentMessage
    from app.models.exercise import Exercise
    from app.models.food import Food
    from app.models.meal import MealLog, MealItem
    from app.models.profile import UserProfile, WeightLog
    from app.models.user import User
    from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession
    from app.models.model_budget import ModelBudget, ModelCharge
    from app.services.auth import create_access_token
    from app.services import agent_jobs
    from app.services.business_clock import calendar_date
    from app.services.agent_tools import build_read_tools
    from app.services.model_budget import PRICING_VERSION

    settings.AGENT_ASYNC_WORKER_ENABLED = True
    settings.AGENT_SYMPTOM_SCOPE_ENABLED = True
    settings.AGENT_SYMPTOM_FACTS_MODEL_ENABLED = True
    for key in ("AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED", "AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED",
                "AGENT_NUTRITION_PROPOSALS_ENABLED", "AGENT_PROFILE_PROPOSALS_ENABLED", "AGENT_WEIGHT_PROPOSALS_ENABLED"):
        setattr(settings, key, True)
    settings.DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") if args.mode == "live" else "offline-no-provider"
    assert settings.DEEPSEEK_API_KEY
    contract = json.loads(args.tasks.read_text(encoding="utf-8"))
    before_source = source_manifest(args.source)
    freeze = {"source_commit": args.commit, "source_files": before_source,
              "observer_files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.iterdir()) if p.is_file() and p.suffix in {".py", ".json"}},
              "shared_observer_files": shared_observer_manifest(),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "tasks_sha256": hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
              "python": sys.version, "versions": {m: importlib.metadata.version(m) for m in
                  ("httpx", "fastapi", "sqlalchemy", "asyncpg", "langchain", "langchain-openai", "openai", "scipy")},
              "settings": {k: v for k, v in settings.model_dump().items() if k.startswith("AGENT_")},
              "pricing_version": PRICING_VERSION, "limits": {"tasks": len(contract["cases"]) * len(contract["cohorts"]), "runs": 10, "provider_attempts": args.max_requests, "estimated_cny": args.max_cny, "seconds": 1200},
              "scope": "synthetic local TCP with one production worker, real model; not phone rendering, human feedback, or production capacity"}
    write_json(args.out / "freeze.json", freeze)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        assert await db.scalar(select(func.count(AgentRun.id))) == 0, "database contains prior runs"
        pg_version = await db.scalar(text("select version()"))
    today = calendar_date(datetime.now(timezone.utc))
    assert str(today) == os.environ["ACCEPTANCE_BUSINESS_DATE"], "business_day_changed"
    week_start = today - timedelta(days=today.weekday())
    tag = uuid4().hex[:10]
    fixture = contract["fixture"]
    catalogue = {r["fields"]["name_zh"]: r["fields"] for r in json.loads((args.source / "backend/app/data/exercise_catalog_v1.json").read_text())}
    async with AsyncSessionLocal() as db:
        exercises = {}
        for i, name in enumerate(("卧推", "深蹲")):
            f = catalogue[name]
            ex = Exercise(id=f"exp-{tag}-ex{i}", name_zh=name, name_en=f["name_en"], category=f["category"],
                          difficulty=f["difficulty"], muscle_primary=f["muscle_primary"], equipment=f["equipment"],
                          movement_pattern=f["movement_pattern"], contraindications=f["contraindications"], is_active=True)
            db.add(ex)
            exercises[name] = ex
        foods = {}
        for i, (name, cat, kcal, protein, carbs, fat) in enumerate((("米饭", "grain", 130, 3, 28, 1), ("鸡胸肉", "protein", 165, 31, 0, 3.6))):
            f = Food(id=f"exp-{tag}-food{i}", name_zh=name, category=cat, calories_per_100g=kcal, protein_g=protein,
                     carbs_g=carbs, fat_g=fat, diet_tags=[], is_common_in_china=True, is_active=True)
            db.add(f)
            foods[name] = f
        await db.commit()

    tasks, user_tasks, credentials = [], {}, {}
    cases = contract["cases"] if args.mode in {"live", "fixture"} else [c for c in contract["cases"] if c["id"] in {"Q01", "Q02"}]
    for cohort in contract["cohorts"]:
        for case in cases:
            identity = f"{cohort['id']}-{case['id']}"
            uid = f"exp-{tag}-{identity}"
            async with AsyncSessionLocal() as db:
                db.add(User(id=uid, email=uid + "@example.invalid", password_hash="synthetic-no-login"))
                await db.flush()
                db.add(UserProfile(user_id=uid, age=30, gender="male", height_cm=170, weight_kg=65,
                                   primary_goal="保持体重", training_days_per_week=3, injuries=[], chronic_conditions=[], onboarding_completed=True))
                conv = AgentConversation(user_id=uid)
                plan = WorkoutPlan(user_id=uid, name=fixture["plan"]["name"], goal="保持体重", days_per_week=1, duration_weeks=4, is_active=not case.get("no_active_plan", False),
                                   created_at=datetime.now(timezone.utc)-timedelta(days=28))
                db.add_all([conv, plan])
                await db.flush()
                for index, item in enumerate(case.get("seed_history", [])):
                    # Synthetic recovery fixture: imported historical text, no
                    # fabricated successful query or server task identifier.
                    db.add(AgentMessage(conversation_id=conv.id, run_id=None,
                        role=item["role"], content=item["content"], content_data={},
                        created_at=datetime.now(timezone.utc)-timedelta(seconds=10-index)))
                for index, item in enumerate(fixture["plan"]["monday"]):
                    db.add(PlannedExercise(plan_id=plan.id, exercise_id=exercises[item["exercise"]].id, day_of_week=1,
                                          sets=item["sets"], reps=item["reps"], rest_seconds=case.get("planned_rest_seconds", {}).get(item["exercise"], 90),
                                          recommended_weight_kg=item["weight_kg"], order_index=index))
                for index, item in enumerate(case["workouts"]):
                    day = week_start if item["date"] == "current_week_start" else week_start-timedelta(days=7)
                    moment = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)+timedelta(hours=index)
                    db.add(WorkoutSession(user_id=uid, plan_id=plan.id, plan_name=plan.name, plan_family_id=plan.family_id,
                                          week_start=day if item["linked"] else None, day_of_week=1 if item["linked"] else None,
                                          trained_at=day, status=item["status"], duration_min=item["duration_min"],
                                          started_at=moment, completed_at=moment + timedelta(minutes=item["duration_min"]) if item["duration_min"] else None))
                for dinner in fixture["dinners"]:
                    meal = MealLog(user_id=uid, logged_at=today + timedelta(days=dinner["day_offset"]), meal_type="晚餐")
                    db.add(meal)
                    await db.flush()
                    for name, grams in (("米饭", dinner["rice_g"]), ("鸡胸肉", dinner["chicken_g"])):
                        food = foods[name]
                        db.add(MealItem(meal_id=meal.id, food_id=food.id, food_name=name, amount_g=grams,
                                        calories=food.calories_per_100g * grams / 100, protein_g=food.protein_g * grams / 100,
                                        carbs_g=food.carbs_g * grams / 100, fat_g=food.fat_g * grams / 100))
                await db.commit()
                task = {"id": identity, "case_id": case["id"], "category": case["category"], "cohort": cohort["id"],
                        "source_commit": args.commit, "environment": "local_tcp_single_worker", "model_mode": args.mode,
                        "data_source": "synthetic", "user_id": uid, "conversation_id": conv.id,
                        "acceptance": case["acceptance"], "sample_role": case.get("sample_role", "original_core"), "seeded_history": case.get("seed_history", []), "turns": [], "collection_complete": False, "active_ms": None}
            tasks.append(task)
            user_tasks[uid] = identity
            credentials[identity] = create_access_token(uid)

    async def business_snapshot(uid):
        async with AsyncSessionLocal() as db:
            planned = (await db.execute(select(Exercise.name_zh, PlannedExercise.sets, PlannedExercise.reps, PlannedExercise.recommended_weight_kg, PlannedExercise.rest_seconds)
                .join(PlannedExercise, PlannedExercise.exercise_id == Exercise.id).join(WorkoutPlan, WorkoutPlan.id == PlannedExercise.plan_id)
                .where(WorkoutPlan.user_id == uid).order_by(PlannedExercise.order_index))).all()
            meals = (await db.execute(select(MealLog.logged_at, MealLog.meal_type, MealItem.food_name, MealItem.amount_g,
                                           MealItem.calories, MealItem.protein_g, MealItem.carbs_g, MealItem.fat_g)
                .join(MealItem, MealItem.meal_id == MealLog.id).where(MealLog.user_id == uid)
                .order_by(MealLog.logged_at, MealLog.meal_type, MealItem.food_name))).all()
            proposals = (await db.scalars(select(AgentProposal).where(AgentProposal.user_id == uid).order_by(AgentProposal.created_at))).all()
            plan_states = (await db.execute(select(WorkoutPlan.id, WorkoutPlan.name, WorkoutPlan.is_active)
                .where(WorkoutPlan.user_id == uid).order_by(WorkoutPlan.id))).all()
            sessions = (await db.execute(select(WorkoutSession.id, WorkoutSession.trained_at, WorkoutSession.status, WorkoutSession.duration_min)
                .where(WorkoutSession.user_id == uid).order_by(WorkoutSession.id))).all()
            def fields(item):
                return {column.name: getattr(item, column.name) for column in item.__table__.columns}
            full_rows = {}
            for model in (WorkoutPlan, WorkoutSession, UserProfile, WeightLog):
                rows = (await db.scalars(select(model).where(model.user_id == uid).order_by(model.id))).all()
                full_rows[model.__tablename__] = [fields(row) for row in rows]
            return {"plan_states": [list(r) for r in plan_states], "full_rows": json.loads(json.dumps(full_rows, default=str)),
                    "sessions": [[r[0], str(r[1]), r[2], r[3]] for r in sessions],
                    "plan": [list(r) for r in planned], "meals": [[str(r[0]), *r[1:]] for r in meals],
                    "proposals": [{"id": p.id, "status": p.status, "version": p.version, "payload": p.payload_data} for p in proposals]}

    if args.mode == "fixture":
        assert not os.environ.get("DEEPSEEK_API_KEY") and not os.environ.get("EMBEDDING_API_KEY")
        snapshots = {task["case_id"]: await business_snapshot(task["user_id"]) for task in tasks}
        assert set(snapshots) == {f"I{i:02d}" for i in range(1, 9)}
        observations = {}
        for case_id, snapshot in snapshots.items():
            assert [row[4] for row in snapshot["plan"]] == ([45, 150] if case_id in {"I05", "I06", "I07"} else [90, 90])
            assert snapshot["plan_states"][0][2] is (case_id != "I08")
            expected_sessions = 2 if case_id == "I01" else int(case_id in {"I02", "I06", "I07"})
            assert len(snapshot["sessions"]) == expected_sessions and not snapshot["meals"] and not snapshot["proposals"]
            assert snapshot["full_rows"]["user_profiles"][0]["experience_level"] is None
            assert not snapshot["full_rows"]["weight_logs"]
            task = next(row for row in tasks if row["case_id"] == case_id)
            async with AsyncSessionLocal() as db:
                tools = build_read_tools(db, user_id=task["user_id"], allowlist=["plan.get_active", "workout.get_next", "workout.list_history"])
                plan_observation = await tools[0].ainvoke({})
                next_observation = await tools[1].ainvoke({})
                history_observation = await tools[2].ainvoke({"start_date": str(week_start-timedelta(days=7)), "end_date": str(today), "limit": 20, "completed_only": True})
            if case_id == "I08":
                assert plan_observation == {"found": False}
                assert next_observation == {"found": False, "reason": "no_active_plan"}
            else:
                assert plan_observation["plan"]["weekly_completed_days"] == 0
                assert plan_observation["weekly_completion_scope"]["week_start"] == str(week_start)
                assert plan_observation["weekly_completion_scope"]["establishes_first_training"] is False
                assert next_observation["rest_seconds_semantics"]["individual_recovery_assessed"] is False
                assert [row["rest_seconds"] for row in next_observation["exercises"]] == [row[4] for row in snapshot["plan"]]
            assert history_observation["count"] == history_observation["total_count"] == expected_sessions
            assert not history_observation["truncated"]
            assert await business_snapshot(task["user_id"]) == snapshot
            observations[case_id] = {"plan": plan_observation, "next": next_observation, "history": history_observation}
        async with AsyncSessionLocal() as db:
            assert await db.scalar(select(func.count(AgentRun.id))) == 0
            assert await db.scalar(select(func.count(ModelCharge.id))) == 0
        write_json(args.out / "fixture-check.json", {"passed": True, "cases": snapshots, "tool_observations": observations, "http_calls": 0,
            "source_commit": args.commit, "business_date": today})
        await engine.dispose()
        print(json.dumps({"fixture_check": "passed", "cases": len(snapshots), "model_http": 0}), flush=True)
        return

    observer = ProviderCapture(args.out, mode=args.mode, max_cny=args.max_cny, max_requests=args.max_requests)
    observer.install()
    original_process = agent_jobs.process_agent_run
    worker_events = []
    async def observed_process(factory, run_id):
        async with factory() as db:
            run = await db.get(AgentRun, run_id)
            identity = user_tasks.get(run.user_id)
            if not identity:
                observer.fatal = "unattributed_worker_run"
                raise RuntimeError(observer.fatal)
        token = RUN_CONTEXT.set({"task_id": identity, "run_id": run_id, "worker_attempt": run.attempt_count})
        worker_events.append({"event": "start", "run_id": run_id, "task_id": identity, "mono": time.monotonic()})
        try:
            return await original_process(factory, run_id)
        finally:
            worker_events.append({"event": "finish", "run_id": run_id, "task_id": identity, "mono": time.monotonic()})
            RUN_CONTEXT.reset(token)
    agent_jobs.process_agent_run = observed_process
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18048, log_level="warning", access_log=False))
    serving = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        if serving.done():
            await serving
            raise RuntimeError("server_did_not_start")
        await asyncio.sleep(.1)
    assert server.started

    def save():
        write_json(args.out / "result.json", {"source_commit": args.commit, "business_date": today,
                   "postgres_version": pg_version, "mode": args.mode, "tasks": tasks, "provider_calls": observer.calls,
                   "worker_events": worker_events, "stopped": observer.fatal, "settings": freeze["settings"]})

    async def run_task(task):
        if observer.fatal:
            return
        case = next(c for c in cases if c["id"] == task["case_id"])
        task["before"] = await business_snapshot(task["user_id"])
        active = []
        async with httpx.AsyncClient(base_url="http://127.0.0.1:18048", trust_env=False, timeout=20,
                                    headers={"Authorization": "Bearer " + credentials[task["id"]]}) as client:
            for message in case["turns"]:
                if observer.fatal:
                    break
                assert calendar_date(datetime.now(timezone.utc)) == today, "business_day_changed"
                start, start_wall = time.monotonic(), time.time()
                entry = {"message": message}
                task["turns"].append(entry)
                try:
                    response = await client.post("/api/v1/agent/runs", json={"conversation_id": task["conversation_id"],
                        "message": message, "client_request_id": uuid4().hex})
                    entry.update(submit_status=response.status_code, submit_response=response.json())
                    body = response.json()
                    if response.status_code != 202:
                        entry["request_error"] = True
                        observer.fatal = "client_submit_http_error"
                        break
                    run_id = body["run_id"]
                    entry["run_id"] = run_id
                    while body.get("status") not in {"completed", "failed"}:
                        if time.monotonic() - start > 180:
                            observer.fatal = "turn_timeout"
                            entry["timeout"] = True
                            break
                        await asyncio.sleep(max(.5, min((body.get("poll_after_ms") or 800) / 1000, 2)))
                        polled = await client.get("/api/v1/agent/runs/" + run_id)
                        polled.raise_for_status()
                        body = polled.json()
                    finish = time.monotonic()
                    entry["visible_timing"] = clock_interval(start, finish, wall_delta_ms=(time.time()-start_wall)*1000)
                    entry["response"] = body
                    active.append(entry["visible_timing"]["ms"])
                    for target, left, right in (("queue_ms", "queued_at", "processing_started_at"), ("processing_ms", "processing_started_at", "completed_at")):
                        value = (datetime.fromisoformat(body[right]) - datetime.fromisoformat(body[left])).total_seconds()*1000 if body.get(left) and body.get(right) else None
                        entry[target] = round(value, 3) if value is not None and value >= 0 and entry["visible_timing"]["ms"] is not None else None
                    async with AsyncSessionLocal() as db:
                        audits = (await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run_id))).all()
                        entry["tool_audits"] = [{"call_id": a.call_id, "tool": a.tool_name, "arguments": a.arguments_data,
                            "result": a.result_data, "status": a.status, "duration_ms": a.duration_ms, "error_code": a.error_code} for a in audits]
                    entry["business_after"] = await business_snapshot(task["user_id"])
                except Exception as exc:
                    entry["client_error_type"] = type(exc).__name__
                    observer.fatal = observer.fatal or "client_collection_error"
                    break
                save()
            if case.get("confirm") and not observer.fatal:
                last = task["turns"][-1].get("response", {}) if task["turns"] else {}
                proposal = last.get("proposal")
                if proposal:
                    start = time.monotonic()
                    confirm = await client.post(f"/api/v1/proposals/{proposal['id']}/confirm", json={
                        "expected_version": proposal["version"], "client_request_id": uuid4().hex})
                    duration = (time.monotonic() - start)*1000
                    active.append(duration)
                    task["confirmation"] = {"status": confirm.status_code, "response": confirm.json(), "elapsed_ms": duration}
                else:
                    task["confirmation"] = {"not_attempted": "no_current_proposal"}
        task["after"] = await business_snapshot(task["user_id"])
        task["collection_complete"] = len(task["turns"]) == len(case["turns"]) and all(t.get("response", {}).get("status") in {"completed", "failed"} for t in task["turns"])
        task["active_ms"] = round(sum(active), 3) if task["collection_complete"] and all(v is not None for v in active) else None
        print(task["id"], "runs", len(task["turns"]), "visible_ms", task["active_ms"], "provider_calls", len(observer.calls), flush=True)
        save()

    try:
        for cohort in contract["cohorts"]:
            rows = [t for t in tasks if t["cohort"] == cohort["id"]]
            n = cohort["client_concurrency"]
            for index in range(0, len(rows), n):
                if observer.fatal:
                    break
                await asyncio.gather(*(run_task(t) for t in rows[index:index+n]))
    finally:
        server.should_exit = True
        await serving
        save()
        async with AsyncSessionLocal() as db:
            charges = (await db.scalars(select(ModelCharge))).all()
            pending = await db.scalar(select(func.count(AgentRun.id)).where(AgentRun.status.in_(("queued", "running", "processing"))))
            ledger = {"pending_runs": pending, "charges": [{"task_id": user_tasks.get(c.user_id), "status": c.status,
                "reserved_nano": c.reserved_nano, "actual_nano": c.actual_nano, "prompt_tokens": c.prompt_tokens,
                "completion_tokens": c.completion_tokens} for c in charges]}
        write_json(args.out / "ledger.json", ledger)
        after_source = source_manifest(args.source)
        assert before_source == after_source, "product_source_changed"
        write_json(args.out / "source-verification.json", {"before_after_equal": True, "files": len(before_source)})
        write_json(args.out / "metrics-unreviewed.json", summarize(tasks, observer.calls))
        await engine.dispose()
    print(json.dumps({"tasks": len(tasks), "calls": len(observer.calls), "known_cost_cny": observer.spent_nano/1e9,
                      "reserved_cny": observer.reserved_nano/1e9, "stopped": observer.fatal, "pending_runs": pending}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--tasks", type=Path, required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--mode", choices=("offline", "fixture", "live"), required=True)
    p.add_argument("--max-cny", type=float, default=5)
    p.add_argument("--max-requests", type=int, default=160)
    asyncio.run(main(p.parse_args()))
