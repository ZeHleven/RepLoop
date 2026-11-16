from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.model_budget import ModelCharge  # noqa: E402
from app.services.model_budget import billing_user, budget_status  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from app.services.agent_intent import (  # noqa: E402
    IntentDomain,
    IntentResolverOutcome,
    RequestKind,
    RequestedEffect,
    RequestedOutput,
)
from app.services.agent_intent_model import (  # noqa: E402
    resolve_intent_with_fallback,
)


@dataclass(frozen=True)
class RouteCase:
    case_id: str
    prompt: str
    domain: IntentDomain
    kind: RequestKind
    effect: RequestedEffect
    output: RequestedOutput = "answer"
    risk: Literal["low", "medium", "high"] = "low"
    decision: Literal["confirm", "reject"] | None = None


CASES = (
    RouteCase("profile.read", "我的个人训练资料是什么", "profile", "query", "read"),
    RouteCase("profile.update", "把我的训练目标改为增肌", "profile", "mutation", "update"),
    RouteCase("health.read", "查看我的健康筛查", "health", "query", "read"),
    RouteCase("health.update", "把伤病史更新为左膝旧伤", "health", "mutation", "update", risk="medium"),
    RouteCase("health.paraphrase.shoulder", "我过去发生过肩关节脱臼", "health", "query", "read", risk="medium"),
    RouteCase("health.paraphrase.lumbar", "腰椎一直有旧患，需要注意什么", "health", "query", "read", risk="medium"),
    RouteCase("health.paraphrase.surgery", "请记录我接受过膝关节手术", "health", "mutation", "update", risk="medium"),
    RouteCase("health.paraphrase.screening", "我只想查看健康筛查结果", "health", "query", "read"),
    RouteCase("general.paraphrase.physiology", "耐力训练为什么会提高最大摄氧量", "general", "query", "read"),
    RouteCase("health.red_flag", "我现在胸痛而且呼吸困难", "health", "query", "read", risk="high"),
    RouteCase("weight.read", "看看我最近的体重趋势", "profile", "query", "read"),
    RouteCase("weight.create", "记录今天体重为66.2公斤", "profile", "mutation", "create"),
    RouteCase("plan.read", "我当前的训练计划是什么", "workout_plan", "query", "read"),
    RouteCase("plan.assess", "结合执行情况评估当前计划是否合适", "workout_plan", "assessment", "read"),
    RouteCase("plan.update", "把当前计划改为每周训练三天", "workout_plan", "mutation", "update"),
    RouteCase("session.next", "下一练应该做什么", "workout_session", "query", "read"),
    RouteCase("session.active", "我正在进行的训练做到哪里了", "workout_session", "query", "read"),
    RouteCase("history.read", "查看最近四周训练记录", "workout_history", "query", "read"),
    RouteCase("progress.read", "分析一下最近训练进度", "workout_progress", "assessment", "read"),
    RouteCase("nutrition.today", "今天已经吃了什么", "nutrition", "query", "read"),
    RouteCase("nutrition.history", "查看最近七天饮食记录", "nutrition", "query", "read"),
    RouteCase("nutrition.food", "搜索食品库里的鸡胸肉", "nutrition", "query", "read"),
    RouteCase("nutrition.create", "记录今天午餐鸡胸肉150克", "nutrition", "mutation", "create"),
    RouteCase("nutrition.generation.1", "结合我的情况安排今天怎么吃", "nutrition", "generation", "read", "daily_meal_plan"),
    RouteCase("nutrition.generation.2", "看看我近期训练和体重，安排今天的饭", "nutrition", "generation", "read", "daily_meal_plan"),
    RouteCase("nutrition.generation.3", "综合我的资料给我配今天三顿饭", "nutrition", "generation", "read", "daily_meal_plan"),
    RouteCase("nutrition.generation.4", "请根据我的档案、体重和训练安排制定今天全天饮食", "nutrition", "generation", "read", "daily_meal_plan"),
    RouteCase("nutrition.generation.5", "今天是训练日，帮我推荐一整天每餐怎么吃", "nutrition", "generation", "read", "daily_meal_plan"),
    RouteCase("nutrition.generation.no_save", "参考我最近训练和体重的变化，给我今天三餐建议，不要记录", "nutrition", "generation", "read", "daily_meal_plan"),
    RouteCase("nutrition.single_meal.no_save", "晚饭怎么搭配比较合适？仅给建议，别保存成记录。", "nutrition", "query", "read"),
    RouteCase("decision.confirm", "确认提交这份提案", "general", "proposal_decision", "decide", decision="confirm"),
    RouteCase("decision.reject", "拒绝刚才的方案", "general", "proposal_decision", "decide", decision="reject"),
    RouteCase("general.read", "力量训练后为什么会酸痛", "general", "query", "read"),
)


def _case_result(case: RouteCase, outcome: IntentResolverOutcome) -> dict:
    """Build an enum-only, privacy-safe diagnostic result for one route."""
    route = outcome.resolution
    decision = next((
        str(item.value)
        for item in route.change_requests
        if item.field_path == "proposal.status"
    ), None)
    expected = {
        "intent_domain": case.domain,
        "request_kind": case.kind,
        "requested_effect": case.effect,
        "requested_output": case.output,
        "risk_level": case.risk,
        "decision_action": case.decision,
    }
    actual = {
        "intent_domain": route.intent_domain,
        "request_kind": route.request_kind,
        "requested_effect": route.requested_effect,
        "requested_output": route.requested_output,
        "risk_level": route.risk_level,
        "decision_action": decision,
    }
    mismatch_fields = [
        field for field, expected_value in expected.items()
        if actual[field] != expected_value
    ]
    safety_shortcut = case.kind == "proposal_decision" or case.risk == "high"
    ordinary_rules_fallback = outcome.source == "rules" and not safety_shortcut
    return {
        "case_id": case.case_id,
        "passed": not mismatch_fields and not outcome.understanding_failed,
        "expected": expected,
        "actual": actual,
        "mismatch_fields": mismatch_fields,
        "read_targets": list(route.evidence_requirements),
        "source": outcome.source,
        # Retain the original flat fields for report consumers migrating from v1.
        "request_kind": route.request_kind,
        "requested_effect": route.requested_effect,
        "understanding_failed": outcome.understanding_failed,
        "attempt_count": outcome.attempt_count,
        "error_category": outcome.error_category,
        "ordinary_rules_fallback": ordinary_rules_fallback,
    }


async def _seed_billing_subject() -> str:
    # The live gate owns a fresh local PostgreSQL service. Never seed an
    # evaluation identity into a production or remotely hosted database.
    url = make_url(settings.DATABASE_URL)
    if (url.drivername != "postgresql+asyncpg"
            or url.host not in {"localhost", "127.0.0.1", "::1"}
            or not (url.database == "fitness_live_eval" or (url.database or "").endswith("_test"))):
        raise RuntimeError("Route live evaluation requires an isolated local evaluation database")
    user_id = f"route-live-eval-{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as db:
        db.add(User(id=user_id, email=f"{user_id}@example.com", password_hash="synthetic-live-eval-only"))
        # Budget reservations use their own session and a user foreign key.
        await db.commit()
    return user_id


async def _billing_summary(user_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        charges = list((await db.execute(select(ModelCharge.status, ModelCharge.actual_nano).where(
            ModelCharge.user_id == user_id))).all())
    state = await budget_status(user_id)
    complete = not state["blocked"] and all(status == "settled" and type(cost) is int for status, cost in charges)
    return {
        "request_reservations": len(charges),
        "status_counts": dict(Counter(status for status, _ in charges)),
        "complete": complete,
        "estimated_cny": float(state["spent_yuan"]) if complete else None,
        "known_settled_cny": sum(cost for status, cost in charges if status == "settled" and type(cost) is int) / 1e9,
        "reserved_cny": float(state["reserved_yuan"]),
        "blocked": state["blocked"],
        "cost_basis": "Application conservative ledger tariff, not provider invoice; no account identifiers.",
    }


async def evaluate() -> dict:
    user_id = await _seed_billing_subject()
    results: list[dict] = []
    for case in CASES:
        # One durable account for the complete suite: no per-case budget reset.
        with billing_user(user_id):
            outcome = await resolve_intent_with_fallback(case.prompt, use_model=True)
        results.append(_case_result(case, outcome))

    passed_count = sum(bool(item["passed"]) for item in results)
    structural_valid_count = sum(
        item["source"] in {"model", "rules"}
        and not item["ordinary_rules_fallback"]
        for item in results
    )
    critical_ids = {
        case.case_id for case in CASES
        if case.kind in {"mutation", "proposal_decision"} or case.risk == "high"
    }
    critical_passed = sum(
        bool(item["passed"]) for item in results
        if item["case_id"] in critical_ids
    )
    ordinary_rules_fallbacks = sum(
        bool(item["ordinary_rules_fallback"]) for item in results
    )
    total = len(results)
    return {
        "schema_version": "2.0",
        "router_version": "semantic_route_v2",
        "model": settings.AGENT_INTENT_MODEL,
        "total": total,
        "passed_count": passed_count,
        "accuracy": passed_count / total,
        "structural_valid_count": structural_valid_count,
        "critical_total": len(critical_ids),
        "critical_passed": critical_passed,
        "ordinary_rules_fallbacks": ordinary_rules_fallbacks,
        "passed": (
            structural_valid_count == total
            and critical_passed == len(critical_ids)
            and passed_count / total >= 0.95
            and ordinary_rules_fallbacks == 0
        ),
        "results": results,
        "billing": await _billing_summary(user_id),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real-model Agent route preflight")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-sha")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not settings.DEEPSEEK_API_KEY:
        report = {
            "schema_version": "2.0",
            "router_version": "semantic_route_v2",
            "candidate_sha": args.candidate_sha,
            "passed": False,
            "fatal_error": {"type": "missing_deepseek_api_key"},
            "results": [],
        }
        status = 2
    else:
        try:
            report = asyncio.run(evaluate())
            status = 1 if args.strict and not report["passed"] else 0
        except Exception as exc:
            report = {
                "schema_version": "2.0",
                "router_version": "semantic_route_v2",
                "passed": False,
                "fatal_error": {"type": type(exc).__name__},
                "results": [],
            }
            status = 1
        report["candidate_sha"] = args.candidate_sha
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
