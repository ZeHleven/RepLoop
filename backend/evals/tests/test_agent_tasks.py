"""Complete API/SQL task flows; assertions live in the versioned task matrix.

Scripted mode replaces model boundaries only. Live mode records raw provider
outputs. Both use real runtime, tools, proposal handlers and isolated SQL.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.agent import AgentArtifact, AgentConversation, AgentProposal, AgentRun, AgentToolCall
from app.models.food import Food
from app.models.meal import MealItem, MealLog
from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from app.services.agent_intent import IntentResolution, IntentResolverOutcome, normalize_resolution
from app.services.agent_jobs import claim_next_agent_run, process_agent_run
from app.services.agent_tools import build_read_tools, LANGCHAIN_TOOL_NAMES
from app.services.ai_client import StructuredCompletionResult
from app.services.auth import create_access_token
from app.services.business_clock import business_today
from evals.task_fixtures import TaskRecord, selected_tasks, task_client  # noqa: F401
from evals.nutrition_answer_checks import history_scope_claim_is_consistent, today_calories_appear


async def seed(session):
    key = uuid4().hex
    user = User(id="task-" + key, email=key + "@example.invalid", password_hash="synthetic-no-login")
    session.add(user)
    await session.flush()
    profile = UserProfile(user_id=user.id, age=30, gender="prefer_not_to_say", height_cm=170,
                          weight_kg=65, primary_goal="增肌", training_days_per_week=3,
                          diet_restriction=None, injuries=[], chronic_conditions=[], onboarding_completed=True)
    conversation = AgentConversation(user_id=user.id)
    foods = [Food(id=key + "-" + name, name_zh=name, category=category,
                  calories_per_100g=calories, protein_g=protein, carbs_g=carbs, fat_g=fat,
                  diet_tags=[], is_common_in_china=True, is_active=True)
             for name, category, calories, protein, carbs, fat in [
                 ("米饭", "grain", 130, 3, 28, 1), ("鸡胸", "protein", 165, 31, 0, 3.6),
                 ("橄榄油", "oil", 900, 0, 0, 100)]]
    session.add_all([profile, conversation, *foods, WeightLog(user_id=user.id, weight_kg=66)])
    await session.commit()
    return user, profile, conversation, foods


async def meal_count(session, user_id):
    return await session.scalar(select(func.count(MealLog.id)).where(MealLog.user_id == user_id))


def scripted_resolution(case):
    if case.scenario == "meal_lifecycle":
        return IntentResolution(primary_intent="nutrition_today_query", intent_domain="nutrition",
                                request_kind="generation", requested_output="daily_meal_plan",
                                resolved_query=case.message, confidence=1)
    return IntentResolution(primary_intent="nutrition_today_query", intent_domain="nutrition",
                            request_kind="query", evidence_requirements=case.parameters["evidence"],
                            resolved_query=case.message, confidence=1)


async def run_meal(case, record, client, session, monkeypatch, user, profile, conversation, foods):
    parameters = case.parameters
    payload = {
        "meals": [{"meal_type": meal, "items": [
            {"food_id": foods[0].id, "amount_g": 300}, {"food_id": foods[1].id, "amount_g": 100},
            {"food_id": foods[2].id, "amount_g": 20}]} for meal in ("早餐", "午餐", "晚餐")],
        "rationale": [f"合成解释 {i}：保留条件和不确定性。" for i in range(parameters.get("rationale_count", 6))],
    }
    if parameters.get("bad_food"):
        payload["meals"][0]["items"][0]["food_id"] = "not-a-catalog-food"
    from app.services import agent_daily_meal_plans as meals
    real_completion = meals.structured_chat_completion

    async def completion(*args, **kwargs):
        if record.data["model_mode"] == "live":
            result = await real_completion(*args, **kwargs)
        else:
            raw = json.dumps(payload, ensure_ascii=False)
            result = StructuredCompletionResult(payload=payload, raw_output=raw, mode="deepseek_json_mode",
                                                finish_reason="stop", duration_ms=0, output_chars=len(raw))
        record.data["raw_model_outputs"].append({"stage": "meal_generation", "raw": result.raw_output,
                                                "payload": result.payload, "mode": result.mode})
        return result

    monkeypatch.setattr(meals, "structured_chat_completion", completion)
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    generated = await record.request(client, "POST", "/api/v1/agent/chat", headers=headers, json={
        "conversation_id": conversation.id, "message": case.message})
    body = generated.json()
    run = await session.get(AgentRun, body["run_id"])
    audits = list((await session.scalars(select(AgentToolCall).where(AgentToolCall.run_id == run.id))).all())
    record.observe("generation", request_kind=run.request_kind, has_artifact="artifact" in body,
                   has_proposal="proposal" in body, model_attempts=sum(
                       item["stage"] == "meal_generation" for item in record.data["raw_model_outputs"]),
                   evidence_count=sum(item.call_id.startswith("evidence:") for item in audits if item.call_id))
    record.observe("writes", before_decision=await meal_count(session, user.id))
    if "artifact" not in body:
        return
    artifact = await session.get(AgentArtifact, body["artifact"]["id"])
    data = artifact.payload_data
    record.data["artifact_payload"] = data
    generated_items = [item for meal in data["meals"] for item in meal["items"]]
    consistent = all(abs(sum(item[field] for item in generated_items) - data["daily_totals"][field]) < 0.11
                     for field in ("calories", "protein_g", "carbs_g", "fat_g"))
    raw_reasons = record.data["raw_model_outputs"][-1]["payload"].get("rationale", [])
    record.observe("generation", nutrition_consistent=consistent,
                   rationale_preserved=all(reason in "\n".join(data["rationale"]) for reason in raw_reasons),
                   card_matches_artifact=any(card["type"] == "daily_meal_plan"
                       and card["data"]["daily_totals"] == data["daily_totals"] for card in body["cards"]))
    save = await record.request(client, "POST", "/api/v1/agent/chat", headers=headers, json={
        "conversation_id": conversation.id, "message": "保存这份方案",
        "artifact_action": {"action": "save_as_proposal", "artifact_id": artifact.id,
                            "expected_version": artifact.version,
                            "payload_fingerprint": artifact.payload_fingerprint}})
    proposal = save.json()["proposal"]
    record.observe("writes", before_decision=await meal_count(session, user.id))
    action = parameters.get("action", "confirm")
    if action == "expired":
        row = await session.get(AgentProposal, proposal["id"])
        row.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    elif action == "conflict":
        profile.diet_restriction = "不吃鸡肉"
        await session.commit()
    elif action == "revision":
        await record.request(client, "POST", "/api/v1/agent/chat", headers=headers, json={
            "conversation_id": conversation.id, "message": "重新安排今天的饮食"})
    request = {"expected_version": proposal["version"], "client_request_id": "decision-" + uuid4().hex}
    endpoint = f"/api/v1/proposals/{proposal['id']}/" + ("reject" if action == "reject" else "confirm")
    decided = await record.request(client, "POST", endpoint, headers=headers, json=request)
    record.observe("decision", http_status=decided.status_code, status=decided.json().get("status"))
    record.observe("writes", after_decision=await meal_count(session, user.id))
    if action == "confirm":
        replay = await record.request(client, "POST", endpoint, headers=headers, json=request)
        record.observe("decision", replay_equal=replay.json() == decided.json())
        record.observe("writes", after_replay=await meal_count(session, user.id))
        response = await record.request(client, "GET", "/api/v1/meals/history", headers=headers)
        day = next((item for item in response.json() if item["date"] == data["target_date"]), {})
        record.observe("readback", matches_artifact=all(
            abs(day.get("total_" + field, -999) - data["daily_totals"][field]) < 0.11
            for field in ("calories", "protein_g", "carbs_g", "fat_g")))


async def run_query(case, record, client, session, session_factory, monkeypatch, user, conversation, foods):
    # Sparse history plus a future row exercises the real date/owner SQL filter.
    for day in (date(2026, 8, 1), date(2026, 9, 29), date(2026, 9, 30)):
        meal = MealLog(user_id=user.id, logged_at=day, meal_type="早餐")
        session.add(meal)
        await session.flush()
        session.add(MealItem(meal_id=meal.id, food_id=foods[0].id, food_name="米饭",
                             amount_g=100, calories=130, protein_g=3, carbs_g=28, fat_g=1))
    await session.commit()
    if "budget" in case.parameters:
        monkeypatch.setattr(settings, "AGENT_MAX_TOOL_CALLS", case.parameters["budget"])

    async def read_agent(db, *, user_id, user_message, tool_allowlist, **_kwargs):
        from app.services.agent_query_reports import report_for_resolution, execute_query_report
        report = report_for_resolution(_kwargs["resolution"], tool_allowlist)
        if report is not None and not case.parameters.get("omit_tool"):
            return await execute_query_report(report, build_read_tools(db, user_id=user_id, allowlist=tool_allowlist))
        messages = [HumanMessage(content=user_message)]
        selected = tool_allowlist[:-1] if case.parameters.get("omit_tool") else tool_allowlist
        for i, (tool_id, tool) in enumerate(zip(selected, build_read_tools(db, user_id=user_id, allowlist=selected))):
            call_id = f"query-{i}"
            result = await tool.ainvoke({})
            messages.extend([AIMessage(content="", tool_calls=[{
                "name": LANGCHAIN_TOOL_NAMES[tool_id], "args": {}, "id": call_id, "type": "tool_call"}]),
                ToolMessage(content=json.dumps(result, ensure_ascii=False, default=str),
                            tool_call_id=call_id, name=LANGCHAIN_TOOL_NAMES[tool_id])])
        reply = (f"今天共{result['total_calories']:g}千卡。" if selected == ["nutrition.get_today"]
                 else "合成模型回答；事实与展示断言读取真实工具卡片。")
        messages.append(AIMessage(content=reply))
        return {"messages": messages}

    if record.data["model_mode"] == "scripted":
        monkeypatch.setattr("app.services.agent_runtime.invoke_langchain_agent", read_agent)
    headers = {"Authorization": "Bearer " + create_access_token(user.id)}
    payload = {"conversation_id": conversation.id, "message": case.message,
               "client_request_id": "query-" + uuid4().hex}
    submitted = await record.request(client, "POST", "/api/v1/agent/runs", headers=headers, json=payload)
    run_id = submitted.json()["run_id"]
    run = await session.get(AgentRun, run_id)
    # Durable request time is Shanghai Sep 29, even if the worker runs a day later.
    run.queued_at = datetime(2026, 9, 28, 16, 30, tzinfo=timezone.utc)
    if case.parameters.get("recovered"):
        run.status = "running"
        run.attempt_count = 1
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    await session.commit()
    claimed = await claim_next_agent_run(session)
    assert claimed == run_id  # runner/setup invariant, not a business score
    await process_agent_run(session_factory, run_id)
    polled = await record.request(client, "GET", f"/api/v1/agent/runs/{run_id}", headers=headers)
    duplicate = await record.request(client, "POST", "/api/v1/agent/runs", headers=headers, json=payload)
    await session.refresh(run)
    trace = run.execution_trace or {}
    body = polled.json()
    contract = trace.get("evidence_contract", {})
    record.observe("query", request_kind=run.request_kind,
                   meal_count_unchanged=await meal_count(session, user.id) == 3,
                   idempotent=duplicate.json().get("run_id") == run_id,
                   as_of=trace.get("request_context", {}).get("as_of"),
                   contract_status=contract.get("status"),
                   required_count=len(contract.get("required_tools", [])),
                   tool_calls=trace.get("budget_usage", {}).get("tool_calls"),
                   terminal_action=trace.get("terminal_action"), reply=body.get("reply"))
    cards = body.get("cards", [])
    for card in cards:
        data = card["data"]
        if card["type"] == "nutrition.get_today":
            record.observe("query", calories=data.get("total_calories"), card_date=data.get("date"),
                           reply_facts_consistent=today_calories_appear(body["reply"], data))
        elif card["type"] == "nutrition.list_history":
            record.observe("query", logged_dates=[item["date"] for item in data["days"]],
                           scope_selection=data.get("scope", {}).get("selection"),
                           reply_facts_consistent=history_scope_claim_is_consistent(body["reply"], data))


@pytest.mark.asyncio
@pytest.mark.parametrize("task_case", selected_tasks(), ids=lambda case: case.id)
async def test_complete_task(task_case, task_client, db_session, session_factory, monkeypatch):
    record = TaskRecord(task_case)
    user = None
    user_id = None
    try:
        user, profile, conversation, foods = await seed(db_session)
        user_id = user.id
        if record.data["model_mode"] == "scripted":
            async def resolve(message, **_kwargs):
                return IntentResolverOutcome(resolution=normalize_resolution(
                    message, scripted_resolution(task_case)), source="model", attempt_count=1)
            monkeypatch.setattr("app.services.agent_runtime.resolve_intent_with_fallback", resolve)
        else:
            from app.services import agent_intent_model, agent_runtime
            intent_completion = agent_intent_model.structured_chat_completion
            direct_agent = agent_runtime.invoke_langchain_agent

            async def capture_intent(*args, **kwargs):
                completion = await intent_completion(*args, **kwargs)
                record.data["raw_model_outputs"].append({
                    "stage": "intent", "raw": completion.raw_output,
                    "payload": completion.payload, "mode": completion.mode})
                return completion

            async def capture_direct(*args, **kwargs):
                result = await direct_agent(*args, **kwargs)
                record.data["raw_agent_messages"] = [
                    message.model_dump(mode="json") for message in result.get("messages", [])]
                return result

            monkeypatch.setattr(agent_intent_model, "structured_chat_completion", capture_intent)
            monkeypatch.setattr(agent_runtime, "invoke_langchain_agent", capture_direct)
        if task_case.scenario == "meal_lifecycle":
            await run_meal(task_case, record, task_client, db_session, monkeypatch,
                           user, profile, conversation, foods)
        else:
            await run_query(task_case, record, task_client, db_session, session_factory,
                            monkeypatch, user, conversation, foods)
        await record.capture_runs(db_session, user_id)
    except Exception as exc:
        # Preserve failures, including unexpected runner/fixture errors; no
        # automatic claim that a failure was the model's fault.
        record.data["error"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        try:
            await db_session.rollback()
            if user_id is not None:
                await record.capture_runs(db_session, user_id)
        except Exception:
            pass
        raise
    finally:
        score = record.finish()
    assert score["passed"], json.dumps(score, ensure_ascii=False)
