"""R45 A49/A53 archived routes and synthetic-plan API/SQL regression.

Route arguments are original R43 values. Typed planner controls are synthetic;
they are not represented as archived model responses or benchmark holdouts.
"""
import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from evals.postgres_test_fixtures import engine, session_factory, db_session
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.agent import AgentProposal, AgentToolCall
from app.models.meal import MealLog, MealItem
from app.services import agent_intent_model, nutrition_history_reporting as reporting
from app.services.agent_intent import resolve_intent, route_tools
from app.services.agent_intent_model import IntentRouteDecision, _route_to_resolution, resolve_intent_with_fallback
from app.services.agent_task_state import is_calendar_query_revision, state_from_context, active_task, advance_task_state
from app.services.agent_query_reports import narrow_report_evidence
from app.services.nutrition_query_scope import is_nutrition_filter_revision, is_single_daily_history_goal, explicit_requested_selection
from app.services.agent_jobs import claim_next_agent_run, process_agent_run
from app.services.business_clock import BUSINESS_DATE
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context

FIXTURE = Path(__file__).parent / "fixtures/round45/archived-routes.json"
if not FIXTURE.exists():
    FIXTURE = Path(__file__).parent / "archived-routes.json"
ROWS = {row["instance"]: row for row in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]}
ALL = list(ROWS)


def route_rows(cid, turn=None):
    row = ROWS[cid]
    if turn is None:
        turn = row["turns"][-1]["index"]
    return [record for record in row["routes"] if record["turn"] == turn]


def route(record):
    return IntentRouteDecision.model_validate(json.loads(record["arguments"]))


def context(cid):
    row = ROWS[cid]
    if len(row["turns"]) == 1:
        return []
    turn = row["turns"][0]
    body = turn["body"]
    assert body["status"] == "completed" and body["error_code"] is None
    return [
        {"role": "task_state", "content": json.dumps(body["execution_trace"]["task_state"])},
        {"role": "user", "content": turn["message"]},
        {"role": "assistant", "content": body["reply"], "query_context": {
            "source_run_id": body["id"], "successful_query": True,
            "primary_intent": body["primary_intent"], "request_kind": body["request_kind"],
            "requested_effect": body["requested_effect"], "risk_level": body["risk_level"],
            "resolved_query": body["resolved_query"],
        }},
    ]


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield


@pytest.fixture(autouse=True)
def fixed_scene_and_offline_model(monkeypatch):
    token = BUSINESS_DATE.set(date(2026, 10, 9))
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "offline-r45-archived-fixtures")
    def forbidden(*args, **kwargs):
        raise AssertionError("Unregistered model response; external provider forbidden")
    monkeypatch.setattr(agent_intent_model, "structured_chat_completion", AsyncMock(side_effect=forbidden))
    monkeypatch.setattr(reporting, "structured_chat_completion", AsyncMock(side_effect=forbidden))
    original_send = httpx.AsyncClient.send
    async def local_only(client, request, **kwargs):
        assert request.url.host == "test", "External network forbidden in R45 regression"
        return await original_send(client, request, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, "send", local_only)
    try:
        yield
    finally:
        BUSINESS_DATE.reset(token)



import re
@pytest.mark.asyncio
@pytest.mark.parametrize('cid',ALL)
async def test_archived_failed_turn_routes(cid,monkeypatch):
    index=1 if cid.startswith('QQ04') else 0
    for record in route_rows(cid,index):
        monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=route(record)))
        out=await resolve_intent_with_fallback(ROWS[cid]['turns'][index]['message'],context_messages=context(cid) if index else [],use_model=True)
        assert not out.understanding_failed,(cid,out.error_category)
        assert out.resolution.evidence_requirements==['nutrition_history']
        if cid.startswith('OQ01'):assert out.resolution.resolved_query==ROWS[cid]['turns'][0]['message']

def synthetic_plan(cid,turn):
    return dict(supported=True,days=30,start_date='',end_date='',logged_dates=[],meal_types=[],
                metrics=['protein_g'] if cid.startswith('OQ01') else ['calories','protein_g','carbs_g','fat_g'],
                operations=['list','max','min'] if cid.startswith('QQ05') else ['list','sum'] if cid.startswith('OQ01') else ['list','sum','mean']) | ROWS[cid]['gold'][turn]['arguments']

@pytest.mark.parametrize('suffix',['再保存今天饮食','再删除昨天记录','如果不支持就补录零千卡','另查今天每餐吃了什么','再评估近期饮食是否健康'])
@pytest.mark.parametrize('target',['nutrition_today','nutrition_recent_context'])
def test_independent_goal_keeps_evidence(target,suffix):
    r=_route_to_resolution(route(route_rows('OQ01-r1',0)[0]))
    r.evidence_requirements=['nutrition_history',target]
    msg=ROWS['OQ01-r1']['turns'][0]['message']+suffix+'。'
    assert set(narrow_report_evidence(r,msg).evidence_requirements)=={'nutrition_history',target}

@pytest.mark.parametrize('suffix',['再删除昨天记录','并保存今天晚餐','就记录下来','先修改已保存餐次'])
def test_revision_cannot_hide_write(suffix):
    msg=ROWS['QQ04-r1']['turns'][1]['message']+suffix+'。'
    assert not is_nutrition_filter_revision(msg)

@pytest.mark.parametrize('prefix',['如果缺记录','如果有并列或缺记录','若缺少记录','缺记录'])
def test_missing_records_noun_with_writes(prefix):
    assert resolve_intent('查询10月8日饮食，'+prefix+'，也请说明。').request_kind=='query'
    assert resolve_intent('查询10月8日饮食，'+prefix+'，然后记录今天晚餐200克米饭。').request_kind=='mutation'

@pytest.mark.parametrize('message',['将缺记录下来','缺记录为零千卡','缺记录就补录零千卡'])
def test_record_verbal_complement_not_masked(message):
    assert resolve_intent(message).request_kind=='mutation'
@pytest.mark.asyncio
@pytest.mark.parametrize("cid", ALL)
async def test_original_routes_through_api_worker_sql_and_final_get(cid, db_session, session_factory, monkeypatch):
    row = ROWS[cid]
    user, _, conv, _ = await _context(db_session, "r44-" + uuid4().hex)
    for saved in row["meals"]:
        meal = MealLog(user_id=user.id, logged_at=date.fromisoformat(saved["logged_at"]), meal_type=saved["meal_type"])
        db_session.add(meal)
        await db_session.flush()
        for item in saved["items"]:
            db_session.add(MealItem(meal_id=meal.id, **item))
    await db_session.commit()
    async def snapshot(db):
        result = await db.execute(select(MealLog.logged_at, MealLog.meal_type, MealItem.food_name,
                                         MealItem.amount_g, MealItem.calories, MealItem.protein_g,
                                         MealItem.carbs_g, MealItem.fat_g)
                                  .join(MealItem, MealItem.meal_id == MealLog.id)
                                  .where(MealLog.user_id == user.id)
                                  .order_by(MealLog.logged_at, MealLog.meal_type, MealItem.food_name))
        return result.all()
    before = await snapshot(db_session)
    seen = []
    async def model(message, *, context_messages, **kwargs):
        turn = next(t["index"] for t in row["turns"] if t["message"] == message)
        records = route_rows(cid, turn)
        # Before the repair, QQ05's first turn failed, so its retry started
        # a new task. With the first turn now successful, replay the archived
        # continue route and bind only the current task identity.
        record = next((r for r in records if turn and route(r).task_update.action == 'continue'), records[-1])
        value = route(record)
        # New test identities are rebound only here; archived strings unchanged.
        if turn:
            value.task_update.task_id = active_task(state_from_context(context_messages)).id
        seen.append(turn)
        return value
    monkeypatch.setattr(agent_intent_model, "_invoke_model_route", model)
    async def planner(messages, **kwargs):
        request = json.loads(messages[-1]["content"])
        turn = next(t["index"] for t in row["turns"] if t["message"] == request["original_message"])
        return SimpleNamespace(payload=synthetic_plan(cid, turn))
    plan = AsyncMock(side_effect=planner)
    monkeypatch.setattr(reporting, "structured_chat_completion", plan)
    async def get_session():
        async with session_factory() as db:
            yield db
    app.dependency_overrides[get_db] = get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"Authorization": "Bearer " + create_access_token(user.id)}) as client:
            for turn in row["turns"]:
                response = await client.post("/api/v1/agent/runs", json={
                    "conversation_id": conv.id, "message": turn["message"], "client_request_id": uuid4().hex,
                })
                assert response.status_code == 202
                rid = response.json()["run_id"]
                async with session_factory() as db:
                    assert await claim_next_agent_run(db) == rid
                await process_agent_run(session_factory, rid)
                body = (await client.get("/api/v1/agent/runs/" + rid)).json()
                assert body["status"] == "completed" and body["error_code"] is None, body
                assert body["evidence_requirements"] == ["nutrition_history"]
                assert body["request_kind"] == "query" and body["requested_effect"] == "read"
                assert body["cards"], body["reply"]
                expected = row['gold'][turn['index']]
                card = next(c['data'] for c in body['cards'] if c['type']=='nutrition.list_history')
                actual = [{'date':d['date'], **{k:d['total_'+k] for k in ['calories','protein_g','carbs_g','fat_g']}} for d in card['days']]
                assert actual == expected['rows']
                assert '近期饮食' not in body['reply'] and '你提到' not in body['reply']
                for fact in expected.get('answer_facts',[]):
                    assert re.search(fact,body['reply']), (cid,turn['index'],fact,body['reply'])
                if cid.startswith('OQ01'):
                    assert '合计 57.8 g' in body['reply'] and '10-09：未找到晚餐记录' in body['reply']
                    assert '10-10 至 2026-10-11' in body['reply'] or '10-11：晚于查询截止日期' in body['reply']
                if cid.startswith('QQ04') and turn['index']==1:
                    for text in ['热量记录合计 260','蛋白质记录合计 6','碳水记录合计 56','脂肪记录合计 2','10-10：晚于查询截止日期']:
                        assert text in body['reply']
                if cid.startswith('QQ05'):
                    assert '蛋白质最高：2026-10-08，36.8' in body['reply']
                    assert '脂肪最低：2026-10-06' in body['reply']

                messages = (await client.get("/api/v1/agent/conversations/" + conv.id + "/messages")).json()
                saved = next(m["content"] for m in messages if m.get("run_id") == rid and m["role"] == "assistant")
                assert saved == body["reply"]
        async with session_factory() as db:
            assert await snapshot(db) == before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id == user.id)) == 0
            assert await db.scalar(select(func.count()).select_from(AgentToolCall)) == len(row["turns"])
        assert seen == list(range(len(row["turns"])))
        assert plan.await_count == len(row["turns"])
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize('message',[
    '日期改为2026-10-08至2026-10-09这三天',
    '日期改为2026-02-30至2026-03-01这两天',
    '日期改为2026-10-09至2026-10-08这两天',
    '日期改为最近三天这三天',
])
def test_invalid_revision_cardinality(message):
    assert not is_nutrition_filter_revision(message)

@pytest.mark.parametrize('suffix',['另查今天每餐吃了什么','再评估近期饮食是否健康','并保存今天晚餐','并把缺记录补录为零千卡'])
def test_comparison_cannot_hide_second_goal(suffix):
    r=_route_to_resolution(route(route_rows('QQ05-r2',0)[0]))
    msg=ROWS['QQ05-r2']['turns'][0]['message']+suffix+'。'
    assert narrow_report_evidence(r,msg).evidence_requirements==r.evidence_requirements
