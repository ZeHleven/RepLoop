"""Real-model run of the frozen acceptance set v1 through ASGI + worker + PostgreSQL.

Each case gets a fresh synthetic user and conversation with the frozen
fixture (周一 卧推 3×10 20kg, 深蹲 3×10 30kg; sessions 10-07 and 10-08
completed; 10-08 dinner 米饭150g + 鸡胸100g). Business date is fixed at
2026-10-09 (Asia/Shanghai). Settings mirror the 2026-10-18 acceptance.

Modes:
  --dry-run   provider returns HTTP 503 for every call (plumbing check, ¥0)
  --live      real DeepSeek; needs DEEPSEEK_API_KEY in the environment

Guards: only api.deepseek.com, ≤120 provider requests, stop when the
conservative estimate exceeds ¥1.50. Never records request headers or keys.
Usage from backend/ with TEST_DATABASE_URL pointing at an isolated *_test DB.
"""
import argparse, asyncio, hashlib, importlib, json, os, pathlib, sys, time
from datetime import date, datetime, timezone
from uuid import uuid4

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))

PRICE_IN_MISS, PRICE_OUT = 2.0, 8.0  # CNY per million tokens, conservative peak price
MAX_REQUESTS, MAX_CNY = 120, 1.50
SETTINGS = {
    "AGENT_ENABLED": True, "AGENT_MODEL": "deepseek-v4-flash", "AGENT_INTENT_MODEL_ENABLED": True,
    "AGENT_INTENT_MODEL": "deepseek-v4-flash", "AGENT_RULES_FIRST_ENABLED": False,
    "AGENT_PLANNED_EXECUTION_ENABLED": True, "AGENT_ASYNC_WORKER_ENABLED": False,
    "AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED": True, "AGENT_NUTRITION_PROPOSALS_ENABLED": True,
    "AGENT_SYMPTOM_SCOPE_ENABLED": True, "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
}


class Provider:
    def __init__(self, live):
        self.live, self.requests, self.usage, self.fatal = live, 0, [], None

    def install(self):
        recorder = self
        for name in ("httpx", "httpx2"):
            try:
                mod = importlib.import_module(name)
            except ImportError:
                continue
            original = mod.AsyncClient.send

            async def send(client, request, _mod=mod, _original=original, **kwargs):
                if request.url.host == "test":
                    return await _original(client, request, **kwargs)
                if request.url.host != "api.deepseek.com":
                    raise RuntimeError("unexpected outbound host " + request.url.host)
                if recorder.fatal or recorder.requests >= MAX_REQUESTS or recorder.cost() > MAX_CNY:
                    recorder.fatal = recorder.fatal or "budget"
                    raise RuntimeError("evaluation budget reached")
                recorder.requests += 1
                if not recorder.live:
                    return _mod.Response(503, json={"error": {"message": "dry-run"}}, request=request)
                response = await _original(client, request, **kwargs)
                try:
                    body = json.loads(response.content)
                    u = body.get("usage") or {}
                    recorder.usage.append({"prompt": u.get("prompt_tokens", 0),
                                           "cache_hit": u.get("prompt_cache_hit_tokens", 0),
                                           "completion": u.get("completion_tokens", 0),
                                           "model": body.get("model")})
                except Exception:
                    pass
                return response

            mod.AsyncClient.send = send

    def cost(self):
        return sum(u["prompt"] * PRICE_IN_MISS + u["completion"] * PRICE_OUT for u in self.usage) / 1e6


async def main(live):
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings
    from app.database import Base, get_db
    from app.main import app
    from app.models.agent import AgentConversation, AgentMessage, AgentRun
    from app.models.exercise import Exercise
    from app.models.food import Food
    from app.models.meal import MealItem, MealLog
    from app.models.profile import UserProfile
    from app.models.user import User
    from app.models.workout import PlannedExercise, WorkoutPlan, WorkoutSession
    from app.services.agent_jobs import claim_next_agent_run, process_agent_run
    from app.services.auth import create_access_token
    from app.services.business_clock import BUSINESS_DATE

    db_url = os.environ["TEST_DATABASE_URL"]
    assert db_url.rsplit("/", 1)[-1].endswith("_test"), "use an isolated *_test database"
    for key, value in SETTINGS.items():
        setattr(settings, key, value)
    if live:
        assert os.environ.get("DEEPSEEK_API_KEY"), "DEEPSEEK_API_KEY missing"
        settings.DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
    else:
        settings.DEEPSEEK_API_KEY = "dry-run-no-key"

    raw = (HERE / "cases.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (HERE / "cases.sha256").read_text().split()[0]
    cases = json.loads(raw)["cases"]
    catalog = {r["fields"]["name_zh"]: r["fields"] for r in
               json.loads((ROOT / "backend/app/data/exercise_catalog_v1.json").read_text(encoding="utf-8"))}

    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override

    provider = Provider(live)
    provider.install()
    BUSINESS_DATE.set(date(2026, 10, 9))
    results, started = [], time.time()

    async with factory() as db:
        tag = uuid4().hex[:8]
        exercises = {}
        for name in ("卧推", "深蹲"):
            f = catalog[name]
            ex = Exercise(id=f"acc-{tag}-{name}", name_zh=name, name_en=f["name_en"], category=f["category"],
                          difficulty=f["difficulty"], muscle_primary=f["muscle_primary"],
                          equipment=f["equipment"], movement_pattern=f["movement_pattern"],
                          contraindications=f["contraindications"], is_active=True)
            db.add(ex)
            exercises[name] = ex
        foods = {n: Food(id=f"acc-{tag}-{n}", name_zh=n, category=c, calories_per_100g=k, protein_g=p,
                         carbs_g=cb, fat_g=fa, diet_tags=[], is_common_in_china=True, is_active=True)
                 for n, c, k, p, cb, fa in [("米饭", "grain", 130, 3, 28, 1), ("鸡胸", "protein", 165, 31, 0, 3.6)]}
        db.add_all(foods.values())
        await db.commit()

    for case in cases:
        async with factory() as db:
            uid = f"acc-{case['id']}-{uuid4().hex[:8]}"
            db.add(User(id=uid, email=uid + "@example.invalid", password_hash="synthetic-no-login"))
            await db.flush()
            db.add(UserProfile(user_id=uid, age=30, gender="male", height_cm=170, weight_kg=65,
                               primary_goal="保持体重", training_days_per_week=3, injuries=[],
                               chronic_conditions=[], onboarding_completed=True))
            conv = AgentConversation(user_id=uid)
            plan = WorkoutPlan(user_id=uid, name="第31轮合成计划", goal="保持体重", days_per_week=1, duration_weeks=4)
            db.add_all([conv, plan])
            await db.flush()
            for i, (name, weight) in enumerate([("卧推", 20), ("深蹲", 30)]):
                db.add(PlannedExercise(plan_id=plan.id, exercise_id=exercises[name].id, day_of_week=1, sets=3,
                                       reps="10", rest_seconds=90, recommended_weight_kg=weight, order_index=i))
            for day in ("2026-10-07", "2026-10-08"):
                d = date.fromisoformat(day)
                db.add(WorkoutSession(user_id=uid, plan_id=plan.id, plan_name=plan.name, trained_at=d,
                                      status="completed", started_at=datetime(d.year, d.month, d.day, 1, tzinfo=timezone.utc),
                                      completed_at=datetime(d.year, d.month, d.day, 2, tzinfo=timezone.utc)))
            meal = MealLog(id=str(uuid4()), user_id=uid, logged_at=date(2026, 10, 8), meal_type="晚餐")
            db.add(meal)
            await db.flush()
            for n, g in (("米饭", 150), ("鸡胸", 100)):
                f = foods[n]
                db.add(MealItem(id=str(uuid4()), meal_id=meal.id, food_id=f.id, food_name=n, amount_g=g,
                                calories=f.calories_per_100g * g / 100, protein_g=f.protein_g * g / 100,
                                carbs_g=f.carbs_g * g / 100, fat_g=f.fat_g * g / 100))
            await db.commit()
            conv_id = conv.id

        turns = []
        headers = {"Authorization": "Bearer " + create_access_token(uid)}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
            for message in case["turns"]:
                before_req, before_usage = provider.requests, len(provider.usage)
                t0 = time.time()
                res = await client.post("/api/v1/agent/runs", json={
                    "conversation_id": conv_id, "message": message, "client_request_id": uuid4().hex})
                entry = {"message": message, "submit_status": res.status_code}
                if res.status_code == 202:
                    run_id = res.json()["run_id"]
                    async with factory() as db:
                        claimed = await claim_next_agent_run(db)
                    try:
                        await asyncio.wait_for(process_agent_run(factory, run_id), 180)
                    except Exception as exc:  # recorded, never retried
                        entry["worker_error"] = type(exc).__name__ + ": " + str(exc)[:200]
                    body = (await client.get("/api/v1/agent/runs/" + run_id)).json()
                    async with factory() as db:
                        run = await db.get(AgentRun, run_id)
                        msg = (await db.scalars(select(AgentMessage).where(
                            AgentMessage.run_id == run_id, AgentMessage.role == "assistant"))).first()
                    trace = body.get("execution_trace") or {}
                    entry.update({
                        "run_id": run_id, "claimed": claimed == run_id, "status": body.get("status"),
                        "error_code": body.get("error_code"), "reply": body.get("reply"),
                        "termination_reason": trace.get("termination_reason"),
                        "terminal_action": trace.get("terminal_action"),
                        "intent": {"request_kind": run.request_kind, "risk_level": run.risk_level,
                                   "primary_intent": run.primary_intent, "intent_source": run.intent_source,
                                   "clarification_required": run.clarification_required},
                        "guardrail": (msg.content_data or {}).get("guardrail") if msg else None,
                        "provider_requests": provider.requests - before_req,
                        "usage": provider.usage[before_usage:],
                        "seconds": round(time.time() - t0, 1),
                    })
                turns.append(entry)
                if provider.fatal:
                    break
        last = turns[-1] if turns else {}
        path = ("symptom_deterministic_scope" if last.get("termination_reason") == "symptom_deterministic_scope"
                else "emergency_template" if last.get("termination_reason") in {"health_red_flag", "health_red_flag_rules"}
                else "legacy_model_path")
        results.append({"id": case["id"], "expected_path": case["path"], "path": path,
                        "path_ok": path == case["path"], "turns": turns})
        print(case["id"], path, "ok" if path == case["path"] else "MISS",
              f"req={provider.requests} est=¥{provider.cost():.4f}", flush=True)
        if provider.fatal:
            print("STOPPED:", provider.fatal)
            break

    out = {"mode": "live" if live else "dry-run", "cases_sha256": hashlib.sha256(raw).hexdigest(),
           "business_date": "2026-10-09", "settings": SETTINGS, "provider_requests": provider.requests,
           "estimated_cny": round(provider.cost(), 6), "price_basis": "input ¥2/M (all as cache miss), output ¥8/M",
           "stopped": provider.fatal, "seconds": round(time.time() - started, 1), "results": results}
    name = "live-run.json" if live else "dry-run.json"
    (HERE / name).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"path {sum(r['path_ok'] for r in results)}/{len(results)}; requests {provider.requests}; est ¥{provider.cost():.4f}; -> {name}")
    app.dependency_overrides.clear()
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true")
    group.add_argument("--dry-run", action="store_true")
    asyncio.run(main(parser.parse_args().live))
