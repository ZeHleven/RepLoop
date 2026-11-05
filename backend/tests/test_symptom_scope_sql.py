"""SCOPE-20261025-01 through real ASGI, worker and PostgreSQL; no model calls.

The intent layer is mocked; any HTTP request to the model provider fails the
test, proving the symptom answer is server-built.
"""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from evals.postgres_test_fixtures import db_session, engine, session_factory  # noqa: F401
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.agent import AgentMessage, AgentProposal, AgentRun
from app.models.exercise import Exercise
from app.models.workout import PlannedExercise, WorkoutPlan, WorkoutSession
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolution, IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run, process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context


def scripted_http(monkeypatch, sequence):
    """Offline provider: any model call beyond `sequence` fails the test."""
    import copy, importlib, json
    requests = []
    for module in ("httpx", "httpx2"):
        try:
            mod = importlib.import_module(module)
        except ImportError:
            continue
        original = mod.AsyncClient.send

        async def send(client, request, _mod=mod, _original=original, **kwargs):
            if request.url.host == "test":
                return await _original(client, request, **kwargs)
            assert request.url.host == "api.deepseek.com", "Offline fixture cannot call another host"
            requests.append(json.loads(request.content))
            assert len(requests) <= len(sequence), "Unbounded model retry"
            message = copy.deepcopy(sequence[len(requests) - 1])
            return _mod.Response(200, json={"id": "offline", "object": "chat.completion", "model": "deepseek-flash",
                                            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                                            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}},
                                 request=request)
        monkeypatch.setattr(mod.AsyncClient, "send", send)
    return requests


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield


async def seed(db, *, injuries=None):
    user, profile, conversation, _ = await _context(db, "scope-" + uuid4().hex)
    profile.injuries = injuries or []
    tag = uuid4().hex[:6]
    bench = Exercise(id="ex-b-" + tag, name_zh="卧推", name_en="Bench", category="力量", difficulty="中级",
                     muscle_primary=["chest"], contraindications=["肩关节", "腕肘部"])
    squat = Exercise(id="ex-s-" + tag, name_zh="深蹲", name_en="Squat", category="力量", difficulty="中级",
                     muscle_primary=["quads"], contraindications=["膝关节", "腰背部", "踝关节"])
    db.add_all([bench, squat])
    plan = WorkoutPlan(user_id=user.id, name="Synthetic scope", days_per_week=1)
    db.add(plan)
    await db.flush()
    db.add_all([
        PlannedExercise(plan_id=plan.id, exercise_id=bench.id, day_of_week=1, sets=3, reps="10",
                        rest_seconds=90, recommended_weight_kg=40, order_index=0),
        PlannedExercise(plan_id=plan.id, exercise_id=squat.id, day_of_week=1, sets=3, reps="8",
                        rest_seconds=120, recommended_weight_kg=60, order_index=1),
    ])
    await db.commit()
    return user, conversation


def resolution(message, *, risk="medium", kind="assessment", clarify=False):
    return IntentResolution(
        primary_intent="plan_query", intent_domain="workout_plan", request_kind=kind,
        requested_effect="read", risk_level=risk, confidence=0.9, resolved_query=message,
        evidence_requirements=["active_plan", "health_screening"],
        clarification_required=clarify,
        clarification_question="疼痛是怎么开始的？" if clarify else None,
        missing_slots=["起因"] if clarify else [],
    )


async def run_once(client, session_factory, conversation_id, message):
    res = await client.post("/api/v1/agent/runs", json={
        "conversation_id": conversation_id, "message": message, "client_request_id": uuid4().hex,
    })
    assert res.status_code == 202, res.text
    run_id = res.json()["run_id"]
    async with session_factory() as db:
        assert await claim_next_agent_run(db) == run_id
    await asyncio.wait_for(process_agent_run(session_factory, run_id), 15)
    body = (await client.get("/api/v1/agent/runs/" + run_id)).json()
    assert body["error_code"] is None, body
    return run_id, body


@pytest.fixture
def offline(monkeypatch, session_factory):
    async def override():
        async with session_factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    for name, value in [("DEEPSEEK_API_KEY", "synthetic-offline"), ("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                        ("AGENT_ENABLED", True), ("AGENT_PLANNED_EXECUTION_ENABLED", False),
                        ("AGENT_SYMPTOM_SCOPE_ENABLED", True)]:
        monkeypatch.setattr(settings, name, value)
    requests = scripted_http(monkeypatch, [])  # any model call fails
    yield requests
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("clarify", [False, True])
async def test_current_wrist_pain_is_server_scoped_without_model_or_writes(db_session, session_factory, monkeypatch, offline, clarify):
    user, conversation = await seed(db_session)
    message = "今天搬箱子后右手腕开始疼，只有撑地和用力握重物时疼。这周每次最多12分钟，按已存计划给具体取舍，只要建议，不保存。"
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", AsyncMock(return_value=IntentResolverOutcome(
        resolution=resolution(message, clarify=clarify), source="model", attempt_count=1)))
    sessions = await db_session.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id == user.id))
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        run_id, body = await run_once(client, session_factory, conversation.id, message)
    assert offline == []
    reply = body["reply"]
    assert "先停：卧推" in reply and "照常：深蹲" in reply
    assert "不要带着疼痛减重“试一下”" in reply and "没有修改你的计划" in reply
    assert body["execution_trace"]["termination_reason"] == "symptom_deterministic_scope"
    async with session_factory() as db:
        run = await db.get(AgentRun, run_id)
        assert run.status == "completed" and not run.clarification_required
        msg = (await db.scalars(select(AgentMessage).where(AgentMessage.run_id == run_id, AgentMessage.role == "assistant"))).one()
        scope = msg.content_data["symptom_scope"]
        assert scope["areas"] == ["腕肘部"] and scope["time_cap_minutes"] == 12
        assert [i["exercise"] for i in scope["days"]["1"] if not i["keep"]] == ["卧推"]
        assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id == user.id)) == 0
        assert await db.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id == user.id)) == sessions
        conv = await db.get(type(conversation), conversation.id)
        assert conv.pending_clarification == {}


@pytest.mark.asyncio
async def test_follow_up_inherits_site_from_previous_user_turn(db_session, session_factory, monkeypatch, offline):
    user, conversation = await seed(db_session)
    first = "今天右膝开始疼，我工作累，想要一周每次最多10分钟的临时训练建议，必要信息可以问。"
    second = "补充：今天下楼后开始，约2分，无肿胀麻木，走路不疼，下蹲才疼。仍是每次最多10分钟，不保存。"
    mock = AsyncMock(side_effect=[
        IntentResolverOutcome(resolution=resolution(first), source="model", attempt_count=1),
        IntentResolverOutcome(resolution=resolution(second), source="model", attempt_count=1),
    ])
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", mock)
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        await run_once(client, session_factory, conversation.id, first)
        _, body = await run_once(client, session_factory, conversation.id, second)
    assert offline == []
    assert "先停：深蹲" in body["reply"] and "照常：卧推" in body["reply"]


@pytest.mark.asyncio
async def test_follow_up_without_symptom_words_continues_scope(db_session, session_factory, monkeypatch, offline):
    user, conversation = await seed(db_session)
    first = "今天膝盖开始疼"
    second = "对了，我计划今天是练上肢的，那还能练吗"
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", AsyncMock(side_effect=[
        IntentResolverOutcome(resolution=resolution(first), source="model", attempt_count=1),
        IntentResolverOutcome(resolution=resolution(second, risk="low"), source="model", attempt_count=1),
    ]))
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        await run_once(client, session_factory, conversation.id, first)
        _, body = await run_once(client, session_factory, conversation.id, second)
    assert offline == []
    assert "先停：深蹲" in body["reply"] and "照常：卧推" in body["reply"]
    assert body["execution_trace"]["termination_reason"] == "symptom_deterministic_scope"


@pytest.mark.asyncio
async def test_swollen_ankle_prompts_care_and_pauses_squat(db_session, session_factory, monkeypatch, offline):
    user, conversation = await seed(db_session)
    message = "今天踩空崴了脚，右脚踝肿了，走路能踩地但明显疼。能不能把今晚深蹲减轻一点继续？"
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", AsyncMock(return_value=IntentResolverOutcome(
        resolution=resolution(message), source="model", attempt_count=1)))
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        _, body = await run_once(client, session_factory, conversation.id, message)
    assert offline == []
    assert body["reply"].startswith("你提到了") and "尽快（24 小时内）就医评估" in body["reply"]
    assert "先停：深蹲" in body["reply"]


@pytest.mark.asyncio
async def test_emergency_backstop_when_intent_layer_misses_high_risk(db_session, session_factory, monkeypatch, offline):
    user, conversation = await seed(db_session)
    message = "跑步后胸口压着疼，已经十分钟没缓解，还冒冷汗。我想先做完今天计划再说，可以吗？"
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", AsyncMock(return_value=IntentResolverOutcome(
        resolution=resolution(message, risk="low"), source="model", attempt_count=1)))
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        _, body = await run_once(client, session_factory, conversation.id, message)
    assert offline == []
    assert body["reply"] == runtime.HIGH_RISK_REPLY
    assert body["execution_trace"]["termination_reason"] == "health_red_flag_rules"


@pytest.mark.asyncio
async def test_high_risk_from_intent_layer_keeps_existing_emergency_path(db_session, session_factory, monkeypatch, offline):
    user, conversation = await seed(db_session)
    message = "今天膝盖开始疼，还有胸口压着疼和冷汗，今天还能练吗？"
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", AsyncMock(return_value=IntentResolverOutcome(
        resolution=resolution(message, risk="high"), source="model", attempt_count=1)))
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        _, body = await run_once(client, session_factory, conversation.id, message)
    assert body["reply"] == runtime.HIGH_RISK_REPLY
    assert body["execution_trace"]["termination_reason"] == "health_red_flag"


@pytest.mark.asyncio
async def test_flag_off_restores_legacy_model_path(db_session, session_factory, monkeypatch, offline):
    user, conversation = await seed(db_session)
    monkeypatch.setattr(settings, "AGENT_SYMPTOM_SCOPE_ENABLED", False)
    message = "今天右手腕开始疼，按已存计划给取舍，不保存。"
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", AsyncMock(return_value=IntentResolverOutcome(
        resolution=resolution(message), source="model", attempt_count=1)))
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        res = await client.post("/api/v1/agent/runs", json={
            "conversation_id": conversation.id, "message": message, "client_request_id": uuid4().hex})
        run_id = res.json()["run_id"]
        async with session_factory() as db:
            assert await claim_next_agent_run(db) == run_id
        await asyncio.wait_for(process_agent_run(session_factory, run_id), 15)
    # The offline provider refuses every call, so the legacy path fails after
    # reaching the model; the new path would have completed with zero calls.
    assert len(offline) >= 1
    async with session_factory() as db:
        assert (await db.get(AgentRun, run_id)).status == "failed"


# docs/agent/16: model-extracted facts through the real runtime.
from app.services.agent_symptom_facts import SymptomFactsOutput, validate_facts


def _facts(message, prior=(), **kw):
    base = dict(purpose="own_symptom_training", reports=[], care_signals=[], clinician_quote="",
                clinician_restricts_load=False, time_cap_minutes=0, emergency_signals=[],
                continues_previous=False, resolved_now=False)
    base.update(kw)
    return validate_facts(SymptomFactsOutput.model_validate(base), user_texts=[message, *prior])


async def _scoped_run(db_session, session_factory, monkeypatch, message, facts_value, *, conversation=None, user=None, kind="assessment"):
    if user is None:
        user, conversation = await seed(db_session)
    monkeypatch.setattr(settings, "AGENT_SYMPTOM_FACTS_MODEL_ENABLED", True)
    monkeypatch.setattr(runtime, "extract_symptom_facts", AsyncMock(return_value=facts_value))
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", AsyncMock(return_value=IntentResolverOutcome(
        resolution=resolution(message, kind=kind), source="rules", attempt_count=1)))
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        res = await client.post("/api/v1/agent/runs", json={
            "conversation_id": conversation.id, "message": message, "client_request_id": uuid4().hex})
        run_id = res.json()["run_id"]
        async with session_factory() as db:
            assert await claim_next_agent_run(db) == run_id
        await asyncio.wait_for(process_agent_run(session_factory, run_id), 15)
        body = (await client.get("/api/v1/agent/runs/" + run_id)).json()
    return user, conversation, run_id, body


@pytest.mark.asyncio
async def test_model_facts_route_dialect_site_without_rules(db_session, session_factory, monkeypatch, offline):
    message = "俺脚脖子今天迈步就疼，没崴着也没肿，单子上的推和蹲你给说说哪个该停"
    f = _facts(message, reports=[{"region": "ankle", "side": "none", "site_text": "脚脖子", "quote": "脚脖子今天迈步就疼"}])
    _, _, run_id, body = await _scoped_run(db_session, session_factory, monkeypatch, message, f)
    assert offline == []
    assert "先停：深蹲" in body["reply"] and "照常：卧推" in body["reply"]
    async with session_factory() as db:
        msg = (await db.scalars(select(AgentMessage).where(AgentMessage.run_id == run_id, AgentMessage.role == "assistant"))).one()
    detector = msg.content_data["symptom_scope"]["detector"]
    assert detector["source"] == "model" and detector["purpose"] == "own_symptom_training"


@pytest.mark.asyncio
async def test_model_says_third_party_so_rules_do_not_hijack(db_session, session_factory, monkeypatch, offline):
    message = "总听人说‘手腕疼就把卧推换成俯卧撑’，这说法有普遍道理吗？我没受伤。"
    _, _, run_id, body = await _scoped_run(db_session, session_factory, monkeypatch, message, _facts(message, purpose="general_knowledge"))
    assert len(offline) >= 1  # legacy model path was reached, not the symptom template
    async with session_factory() as db:
        assert (await db.get(AgentRun, run_id)).status == "failed"


@pytest.mark.asyncio
async def test_model_emergency_returns_emergency_template(db_session, session_factory, monkeypatch, offline):
    message = "练着练着上不来气，嘴唇都紫了"
    f = _facts(message, purpose="no_symptom", emergency_signals=[{"kind": "cyanosis", "quote": "嘴唇都紫了"}])
    monkeypatch.setattr(runtime, "emergency_signals", lambda m: [])  # isolate the model signal
    _, _, _, body = await _scoped_run(db_session, session_factory, monkeypatch, message, f)
    assert body["reply"] == runtime.HIGH_RISK_REPLY
    assert body["execution_trace"]["termination_reason"] == "health_red_flag_model"


@pytest.mark.asyncio
async def test_extraction_failure_falls_back_to_rules(db_session, session_factory, monkeypatch, offline):
    message = "今天右手腕开始疼，按已存计划给取舍，不保存。"
    _, _, run_id, body = await _scoped_run(db_session, session_factory, monkeypatch, message, None)
    assert offline == [] and "先停：卧推" in body["reply"]
    async with session_factory() as db:
        msg = (await db.scalars(select(AgentMessage).where(AgentMessage.run_id == run_id, AgentMessage.role == "assistant"))).one()
    assert msg.content_data["symptom_scope"]["detector"] == {"source": "rules"}


@pytest.mark.asyncio
async def test_model_continuation_carries_swelling_from_previous_scope(db_session, session_factory, monkeypatch, offline):
    first = "早上起来膝盖鼓了一圈，肿得裤腿都紧"
    f1 = _facts(first, reports=[{"region": "knee", "side": "none", "site_text": "膝盖", "quote": "膝盖鼓了一圈"}],
                care_signals=[{"kind": "swelling", "quote": "肿得裤腿都紧"}])
    user, conversation, _, body1 = await _scoped_run(db_session, session_factory, monkeypatch, first, f1)
    assert body1["reply"].startswith("你提到了")
    second = "腿那项算了，推的那项总能留下吧？"
    f2 = _facts(second, prior=[first], purpose="no_symptom", continues_previous=True)
    _, _, _, body2 = await _scoped_run(db_session, session_factory, monkeypatch, second, f2, conversation=conversation, user=user)
    assert offline == []
    assert body2["reply"].startswith("你提到了肿胀") and "照常" not in body2["reply"]


ELBOW = "搬凳子胳膊肘磕门框上了，眼下痛，没肿但有一块青紫。我把推的省掉，只完成蹲的三组可不可以？"


def _elbow_facts(message):
    return _facts(message, reports=[{"region": "wrist_elbow", "side": "none", "site_text": "胳膊肘", "quote": "眼下痛"}],
                  care_signals=[{"kind": "trauma", "quote": "胳膊肘磕门框上了"}, {"kind": "bruise", "quote": "有一块青紫"}])


@pytest.mark.asyncio
async def test_own_symptom_facts_outrank_a_mutation_label(db_session, session_factory, monkeypatch, offline):
    user, _, run_id, body = await _scoped_run(db_session, session_factory, monkeypatch, ELBOW, _elbow_facts(ELBOW), kind="mutation")
    assert offline == []
    assert body["execution_trace"]["termination_reason"] == "symptom_deterministic_scope"
    assert body["reply"].startswith("你提到了") and "照常" not in body["reply"]
    async with session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id == user.id)) == 0


@pytest.mark.asyncio
async def test_explicit_save_request_keeps_the_write_path(db_session, session_factory, monkeypatch, offline):
    message = "胳膊肘磕了眼下痛，帮我把今天只完成蹲的三组记下来"
    f = _facts(message, reports=[{"region": "wrist_elbow", "side": "none", "site_text": "胳膊肘", "quote": "眼下痛"}])
    _, _, _, body = await _scoped_run(db_session, session_factory, monkeypatch, message, f, kind="mutation")
    assert (body.get("execution_trace") or {}).get("termination_reason") != "symptom_deterministic_scope"


@pytest.mark.asyncio
async def test_rules_fallback_also_outranks_a_mutation_label(db_session, session_factory, monkeypatch, offline):
    _, _, _, body = await _scoped_run(db_session, session_factory, monkeypatch, ELBOW, None, kind="mutation")
    assert offline == [] and body["execution_trace"]["termination_reason"] == "symptom_deterministic_scope"


@pytest.mark.asyncio
async def test_plan_edit_command_with_symptom_keeps_the_write_path(db_session, session_factory, monkeypatch, offline):
    message = "右膝疼，帮我把深蹲换成腿举"
    f = _facts(message, reports=[{"region": "knee", "side": "right", "site_text": "右膝", "quote": "右膝疼"}])
    _, _, _, body = await _scoped_run(db_session, session_factory, monkeypatch, message, f, kind="mutation")
    assert (body.get("execution_trace") or {}).get("termination_reason") != "symptom_deterministic_scope"


@pytest.mark.asyncio
async def test_emergency_backstop_is_not_suppressed_by_a_mutation_label(db_session, session_factory, monkeypatch, offline):
    message = "胸口发紧还冒冷汗，帮我把今天的训练记成完成"
    _, _, _, body = await _scoped_run(db_session, session_factory, monkeypatch, message, None, kind="mutation")
    assert offline == [] and body["reply"] == runtime.HIGH_RISK_REPLY
    assert body["execution_trace"]["termination_reason"] == "health_red_flag_rules"


@pytest.mark.asyncio
async def test_model_facts_without_emergency_override_rule_hits(db_session, session_factory, monkeypatch, offline):
    message = "昨天练完胸，今儿两边胸肌按着酸，关节不痛，没有胸口压着或喘不过气。周一那份能帮我看下顺序么？"
    assert runtime.emergency_signals(message)  # the rule backstop alone would fire
    _, _, run_id, body = await _scoped_run(db_session, session_factory, monkeypatch, message, _facts(message, purpose="no_symptom"))
    assert (body.get("execution_trace") or {}).get("termination_reason") not in {"health_red_flag_rules", "health_red_flag_model"}
    assert len(offline) >= 1  # reached the legacy model path


@pytest.mark.asyncio
async def test_unverified_own_symptom_still_takes_the_deterministic_path(db_session, session_factory, monkeypatch, offline):
    message = "今儿穿套头衫，右肩只要往高处抬就刺刺的，放下不疼，外头看着也没鼓包，没磕着过。"
    f = _facts(message, reports=[{"region": "shoulder", "side": "right", "site_text": "右肩", "quote": "右肩往高处抬就刺"}])
    assert f.unverified
    _, _, _, body = await _scoped_run(db_session, session_factory, monkeypatch, message, f)
    assert offline == [] and body["execution_trace"]["termination_reason"] == "symptom_deterministic_scope"
    assert "照常" not in body["reply"]


@pytest.mark.asyncio
async def test_failed_facts_call_does_not_poison_the_turn_billing_scope(db_session, session_factory, monkeypatch, offline):
    """A budget/timeout failure inside the optional facts call stays in its own scope."""
    from app.services import model_budget
    seen = {}

    async def failing_extract(*args, **kwargs):
        scope = model_budget._scope.get()
        seen["scope"] = scope
        scope.failure = model_budget.ModelBudgetError(model_budget.UNCERTAIN, "model_budget_unavailable")
        return None

    monkeypatch.setattr(settings, "AGENT_SYMPTOM_FACTS_MODEL_ENABLED", True)
    monkeypatch.setattr(runtime, "extract_symptom_facts", failing_extract)
    turn_scopes = []
    original = runtime.resolve_intent_with_fallback

    message = "今天右手腕开始疼，按已存计划给取舍，不保存。"

    async def intent(*args, **kwargs):
        turn_scopes.append(model_budget._scope.get())
        return IntentResolverOutcome(resolution=resolution(message), source="model", attempt_count=1)

    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", intent)
    user, conversation = await seed(db_session)
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        run_id, body = await run_once(client, session_factory, conversation.id, message)
    assert seen["scope"] is not None and seen["scope"].failure is not None
    assert turn_scopes and turn_scopes[0] is not seen["scope"] and turn_scopes[0].failure is None
    assert body["execution_trace"]["termination_reason"] == "symptom_deterministic_scope"

