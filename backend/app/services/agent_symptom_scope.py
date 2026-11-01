"""Deterministic training scope for a user's current pain or injury.

Product decision 2026-10-25 (SCOPE-20261025-01): when the user reports a
current personal symptom while asking about training, the model no longer
writes a prescription. The server decides, from the exercise library's
contraindication areas, which saved-plan exercises pause and which may
continue unchanged, compresses kept work to a stated per-session time cap,
and appends fixed care signals and return conditions.

Nothing here diagnoses, invents new exercises, substitutes movements,
lowers weights by estimate, or permits a "try it and see" trial.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# Areas used by Exercise.contraindications and personalized_planner rules.
AREA_WRIST_ELBOW = "腕肘部"
AREA_SHOULDER = "肩关节"
AREA_KNEE = "膝关节"
AREA_ANKLE = "踝关节"
AREA_BACK = "腰背部"
KNOWN_AREAS = (AREA_WRIST_ELBOW, AREA_SHOULDER, AREA_KNEE, AREA_ANKLE, AREA_BACK)

_AREA_WORDS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"(左|右|双)?(?:手腕|腕部|腕关节|腕)"), AREA_WRIST_ELBOW, "手腕"),
    (re.compile(r"(左|右|双)?(?:手肘|胳膊肘|肘部|肘关节|肘|前臂)"), AREA_WRIST_ELBOW, "肘"),
    (re.compile(r"(左|右|双)?(?:肩膀|肩部|肩关节|肩)"), AREA_SHOULDER, "肩"),
    (re.compile(r"(左|右|双)?(?:膝盖|膝部|膝关节|膝|髌骨)"), AREA_KNEE, "膝"),
    (re.compile(r"(左|右|双)?(?:脚踝|踝部|踝关节|踝|崴了?脚|崴脚)"), AREA_ANKLE, "脚踝"),
    (re.compile(r"(?:下背|后背|背部|腰背|腰部|腰椎|腰)"), AREA_BACK, "腰背"),
)
# Body sites the library does not map. Any of them pauses the whole plan.
_UNMAPPED_WORDS = re.compile(
    r"髋|胯|臀部|臀|屁股|颈|脖子|胸口|胸部|胸痛|胸闷|肋|腹部|肚子|大腿|小腿|脚趾|脚跟|足底|脚掌|手指|手掌"
    r"|(?<!过)头(?:部|疼|痛|晕)"
)
_SYMPTOM = re.compile(
    r"疼|痛|酸|不适|不舒服|肿|麻(?!烦|辣|将|酱|油|婆|花|雀)|崴|扭伤|扭到|扭了|拉伤|受伤|抽筋"
    r"|炎|劳损|撕裂|骨折|脱臼|积液|突出|落枕"
)
_NON_SORE_SYMPTOM = re.compile(r"疼|痛|不适|不舒服|肿|麻(?!烦|辣|将|酱|油|婆|花|雀)|崴|扭|伤|抽筋|炎|劳损|撕裂|骨折|脱臼|积液|突出|落枕")
# Ordinary post-exercise muscle soreness is not an injury report.
_DOMS_CONTEXT = re.compile(
    r"练完|练腿后|训练后|运动后|健身后|第二天|隔天|延迟|那种酸|肌肉酸"
    r"|(?:昨天|前天|昨晚|刚才|刚)(?:去)?(?:练|跑|做|上了|撸铁)|练了.{0,8}之?后"
)
# Soreness in these places is muscle soreness; joints stay reportable.
_MUSCLE_SITES = re.compile(r"大腿|小腿|屁股|臀|腹|胳膊|手臂|胸肌|背阔|肌肉|全身|浑身")
# General questions ("膝盖疼的时候一般能做什么") are not a personal report.
_GENERAL_QUESTION = re.compile(r"(?:疼|痛|伤|酸|麻)的(?:时候|人|话)|一般|通常|常见|容易|这种情况的人|这类人")
_PERSONAL_NOW = re.compile(r"我|今天|现在|最近|还|仍|这两天|这几天|一直")
_SYMPTOM_WORDS = r"(?:疼痛|疼|痛|酸痛|酸|不适|不舒服|肿胀|肿|麻木|发麻|麻(?!烦|辣|将|酱|油|婆|花|雀)|受伤|外伤|淤青|变形)"
_NEGATED = re.compile(
    r"(?:不再|没有|没|不|无|并不)(?:任何|明显|什么|哪里|哪儿|什么地方|地方|部位)?(?:有|会|觉得)?" + _SYMPTOM_WORDS
    + r"(?:(?:[、/]|和|及|或)?" + _SYMPTOM_WORDS + r")*"
)
# Signals that must never be handled as an ordinary sore joint. They return
# the emergency template even if the intent layer did not mark high risk.
_EMERGENCY = re.compile(
    r"胸口(?:有点|有些|一阵|突然)?(?:痛|疼|闷|压|发紧|紧|憋)|胸痛|胸闷|冷汗|眼前发黑|眼前一黑|突然头晕|晕厥"
    r"|心跳(?:很|特别|非常|突然|一直)?(?:快|乱)|心跳加速|喘不上气|呼吸困难|嘴角歪|口角歪|说话含糊|口齿不清|半边|一侧(?:手|脚|身体)"
    r"|突然(?:一侧|半边)?(?:手|胳膊|腿)(?:抬不起来|没力气|无力)|晕倒|昏倒|昏迷|意识不清|失去意识|剧烈头痛|心慌|心悸"

    r"|脸[^，,。；;！？!?]{0,6}麻|面部[^，,。；;！？!?]{0,6}麻|嘴唇[^，,。；;！？!?]{0,4}紫|口唇[^，,。；;！？!?]{0,4}紫|发绀"
    r"|说话[^，,。；;！？!?]{0,4}(?:不利索|不清楚|含糊|大舌头)|口齿不清|舌头发硬"
    r"|喘(?:得)?(?:很|特别|非常)厉害|喘不过气|上不来气|透不过气")
_OTHER_PERSON = re.compile(
    r"朋友|同事|别人|他人|家人|我妈|我爸|孩子|老婆|老公|媳妇|妻子|丈夫|爱人|对象|女朋友|男朋友"
    r"|儿子|女儿|爸爸|妈妈|父亲|母亲|学员|客户|(?<!其)他|她"
)
_HYPOTHETICAL = re.compile(r"如果|假如|假设|万一|比如|例如")
_PAST = re.compile(
    r"以前|之前|曾经|过去|去年|上个月|上周|上次|疼过|痛过|伤过|前几天|前阵子|前段时间"
    r"|(?:\d+|[一二两三四五六七八九十几半]+)(?:个)?(?:天|周|星期|月|年)(?:以)?前"
)
_STILL = re.compile(r"今天|今日|现在|目前|刚(?:刚|才)?|突然|开始|还|仍|又|这两天|这几天|一直")
_RESOLVED = re.compile(r"(?:已经|已)?(?:完全)?(?:好了|恢复|痊愈|不疼了|不痛了)|已经完全不")
_RECORD = re.compile(r"档案|记录|健康筛查|病历")
_QUOTED = re.compile(r"“[^”]*”|「[^」]*」|\"[^\"\n]*\"")
_CLAUSE_SPLIT = re.compile(r"[，,。；;！？!?\n]")
_SENTENCE_SPLIT = re.compile(r"[。；;！？!?\n]")
_ADVICE = re.compile(
    r"建议|怎么(?:练|调|安排|处理|办|做)|如何|安排|调整|取舍|还能|能不能|能否|可以(?:做|练|上)?吗|可不可以"
    r"|要不要|行不行|还练|能练|能做|能上|暂停|保留|先不做|回归|恢复|替换|换成|减量|计划给"

    r"|(?:行|可以|能|好)(?:吗|么|不)|照做|照常|练啥|练什么|做什么")
_RECORD_QUERY = re.compile(r"看看|看一下|查一下|查查|查询|统计|几次|多少|列出|记录了|记了")
_TRAINING = re.compile(r"训练|练|计划|动作|健身|锻炼|运动|深蹲|卧推|硬拉|推举|划船|组|次数")
_CARE_NOW = re.compile(
    r"肿|淤青|瘀青|变形|崴|扭伤|扭了|扭到|摔|撞|踩空|外伤|骨折|不能(?:承重|走|动)|无法(?:承重|走路|活动|用力)"
    r"|麻(?!烦|辣|将|酱|油|婆|花|雀)|无力|静息|疼醒|痛醒|休息(?:时|的时候)?也(?:疼|痛)"
    r"|(?:晚上|夜里|夜间|半夜|睡觉|睡着|躺着|侧睡)[^，,。；;！？!?]{0,8}(?:疼|痛)"

    r"|使不上劲|没劲|发软|拿不稳|握不住|磕到|磕了|磕伤|碰伤|撞到|撞了|摔了|摔到")
_DOCTOR = re.compile(r"(?:医生|大夫|医嘱|康复师|治疗师|理疗师)")
_DOCTOR_REST = re.compile(r"休息|别练|不要练|不能练|停练|暂停|先别|制动|别动|卧床|不要运动|别运动|(?:不能|别|不要|避免|少)(?:负重|受力|用力|吃力)")
# Negated trauma/swelling mentions ("没扭到也没撞到") are not care signals.
_NEGATED_CARE = re.compile(r"(?:没有?|无|未|不)(?:被|有)?(?:扭|撞|摔|崴|磕|碰|肿)(?:到|伤|了|过)?")
_TIME_CAP = re.compile(r"(?:每次|一次|单次)?(?:总共|最多|不超过|只有|只想|只能|大约|约)?\s*(\d{1,3})\s*分钟")

SECONDS_PER_SET = 40
SETUP_SECONDS_PER_EXERCISE = 30

CARE_SIGNALS = (
    "出现明显肿胀、淤青或变形，或受伤部位无法承重、无法正常活动",
    "麻木、刺痛或无力，或休息、夜间也疼",
    "疼痛持续加重，或一周左右仍没有好转",
)
RETURN_CONDITIONS = (
    "日常活动和被暂停动作的起始姿势都不疼，并且持续约 48 小时以上，再恢复被暂停的动作",
    "恢复的第一次只用原计划重量的一半左右、组数减半，之后无痛再逐次回到原计划",
    "恢复过程中只要疼痛超过轻微、动作变形，或第二天更疼，就停下这个动作并找医生或康复治疗师评估",
)
DISCLAIMER = "以上是按动作库部位标注做的保守取舍，不是诊断；本次没有修改你的计划或任何记录。"


@dataclass(frozen=True)
class SymptomReport:
    quote: str
    area: str | None  # None means a body site the library cannot map
    site_label: str
    side: str
    source: str = "current"  # "record": cites a saved record; "doctor": relays a clinician restriction


@dataclass(frozen=True)
class PlanItem:
    day_of_week: int
    order_index: int
    exercise_name: str
    contraindications: tuple[str, ...]
    sets: int
    reps: str
    rest_seconds: int
    recommended_weight_kg: float | None = None


@dataclass(frozen=True)
class ItemDecision:
    item: PlanItem
    keep: bool
    reason: str
    kept_sets: int = 0


@dataclass
class ScopeDecision:
    reports: list[SymptomReport]
    areas: list[str]
    unmapped_sites: list[str]
    profile_areas: list[str]
    care_now: list[str]
    profile_injuries: list[str] | None
    time_cap_minutes: int | None
    has_plan: bool
    days: dict[int, list[ItemDecision]] = field(default_factory=dict)
    day_minutes: dict[int, float] = field(default_factory=dict)
    day_time_insufficient: dict[int, bool] = field(default_factory=dict)
    mentioned: list[tuple[str, bool, str]] = field(default_factory=list)
    doctor: list[str] = field(default_factory=list)
    doctor_rest: bool = False
    detector: dict | None = None
    substitution_asked: bool = False

    def as_data(self) -> dict:
        return {
            "version": 1,
            "areas": self.areas,
            "unmapped_sites": self.unmapped_sites,
            "profile_areas": self.profile_areas,
            "profile_injuries": self.profile_injuries,
            "doctor_instructions": self.doctor,
            "doctor_rest": self.doctor_rest,
            "detector": self.detector,
            "care_now": self.care_now,
            "time_cap_minutes": self.time_cap_minutes,
            "has_plan": self.has_plan,
            "reports": [r.__dict__ for r in self.reports],
            "days": {
                str(day): [
                    {
                        "exercise": d.item.exercise_name,
                        "keep": d.keep,
                        "reason": d.reason,
                        "planned_sets": d.item.sets,
                        "kept_sets": d.kept_sets,
                        "contraindications": list(d.item.contraindications),
                    }
                    for d in items
                ]
                for day, items in self.days.items()
            },
            "day_minutes": {str(k): round(v, 1) for k, v in self.day_minutes.items()},
            "day_time_insufficient": {str(k): v for k, v in self.day_time_insufficient.items()},
            "substitution_asked": self.substitution_asked,
            "mentioned_exercises": [
                {"exercise": n, "conflicts": c, "reason": r} for n, c, r in self.mentioned
            ],
        }


def _personal_clause(clause: str) -> bool:
    return not (_OTHER_PERSON.search(clause) or _HYPOTHETICAL.search(clause))


def _positive_symptom(clause: str) -> bool:
    return bool(_SYMPTOM.search(_NEGATED.sub("", clause)))


def _sites(clause: str) -> list[tuple[str | None, str, str]]:
    found: list[tuple[str | None, str, str]] = []
    for pattern, area, label in _AREA_WORDS:
        for match in pattern.finditer(clause):
            side = match.group(1) if match.groups() and match.group(1) else ""
            found.append((area, label, side))
    if not found:
        for match in _UNMAPPED_WORDS.finditer(clause):
            found.append((None, match.group(), ""))
    return found


def current_symptom_reports(message: str) -> list[SymptomReport]:
    """User's own current symptoms, with body sites bound inside a sentence.

    Past-only, resolved, negated, hypothetical, quoted and other people's
    symptoms are excluded. A clause without a body site may bind to the
    nearest site named earlier in the same sentence ("左膝走路正常，深蹲还会疼").
    """
    if not isinstance(message, str) or not message.strip():
        return []
    text = _QUOTED.sub(lambda m: " " * len(m.group()), message)
    reports: list[SymptomReport] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        if _OTHER_PERSON.search(sentence) and not re.search(r"我自己|我(?:今天|现在)", sentence):
            continue
        last_site: tuple[str | None, str, str] | None = None
        for clause in _CLAUSE_SPLIT.split(sentence):
            clause = clause.strip()
            if not clause:
                continue
            sites = _sites(clause)
            if sites:
                last_site = sites[0]
            if (sites and _DOCTOR.search(clause) and _DOCTOR_REST.search(clause)
                    and not _HYPOTHETICAL.search(clause)):
                # "康复师说我膝盖这周不能负重": a relayed restriction is a report
                # even without a symptom word.
                for area, label, side in sites:
                    reports.append(SymptomReport(quote=clause, area=area, site_label=label, side=side, source="doctor"))
                continue
            if not _personal_clause(clause) or not _positive_symptom(clause):
                continue
            positive = _NEGATED.sub("", clause)
            if (not _NON_SORE_SYMPTOM.search(positive) and _DOMS_CONTEXT.search(sentence)
                    and not any(area for area, _, _ in sites)
                    and (_MUSCLE_SITES.search(clause) or not sites)):
                continue
            if _GENERAL_QUESTION.search(clause) and not _PERSONAL_NOW.search(clause):
                continue
            if _RESOLVED.search(clause):
                continue
            if _PAST.search(clause) and not _STILL.search(clause):
                continue
            source = "record" if (_RECORD.search(clause) and not _STILL.search(clause)) else "current"
            bound = sites or ([last_site] if last_site else [])
            if not bound:
                reports.append(SymptomReport(quote=clause, area=None, site_label="", side="", source=source))
                continue
            for area, label, side in bound:
                reports.append(SymptomReport(quote=clause, area=area, site_label=label, side=side, source=source))
    # Keep unlocated reports only when nothing in the message is located.
    located = [r for r in reports if r.site_label]
    return located or reports


def inherit_sites(reports: list[SymptomReport], prior_user_messages: Iterable[str]) -> list[SymptomReport]:
    """A follow-up without a site ("补充：约2分…") reuses the latest prior site."""
    if not reports or any(r.site_label for r in reports):
        return reports
    for prior in prior_user_messages:
        located = [r for r in current_symptom_reports(prior) if r.site_label]
        if located:
            quote = reports[0].quote
            return [SymptomReport(quote=quote, area=r.area, site_label=r.site_label, side=r.side, source=r.source) for r in located]
    return reports


def time_cap_minutes(message: str) -> int | None:
    caps = [int(m.group(1)) for m in _TIME_CAP.finditer(message or "") if 0 < int(m.group(1)) <= 180]
    return min(caps) if caps else None


def care_now_terms(message: str) -> list[str]:
    text = _NEGATED_CARE.sub("", _NEGATED.sub("", _QUOTED.sub("", message or "")))
    return list(dict.fromkeys(m.group() for m in _CARE_NOW.finditer(text)))


_EMERGENCY_TERM = (
    r"(?:胸口[^，,。；;！？!?]{0,4}(?:痛|疼|闷|压|发紧|紧|憋)|胸闷|胸痛|冷汗|喘不(?:上|过)来?气|呼吸困难"
    r"|上不来气|透不过气|心慌|心悸|头晕|眼前发黑|眼前一黑|嘴唇[^，,。；;！？!?]{0,4}紫)"
)
# "也没胸口发紧或喘不过气" denies the signals. Only a negator immediately
# before the term counts, so "没一会儿就胸口疼" is still an emergency.
_NEGATED_EMERGENCY = re.compile(
    r"(?:没有?|无|未|并没有?)(?:有|出现|觉得|感到|什么)?" + _EMERGENCY_TERM
    + r"(?:(?:或|和|、|/|也没有?|也无|或者)" + _EMERGENCY_TERM + r")*"
)


def emergency_signals(message: str) -> list[str]:
    text = _QUOTED.sub("", message or "")
    text = "。".join(
        part for part in _SENTENCE_SPLIT.split(text)
        if not _OTHER_PERSON.search(part) and not _HYPOTHETICAL.search(part)
    )
    text = _NEGATED_EMERGENCY.sub("", text)
    text = _NEGATED.sub("", text)
    text = re.sub(r"(?:没有|没|无|不)(?:任何)?(?:胸闷|冷汗|心慌|心悸|呼吸困难)", "", text)
    return list(dict.fromkeys(m.group() for m in _EMERGENCY.finditer(text)))


def seeks_training_advice(message: str) -> bool:
    return bool(_ADVICE.search(message or ""))


def states_recovery(message: str) -> bool:
    text = message or ""
    return bool(_RESOLVED.search(text) or re.search(r"(?:不|没)(?:再)?(?:疼|痛)了", text))


def doctor_instructions(message: str) -> list[str]:
    """Clauses where the user relays a clinician's instruction, verbatim."""
    text = _QUOTED.sub("", message or "")
    out = []
    for sentence in _SENTENCE_SPLIT.split(text):
        if _OTHER_PERSON.search(sentence.replace("医生", "").replace("大夫", "")):
            continue
        for clause in _CLAUSE_SPLIT.split(sentence):
            clause = clause.strip()
            if clause and _DOCTOR.search(clause) and not _HYPOTHETICAL.search(clause):
                out.append(clause)
    return out


def _episode_priors(message: str, prior_user_messages: list[str]) -> list[str]:
    """Prior turns of the same symptom episode: back to the latest one with a report.

    Used only when this turn names no site of its own, so a follow-up keeps
    earlier swelling, trauma or doctor's instructions.
    """
    if any(r.site_label for r in current_symptom_reports(message)) or states_recovery(message):
        return []
    episode = []
    for prior in prior_user_messages:
        episode.append(prior)
        if current_symptom_reports(prior):
            return episode
    return []


def is_symptom_training_request(message: str, *, has_plan: bool, continuing: bool = False) -> bool:
    """A training-advice request with a current report (or continuing one).

    Pure record queries ("手腕疼，帮我看看上周练了几次") stay on the regular
    path so the question is still answered.
    """
    if continuing:
        return seeks_training_advice(message) and not states_recovery(message)
    if not seeks_training_advice(message) and _RECORD_QUERY.search(message or ""):
        return False
    if not current_symptom_reports(message):
        return False
    return bool(_TRAINING.search(message)) or has_plan


def _exercise_minutes(sets: int, rest_seconds: int) -> float:
    if sets <= 0:
        return 0.0
    return (sets * SECONDS_PER_SET + (sets - 1) * max(rest_seconds, 0) + SETUP_SECONDS_PER_EXERCISE) / 60


def _fit_to_cap(kept: list[ItemDecision], cap: int | None) -> tuple[list[ItemDecision], float, bool]:
    """Reduce sets (minimum 1), then drop trailing exercises, to fit the cap."""
    items = [ItemDecision(d.item, True, d.reason, d.item.sets) for d in kept]
    total = lambda xs: sum(_exercise_minutes(d.kept_sets, d.item.rest_seconds) for d in xs if d.keep)
    if cap is None:
        return items, total(items), False
    while total(items) > cap:
        reducible = [i for i, d in enumerate(items) if d.keep and d.kept_sets > 1]
        if reducible:
            i = max(reducible, key=lambda j: (items[j].kept_sets, j))
            d = items[i]
            items[i] = ItemDecision(d.item, True, d.reason, d.kept_sets - 1)
            continue
        active = [i for i, d in enumerate(items) if d.keep]
        if not active:
            break
        i = active[-1]
        d = items[i]
        items[i] = ItemDecision(d.item, False, f"每次{cap}分钟内放不下，本周先不做", 0)
    insufficient = bool(kept) and not any(d.keep for d in items)
    return items, total(items), insufficient


def decide_scope(
    message: str,
    plan_items: list[PlanItem] | None,
    *,
    profile_injuries: Iterable[str] | None = (),
    prior_user_messages: Iterable[str] = (),
    catalog: dict[str, tuple[str, ...]] | None = None,
    continuing: bool = False,
    facts=None,
    previous_scope: dict | None = None,
) -> ScopeDecision:
    recorded = None if profile_injuries is None else [str(x).strip() for x in profile_injuries if str(x).strip() and x != "none"]
    prior_user_messages = list(prior_user_messages)
    cap = time_cap_minutes(message)
    if facts is not None:
        # docs/agent/16: understanding comes from validated model facts; the
        # pause/keep mapping and reply below stay deterministic.
        from app.services.agent_symptom_facts import CARE_LABEL, REGION_AREA, REGION_LABEL, SIDE_LABEL
        reports = [SymptomReport(
            quote=r["quote"], area=REGION_AREA.get(r["region"]),
            site_label=r.get("site_text") or REGION_LABEL.get(r["region"], ""),
            side=SIDE_LABEL.get(r.get("side", "none"), ""), source="current",
        ) for r in facts.reports]
        care = [CARE_LABEL[c] for c in facts.care]
        doctor = [facts.clinician_quote] if facts.clinician_quote else []
        doctor_rest = facts.clinician_restricts_load
        cap = facts.time_cap_minutes or cap
        carry = continuing and previous_scope and not facts.resolved_now
        if carry:
            if not reports:
                reports = [SymptomReport(**r) for r in previous_scope.get("reports", [])]
            care = list(dict.fromkeys([*care, *previous_scope.get("care_now", [])]))
            doctor = list(dict.fromkeys([*doctor, *previous_scope.get("doctor_instructions", [])]))
            doctor_rest = doctor_rest or bool(previous_scope.get("doctor_rest"))
            cap = cap or previous_scope.get("time_cap_minutes")
        if not reports:
            # A symptom without a located report is treated as an unclear site.
            reports = [SymptomReport(quote=(message or "")[:80], area=None, site_label="", side="")]
    else:
        reports = inherit_sites(current_symptom_reports(message), prior_user_messages)
        if not reports and continuing:
            for prior in prior_user_messages:
                located = current_symptom_reports(prior)
                if located:
                    reports = located
                    break
        episode = _episode_priors(message, prior_user_messages)
        care = list(dict.fromkeys([*care_now_terms(message), *(t for m in episode for t in care_now_terms(m))]))
        doctor = list(dict.fromkeys([*doctor_instructions(message), *(c for m in episode for c in doctor_instructions(m))]))
        doctor_rest = any(_DOCTOR_REST.search(c) for c in doctor)
        if continuing and previous_scope and not cap:
            cap = previous_scope.get("time_cap_minutes")
    areas = list(dict.fromkeys(r.area for r in reports if r.area))
    unmapped = list(dict.fromkeys(r.site_label or "未说明部位" for r in reports if r.area is None))
    profile_areas = [
        a for a in dict.fromkeys(
            area
            for value in recorded or []
            for area in ([value] if value in KNOWN_AREAS else [x[0] for x in _sites(value) if x[0]])
        )
        if a not in areas
    ]
    decision = ScopeDecision(
        reports=reports,
        areas=areas,
        unmapped_sites=unmapped,
        profile_areas=profile_areas,
        care_now=care,
        doctor=doctor,
        profile_injuries=recorded,
        time_cap_minutes=cap,
        has_plan=bool(plan_items),
        substitution_asked=bool(re.search(r"换成|替换|代替|改成|改做|换个", message or "")),
    )
    decision.doctor_rest = bool(doctor_rest)
    pause_all = bool(unmapped) or bool(decision.care_now) or doctor_rest
    by_day: dict[int, list[PlanItem]] = {}
    for item in sorted(plan_items or [], key=lambda x: (x.day_of_week, x.order_index)):
        by_day.setdefault(item.day_of_week, []).append(item)
    for day, items in by_day.items():
        initial: list[ItemDecision] = []
        for item in items:
            current_conflict = [a for a in areas if a in item.contraindications]
            saved_conflict = [a for a in profile_areas if a in item.contraindications]
            if doctor_rest:
                initial.append(ItemDecision(item, False, "按医生的休息嘱咐先停"))
            elif decision.care_now:
                initial.append(ItemDecision(item, False, "就医评估前整份计划先停"))
            elif pause_all:
                initial.append(ItemDecision(item, False, f"当前不适部位（{'、'.join(unmapped)}）无法按动作库判断"))
            elif current_conflict:
                initial.append(ItemDecision(item, False, f"会用到当前不适的{'、'.join(current_conflict)}"))
            elif saved_conflict:
                initial.append(ItemDecision(item, False, f"与健康档案中的{'、'.join(saved_conflict)}限制冲突"))
            else:
                initial.append(ItemDecision(item, True, "动作库标注不涉及当前不适部位，可按原计划做", item.sets))
        kept = [d for d in initial if d.keep]
        fitted, minutes, insufficient = _fit_to_cap(kept, decision.time_cap_minutes)
        fitted_iter = iter(fitted)
        merged = [next(fitted_iter) if d.keep else d for d in initial]
        decision.days[day] = merged
        decision.day_minutes[day] = minutes
        decision.day_time_insufficient[day] = insufficient
    if catalog:
        planned = {i.exercise_name for i in plan_items or []}
        for name, contraindications in catalog.items():
            if name in planned or name not in message:
                continue
            conflicts = pause_all or any(a in contraindications for a in areas)
            reason = (
                "同样会用到当前不适部位，现在也先不做" if conflicts and not pause_all
                else "当前不适部位无法按动作库判断，现在也先不做" if conflicts
                else "动作库标注不涉及当前不适部位；疼痛期间不新增或替换动作，等恢复后再调整计划"
            )
            decision.mentioned.append((name, conflicts, reason))
    return decision


_WEEKDAYS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _site_text(decision: ScopeDecision) -> str:
    sided = {r.site_label for r in decision.reports if r.site_label and r.side}
    labels = list(dict.fromkeys(
        f"{r.side}{r.site_label}" for r in decision.reports
        if r.site_label and (r.side or r.site_label not in sided)
    ))
    return "、".join(labels) or "不适部位"


def render_reply(decision: ScopeDecision) -> str:
    lines: list[str] = []
    site = _site_text(decision)
    if decision.care_now:
        lines.append(
            f"你提到了{'、'.join(decision.care_now)}，建议尽快（24 小时内）就医评估；"
            "在评估之前，整份训练计划都先停，不要带着疼痛减重“试一下”。"
        )
    elif decision.reports and all(r.source == "doctor" for r in decision.reports):
        lines.append(f"医嘱对{site}有限制，先停会用到这个部位的动作，不要自行减重“试一下”。")
    elif decision.reports and all(r.source == "record" for r in decision.reports):
        lines.append(f"你提到记录里的{site}问题，先停会用到这个部位的动作，不要带着疼痛减重“试一下”。")
    else:
        lines.append(f"{site}现在不舒服，先停会用到这个部位的动作，不要带着疼痛减重“试一下”。")
    if decision.doctor:
        quoted = "；".join(f"“{c}”" for c in decision.doctor)
        lines.append(f"你提到{quoted}，以医生的嘱咐为准；下面的取舍不能代替医嘱，两者冲突时按医嘱。")
    if decision.profile_injuries is not None:
        if decision.profile_injuries:
            lines.append(f"健康档案记录的伤病：{'、'.join(decision.profile_injuries)}；今天新说的情况同样计入。")
        else:
            lines.append("健康档案里没有记录伤病，以你今天说的情况为准。")
    if not decision.has_plan:
        lines.append("我没有查到你当前的训练计划，所以没法逐个动作判断。请先暂停所有会引起疼痛的训练。")
    else:
        if decision.unmapped_sites and not decision.care_now:
            lines.append(
                f"动作库没有对“{'、'.join(decision.unmapped_sites)}”做部位标注，"
                "无法判断哪些动作不受影响，所以下面整份计划都先停。"
            )
        if any(d.keep for items in decision.days.values() for d in items):
            lines.append(
                "“照常”只表示动作库没有把它标为会用到当前不适部位，不代表保证安全；"
                "练的时候如果不适部位被牵扯到或疼起来，立刻停下这个动作。"
            )
        cap = decision.time_cap_minutes
        for day, items in decision.days.items():
            label = _WEEKDAYS.get(day, f"第{day}天")
            keep = [d for d in items if d.keep]
            pause = [d for d in items if not d.keep]
            lines.append(f"\n【{label}】")
            for d in keep:
                sets = f"{d.kept_sets} 组" + ("" if d.kept_sets == d.item.sets else f"（原计划 {d.item.sets} 组）")
                weight = f"，{d.item.recommended_weight_kg:g}kg" if d.item.recommended_weight_kg else ""
                lines.append(f"- 照常：{d.item.exercise_name} {sets} × {d.item.reps}{weight}，组间休息 {d.item.rest_seconds} 秒")
            for d in pause:
                lines.append(f"- 先停：{d.item.exercise_name}——{d.reason}")
            if decision.day_time_insufficient.get(day):
                lines.append(f"- 每次 {cap} 分钟连一组可照常的动作都放不下，这一天建议休息，不必勉强凑。")
            elif keep and cap:
                lines.append(f"- 估算约 {decision.day_minutes[day]:.0f} 分钟（按每组约 {SECONDS_PER_SET} 秒加计划休息时间），在 {cap} 分钟以内。")
            elif not keep:
                lines.append("- 这一天的动作都先停，建议休息。")
    for name, conflicts, reason in decision.mentioned:
        lines.append(f"\n你提到的{name}：{reason}。")
    if decision.substitution_asked and not decision.mentioned:
        lines.append("\n你问到的替换动作：疼痛期间不新增或替换动作，也不凭名称判断它是否安全，等恢复后再调整计划。")
    lines.append("\n出现下面任何一种情况，请尽快就医：")
    lines.extend(f"- {s}" for s in CARE_SIGNALS)
    lines.append("\n什么时候恢复被暂停的动作：")
    lines.extend(f"- {s}" for s in RETURN_CONDITIONS)
    lines.append(f"\n{DISCLAIMER}")
    return "\n".join(lines)


async def load_symptom_scope(
    db,
    *,
    user_id: str,
    message: str,
    prior_user_messages: Iterable[str] = (),
    continuing: bool = False,
    facts=None,
    previous_scope: dict | None = None,
) -> ScopeDecision:
    """Read the active plan, profile injuries and library areas, then decide."""
    from sqlalchemy import or_, select

    from app.models.exercise import Exercise
    from app.models.profile import UserProfile
    from app.models.workout import PlannedExercise, WorkoutPlan
    from app.services.personalized_planner import is_exercise_safe_for_areas

    def areas_of(exercise: Exercise) -> tuple[str, ...]:
        # Same rule the domain planner uses: declared contraindications plus
        # name keywords (for example 硬拉 → 腰背部).
        return tuple(a for a in KNOWN_AREAS if not is_exercise_safe_for_areas(exercise, {a}))

    plan = await db.scalar(
        select(WorkoutPlan)
        .where(WorkoutPlan.user_id == user_id, WorkoutPlan.is_active.is_(True))
        .order_by(WorkoutPlan.created_at.desc())
        .limit(1)
    )
    items: list[PlanItem] = []
    if plan is not None:
        rows = (await db.execute(
            select(PlannedExercise, Exercise)
            .join(Exercise, Exercise.id == PlannedExercise.exercise_id)
            .where(PlannedExercise.plan_id == plan.id)
        )).all()
        for planned, exercise in rows:
            items.append(PlanItem(
                day_of_week=int(planned.day_of_week),
                order_index=int(planned.order_index or 0),
                exercise_name=exercise.name_zh,
                contraindications=areas_of(exercise),
                sets=int(planned.sets or 0),
                reps=str(planned.reps or ""),
                rest_seconds=int(planned.rest_seconds or 0),
                recommended_weight_kg=planned.recommended_weight_kg,
            ))
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    catalog_rows = (await db.execute(
        select(Exercise).where(
            Exercise.is_active.is_(True),
            or_(Exercise.owner_id.is_(None), Exercise.owner_id == user_id),
        )
    )).scalars().all()
    catalog = {row.name_zh: areas_of(row) for row in catalog_rows if row.name_zh}
    return decide_scope(
        message,
        items,
        profile_injuries=(profile.injuries or []) if profile is not None else None,
        prior_user_messages=prior_user_messages,
        catalog=catalog,
        continuing=continuing,
        facts=facts,
        previous_scope=previous_scope,
    )


async def previous_reply_was_symptom_scope(db, *, conversation_id: str, run_id: str) -> bool:
    """True when the latest assistant message in this conversation is ours."""
    from sqlalchemy import select

    from app.models.agent import AgentMessage

    data = await db.scalar(
        select(AgentMessage.content_data)
        .where(
            AgentMessage.conversation_id == conversation_id,
            AgentMessage.role == "assistant",
            AgentMessage.run_id != run_id,
        )
        .order_by(AgentMessage.created_at.desc())
        .limit(1)
    )
    return isinstance(data, dict) and data.get("guardrail") == "symptom_deterministic_scope"


async def previous_scope_data(db, *, conversation_id: str, run_id: str) -> dict | None:
    """The symptom-scope decision of the latest assistant reply, if it was ours."""
    from sqlalchemy import select

    from app.models.agent import AgentMessage

    data = await db.scalar(
        select(AgentMessage.content_data)
        .where(
            AgentMessage.conversation_id == conversation_id,
            AgentMessage.role == "assistant",
            AgentMessage.run_id != run_id,
        )
        .order_by(AgentMessage.created_at.desc())
        .limit(1)
    )
    if isinstance(data, dict) and data.get("guardrail") == "symptom_deterministic_scope":
        scope = data.get("symptom_scope")
        return scope if isinstance(scope, dict) else {}
    return None
