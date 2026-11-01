"""docs/agent/16: model-extracted symptom facts (validation, fallback, mapping)."""
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services import agent_symptom_facts as facts_mod
from app.services.agent_symptom_facts import SymptomFactsOutput, extract_symptom_facts, validate_facts
from app.services.agent_symptom_scope import (
    AREA_ANKLE, AREA_BACK, AREA_KNEE, AREA_SHOULDER, AREA_WRIST_ELBOW, PlanItem, decide_scope, render_reply,
)

PLAN = [
    PlanItem(1, 0, "卧推", (AREA_SHOULDER, AREA_WRIST_ELBOW), 3, "10", 90, 20.0),
    PlanItem(1, 1, "深蹲", (AREA_KNEE, AREA_BACK, AREA_ANKLE), 3, "10", 90, 30.0),
]


def output(**kw):
    base = dict(purpose="own_symptom_training", reports=[], care_signals=[], clinician_quote="",
                clinician_restricts_load=False, time_cap_minutes=0, emergency_signals=[],
                continues_previous=False, resolved_now=False)
    base.update(kw)
    return SymptomFactsOutput.model_validate(base)


def report(region, quote, side="none", site_text=""):
    return {"region": region, "side": side, "site_text": site_text, "quote": quote}


MSG = "俺脚脖子今天迈步就疼，没崴着也没肿，单子上的推和蹲哪个该停"


def test_verbatim_quotes_survive_and_invented_ones_are_dropped():
    f = validate_facts(output(
        reports=[report("ankle", "脚脖子今天迈步就疼", site_text="脚脖子"), report("knee", "膝盖也疼")],
        care_signals=[{"kind": "swelling", "quote": "肿得厉害"}],
    ), user_texts=[MSG])
    assert [r["region"] for r in f.reports] == ["ankle"] and f.reports[0]["site_text"] == "脚脖子"
    assert f.care == [] and f.dropped_quotes == 2


def test_symptom_purpose_without_any_surviving_evidence_is_unusable():
    assert validate_facts(output(reports=[report("knee", "编造的原文")]), user_texts=[MSG]) is None
    assert validate_facts(output(purpose="third_party"), user_texts=[MSG]).purpose == "third_party"


def test_clinician_restriction_requires_a_real_quote():
    msg = "手肘还疼，大夫说这几天歇着别拿重东西"
    f = validate_facts(output(reports=[report("wrist_elbow", "手肘还疼")], clinician_quote="大夫说这几天歇着别拿重东西",
                              clinician_restricts_load=True), user_texts=[msg])
    assert f.clinician_restricts_load and f.clinician_quote
    g = validate_facts(output(reports=[report("wrist_elbow", "手肘还疼")], clinician_quote="医生让我休息",
                              clinician_restricts_load=True), user_texts=[msg])
    assert not g.clinician_restricts_load and g.clinician_quote == ""


@pytest.mark.asyncio
async def test_extraction_falls_back_to_none_on_every_failure(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_SYMPTOM_FACTS_MODEL_ENABLED", True)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "synthetic")

    class R:
        def __init__(self, payload=None, parse_error=None):
            self.payload, self.parse_error = payload, parse_error

    monkeypatch.setattr(facts_mod, "structured_chat_completion", AsyncMock(return_value=R(parse_error="bad")))
    assert await extract_symptom_facts(MSG) is None
    monkeypatch.setattr(facts_mod, "structured_chat_completion", AsyncMock(return_value=R(payload={"purpose": "x"})))
    assert await extract_symptom_facts(MSG) is None
    monkeypatch.setattr(facts_mod, "structured_chat_completion", AsyncMock(side_effect=TimeoutError()))
    assert await extract_symptom_facts(MSG) is None
    good = output(reports=[report("ankle", "脚脖子今天迈步就疼")]).model_dump()
    monkeypatch.setattr(facts_mod, "structured_chat_completion", AsyncMock(return_value=R(payload=good)))
    assert (await extract_symptom_facts(MSG)).reports[0]["region"] == "ankle"
    monkeypatch.setattr(settings, "AGENT_SYMPTOM_FACTS_MODEL_ENABLED", False)
    assert await extract_symptom_facts(MSG) is None


def facts(msg, **kw):
    return validate_facts(output(**kw), user_texts=[msg])


def test_model_region_drives_deterministic_pause_keep():
    d = decide_scope(MSG, PLAN, facts=facts(MSG, reports=[report("ankle", "脚脖子今天迈步就疼", site_text="脚脖子")]))
    day = {x.item.exercise_name: x for x in d.days[1]}
    assert not day["深蹲"].keep and day["卧推"].keep
    assert render_reply(d).startswith("脚脖子现在不舒服")


@pytest.mark.parametrize("region", ["other", "unclear"])
def test_unmapped_regions_pause_the_whole_plan(region):
    msg = "右边屁股一坐就疼"
    d = decide_scope(msg, PLAN, facts=facts(msg, reports=[report(region, "右边屁股一坐就疼")]))
    assert all(not x.keep for x in d.days[1])


def test_model_care_signal_and_doctor_rest_and_cap():
    msg = "膝盖肿了，康复师说这周别负重，每次最多15分钟"
    f = facts(msg, reports=[report("knee", "膝盖肿了")], care_signals=[{"kind": "swelling", "quote": "膝盖肿了"}],
              clinician_quote="康复师说这周别负重", clinician_restricts_load=True, time_cap_minutes=15)
    d = decide_scope(msg, PLAN, facts=f)
    reply = render_reply(d)
    assert reply.startswith("你提到了肿胀") and "以医生的嘱咐为准" in reply and "照常" not in reply
    assert d.time_cap_minutes == 15 and d.doctor_rest


def test_continuation_carries_previous_scope_unless_resolved():
    first = "早上起来膝盖肿了一圈"
    prev = decide_scope(first, PLAN, facts=facts(first, reports=[report("knee", "膝盖肿了一圈")],
                                                 care_signals=[{"kind": "swelling", "quote": "膝盖肿了一圈"}],
                                                 time_cap_minutes=0)).as_data()
    follow = "那推的那项总能留下吧"
    d = decide_scope(follow, PLAN, continuing=True, previous_scope=prev,
                     facts=facts(follow, purpose="no_symptom", continues_previous=True))
    assert d.care_now and all(not x.keep for x in d.days[1])
    done = "现在不疼了，推的那项能留吗"
    r = decide_scope(done, PLAN, continuing=True, previous_scope=prev,
                     facts=facts(done, purpose="past_resolved", continues_previous=True, resolved_now=True))
    assert not r.care_now


def test_previous_time_cap_is_carried_on_follow_up():
    first = "右膝屈膝就疼，每次顶多12分钟"
    prev = decide_scope(first, PLAN, facts=facts(first, reports=[report("knee", "右膝屈膝就疼")], time_cap_minutes=12)).as_data()
    follow = "那上半身那项还照旧不"
    d = decide_scope(follow, PLAN, continuing=True, previous_scope=prev,
                     facts=facts(follow, purpose="no_symptom", continues_previous=True))
    assert d.time_cap_minutes == 12 and "在 12 分钟以内" in render_reply(d)


def test_symptom_without_located_report_is_unclear_and_pauses_all():
    msg = "医生让我这周别练了"
    d = decide_scope(msg, PLAN, facts=facts(msg, clinician_quote="医生让我这周别练了", clinician_restricts_load=True))
    assert all(not x.keep for x in d.days[1])


def test_emergency_only_output_is_kept_without_body_report():
    msg = "做着做着眼前一片黑，人直接倒了"
    f = validate_facts(output(emergency_signals=[{"kind": "syncope_or_blackout", "quote": "眼前一片黑"}]), user_texts=[msg])
    assert f is not None and f.emergency == ["syncope_or_blackout"]


def test_rule_backstop_ignores_directly_negated_emergency_terms():
    from app.services.agent_symptom_scope import emergency_signals
    assert emergency_signals("肩肘都不疼，也没胸口发紧或喘不过气，蛋白质怎么分") == []
    assert emergency_signals("没一会儿就胸口疼得厉害")
    assert emergency_signals("没有胸闷，但是冒冷汗")
