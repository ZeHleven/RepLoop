"""Model-extracted symptom facts; the server still owns every decision.

Design: docs/agent/16-symptom-facts-extraction-design.md. One strict-schema
call returns enums, booleans, a number and verbatim user quotes. Nothing
here writes a reply. Every quote must be a contiguous substring of the
user's own text in this symptom episode, otherwise the item is dropped; a
structurally invalid result is discarded so the caller falls back to the
rule detector. An own-symptom purpose whose quotes all fail the check is kept
as "unverified": the turn still pauses the whole plan (unclear site).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings
from app.services.ai_client import structured_chat_completion
from app.services.structured_schema import strict_tool_schema

logger = logging.getLogger(__name__)

Purpose = Literal[
    "own_symptom_training", "record_query", "general_knowledge",
    "third_party", "past_resolved", "no_symptom",
]
Region = Literal["shoulder", "wrist_elbow", "knee", "back", "ankle", "other", "unclear"]
Side = Literal["left", "right", "both", "none"]
CareKind = Literal[
    "swelling", "bruise", "deformity", "trauma", "cannot_bear_weight",
    "numbness", "weakness", "rest_or_night_pain",
]
EmergencyKind = Literal[
    "chest_pain_or_pressure", "cold_sweat", "breathing_difficulty", "cyanosis",
    "syncope_or_blackout", "abnormal_heartbeat", "one_sided_numbness_or_weakness",
    "speech_difficulty",
]

REGION_AREA = {
    "shoulder": "肩关节", "wrist_elbow": "腕肘部", "knee": "膝关节",
    "back": "腰背部", "ankle": "踝关节",
}
REGION_LABEL = {
    "shoulder": "肩", "wrist_elbow": "手腕/肘", "knee": "膝", "back": "腰背",
    "ankle": "脚踝", "other": "", "unclear": "",
}
SIDE_LABEL = {"left": "左", "right": "右", "both": "双", "none": ""}
CARE_LABEL = {
    "swelling": "肿胀", "bruise": "淤青", "deformity": "变形", "trauma": "外伤",
    "cannot_bear_weight": "无法承重", "numbness": "麻木", "weakness": "无力",
    "rest_or_night_pain": "休息或夜间疼痛",
}


class _Report(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region: Region
    side: Side
    site_text: str = Field(max_length=20, description="用户原话里的部位词，如“脚脖子”“右膝”；原文没有部位就填空字符串")
    quote: str = Field(min_length=1, max_length=80, description="用户原话中描述该症状的连续片段，逐字复制")


class _Care(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: CareKind
    quote: str = Field(min_length=1, max_length=80)


class _Emergency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: EmergencyKind
    quote: str = Field(min_length=1, max_length=80)


class SymptomFactsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Purpose
    reports: list[_Report] = Field(max_length=6)
    care_signals: list[_Care] = Field(max_length=8)
    clinician_quote: str = Field(max_length=120, description="用户转述的医生/康复师/治疗师嘱咐原文；没有就填空字符串")
    clinician_restricts_load: bool
    time_cap_minutes: int = Field(ge=0, le=180, description="每次训练时长上限（分钟）；没有说就填 0")
    emergency_signals: list[_Emergency] = Field(max_length=8)
    continues_previous: bool
    resolved_now: bool


SYSTEM_PROMPT = """你只负责从健身对话里提取「用户本人当前症状」的结构化事实，不回答问题、不给建议、不写回复。输出必须符合 schema。

判断 purpose（本轮主要目的，只选一个）：
- own_symptom_training：用户本人现在有疼、酸（关节）、麻、肿、扭伤、发炎、受伤或医嘱限制，并且在问训练怎么办，或者只是在陈述这个情况。
- record_query：主要是在查记录、统计、列日期、算数，即使顺带提到疼痛。
- general_knowledge：泛泛的知识或常识问题，不是在报告本人当前情况（如“××疼的人适合做什么”“科普用”“不是说我”“我没受伤，只是问问”）。
- third_party：疼痛或受伤的是别人（家人、朋友、学员等），本人没有当前症状。
- past_resolved：只是过去的伤，已经好了、现在不疼。
- no_symptom：本人没有症状，问的是其他训练、饮食或计划问题；训练后普通的肌肉酸痛（不涉及关节）也属于这里。
如果同一句里既有别人的伤又有本人当前的症状，以本人为准。

reports：只列本人当前症状。region 按实际身体部位归类：shoulder 肩；wrist_elbow 手腕、手肘、前臂；knee 膝；back 腰、背；ankle 脚踝；other 其他部位（髋、臀、颈、大腿、小腿、手指、胸等）；unclear 说不清部位。方言或口语部位按实际部位归类。被否定的部位（如“膝盖没事”“肩不疼”）不要列。
care_signals：本人当前出现的就医信号，被否定的（如“没肿”“没撞到”）不要列。各类含义：swelling 看得见的肿、鼓起来（单纯“胀痛”“发胀”的感觉不算）；bruise 淤青、青紫；deformity 变形、看着歪；trauma 扭到、崴、摔、撞、磕、拉伤等受伤经过；cannot_bear_weight 脚落地撑不住、不能踩、不能承重；numbness 麻、木；weakness 没劲、使不上劲、发软、拿不稳；rest_or_night_pain 不动也疼、夜里或睡觉时疼。
clinician_quote：用户转述的医生或康复师嘱咐原文；clinician_restricts_load 在嘱咐要求休息、制动、不能负重或避免用力时为 true。
time_cap_minutes：用户说的每次训练时长上限，没有填 0。
emergency_signals：本人当前的急症信号，被否定的不要列。
continues_previous：本轮没有重新描述症状，但明显在接着上一轮的症状话题追问（如“那上肢还能练吗”）。
resolved_now：用户说现在已经不疼、已经好了。

所有 quote 必须从用户原话里逐字复制一段连续文字，不能改写、不能拼接、不能补字。没有就返回空数组。"""

EXAMPLE = {
    "purpose": "own_symptom_training",
    "reports": [{"region": "knee", "side": "right", "site_text": "右膝", "quote": "右膝下楼梯的时候疼"}],
    "care_signals": [],
    "clinician_quote": "",
    "clinician_restricts_load": False,
    "time_cap_minutes": 20,
    "emergency_signals": [],
    "continues_previous": False,
    "resolved_now": False,
}
SCHEMA = strict_tool_schema(SymptomFactsOutput)


# No word-list filtering of reports here (held-out v4 HP01: a negation filter
# dropped "右肩只要往高处抬就刺刺的，放下不疼"). If the model over-reports a
# denied site, the plan only pauses more, never less.


@dataclass
class SymptomFacts:
    purpose: str
    reports: list[dict[str, str]]
    care: list[str]
    clinician_quote: str
    clinician_restricts_load: bool
    time_cap_minutes: int | None
    emergency: list[str]
    continues_previous: bool
    resolved_now: bool
    dropped_quotes: int = 0
    unverified: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def as_data(self) -> dict[str, Any]:
        return {
            "source": "model", "purpose": self.purpose, "reports": self.reports,
            "care": self.care, "clinician_quote": self.clinician_quote,
            "clinician_restricts_load": self.clinician_restricts_load,
            "time_cap_minutes": self.time_cap_minutes, "emergency": self.emergency,
            "continues_previous": self.continues_previous, "resolved_now": self.resolved_now,
            "dropped_quotes": self.dropped_quotes, "unverified": self.unverified,
        }


_ENUMS = {
    "region": set(Region.__args__), "side": set(Side.__args__),
    "care": set(CareKind.__args__), "emergency": set(EmergencyKind.__args__),
}


def normalize_payload(payload: Any) -> Any:
    """Repair out-of-enum or over-long values the provider sometimes returns.

    Seen in the dev pool: ``side: "unclear"`` voided a whole extraction and
    the turn fell back to rules. Only shape is repaired here; every quote is
    still checked against the user's own text in ``validate_facts``. Unknown
    care or emergency kinds are dropped (the rule and intent backstops stay).
    """
    if not isinstance(payload, dict):
        return payload
    p = dict(payload)
    reports = []
    for r in p.get("reports") or []:
        if not isinstance(r, dict):
            continue
        r = dict(r)
        if r.get("region") not in _ENUMS["region"]:
            r["region"] = "unclear"
        if r.get("side") not in _ENUMS["side"]:
            r["side"] = "none"
        if not isinstance(r.get("site_text"), str) or len(r["site_text"]) > 20:
            r["site_text"] = ""
        if isinstance(r.get("quote"), str):
            r["quote"] = r["quote"][:80]
        reports.append(r)
    p["reports"] = reports[:6]
    for key, enum, limit in (("care_signals", "care", 8), ("emergency_signals", "emergency", 8)):
        items = []
        for item in p.get(key) or []:
            if isinstance(item, dict) and item.get("kind") in _ENUMS[enum] and isinstance(item.get("quote"), str):
                items.append({"kind": item["kind"], "quote": item["quote"][:80]})
        p[key] = items[:limit]
    if isinstance(p.get("clinician_quote"), str):
        p["clinician_quote"] = p["clinician_quote"][:120]
    cap = p.get("time_cap_minutes")
    if not isinstance(cap, int) or isinstance(cap, bool) or not 0 <= cap <= 180:
        p["time_cap_minutes"] = 0
    return p


def validate_facts(output: SymptomFactsOutput, *, user_texts: list[str]) -> SymptomFacts | None:
    """Keep only items whose quote is the user's own verbatim text."""
    def own(quote: str) -> bool:
        q = (quote or "").strip()
        return bool(q) and any(q in text for text in user_texts)

    dropped = 0
    reports = []
    for r in output.reports:
        if own(r.quote):
            reports.append({
                "region": r.region, "side": r.side,
                "site_text": r.site_text if own(r.site_text) else "", "quote": r.quote,
            })
        else:
            dropped += 1
    care = []
    for c in output.care_signals:
        if own(c.quote):
            care.append(c.kind)
        else:
            dropped += 1
    emergency = []
    for e in output.emergency_signals:
        if own(e.quote):
            emergency.append(e.kind)
        else:
            dropped += 1
    clinician = output.clinician_quote if own(output.clinician_quote) else ""
    if output.clinician_quote and not clinician:
        dropped += 1
    # The model says the user has a current symptom but no quote survived:
    # keep the purpose so the turn still takes the deterministic path with an
    # unclear site (whole plan paused) instead of falling to the model path.
    unverified = bool(output.purpose == "own_symptom_training" and not reports and not clinician
                      and not output.continues_previous and not emergency)
    return SymptomFacts(
        purpose=output.purpose,
        reports=reports,
        care=list(dict.fromkeys(care)),
        clinician_quote=clinician,
        clinician_restricts_load=bool(output.clinician_restricts_load and clinician),
        time_cap_minutes=output.time_cap_minutes or None,
        emergency=list(dict.fromkeys(emergency)),
        continues_previous=output.continues_previous,
        resolved_now=output.resolved_now,
        dropped_quotes=dropped,
        unverified=unverified,
        raw=output.model_dump(),
    )


async def extract_symptom_facts(
    message: str,
    *,
    prior_user_messages: list[str] | None = None,
    previous_reply_was_scope: bool = False,
) -> SymptomFacts | None:
    """Return validated facts, or None on any failure (caller uses rules)."""
    if not settings.AGENT_SYMPTOM_FACTS_MODEL_ENABLED or not settings.DEEPSEEK_API_KEY:
        return None
    priors = [m for m in (prior_user_messages or []) if isinstance(m, str)][:3]
    content = json.dumps({
        "current_user_message": message,
        "previous_user_messages_newest_first": priors,
        "previous_assistant_reply_was_symptom_answer": previous_reply_was_scope,
    }, ensure_ascii=False)
    try:
        completion = await structured_chat_completion(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
            model=settings.AGENT_INTENT_MODEL,
            max_tokens=settings.AGENT_SYMPTOM_FACTS_MAX_TOKENS,
            temperature=0,
            function_name="submit_symptom_facts",
            function_description="提交用户本人当前症状的结构化事实（只提取，不建议）",
            json_schema=SCHEMA,
            json_example=EXAMPLE,
            timeout_seconds=settings.AGENT_SYMPTOM_FACTS_TIMEOUT_SECONDS,
        )
        if completion.parse_error or completion.payload is None:
            logger.warning("Symptom facts unusable: %s", completion.parse_error or "empty")
            return None
        output = SymptomFactsOutput.model_validate(normalize_payload(completion.payload))
    except ValidationError as exc:
        # Locations and error types only; never the user's text.
        logger.warning("Symptom facts invalid: %s", [(e["loc"], e["type"]) for e in exc.errors()][:5])
        return None
    except Exception as exc:  # provider, timeout: fall back to rules
        logger.warning("Symptom facts extraction failed: %s", type(exc).__name__)
        return None
    return validate_facts(output, user_texts=[message, *priors])
