"""SCOPE-20261025-01: deterministic training scope for current pain (pure)."""
import pytest

from app.services.agent_symptom_scope import (
    AREA_ANKLE,
    AREA_BACK,
    AREA_KNEE,
    AREA_SHOULDER,
    AREA_WRIST_ELBOW,
    CARE_SIGNALS,
    RETURN_CONDITIONS,
    PlanItem,
    care_now_terms,
    current_symptom_reports,
    decide_scope,
    emergency_signals,
    is_symptom_training_request,
    render_reply,
    seeks_training_advice,
    doctor_instructions,
    time_cap_minutes,
)

BENCH = PlanItem(1, 0, "卧推", (AREA_SHOULDER, AREA_WRIST_ELBOW), 3, "10", 90, 40.0)
SQUAT = PlanItem(1, 1, "深蹲", (AREA_KNEE, AREA_BACK, AREA_ANKLE), 3, "8", 120, 60.0)
ADDUCTION = PlanItem(3, 0, "器械髋内收", (AREA_KNEE,), 3, "12", 60)
CURL = PlanItem(3, 1, "哑铃弯举", (AREA_WRIST_ELBOW,), 3, "12", 60, 8.0)
PLAN = [BENCH, SQUAT, ADDUCTION, CURL]


def areas(message, prior=()):
    return decide_scope(message, PLAN, prior_user_messages=prior).areas


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("档案没有伤病记录，但我今天膝盖突然疼。想要一周每次15分钟的训练建议", [AREA_KNEE]),
        ("今天手腕开始有点疼，请说说如何调整训练", [AREA_WRIST_ELBOW]),
        ("今天提重包后右肩开始疼，大约2分，只有抬手过头疼", [AREA_SHOULDER]),
        ("今天脚踝突然疼，请先核对已存训练计划", [AREA_ANKLE]),
        ("昨天搬纸箱后右肘伸直时会酸痛，今天还在。今晚原计划还能做吗", [AREA_WRIST_ELBOW]),
        ("今天踩空崴了脚，右脚踝肿了，走路能踩地但明显疼。能不能把今晚深蹲减轻一点", [AREA_ANKLE]),
        ("档案里的膝部不适还没完全好，今天左肩又开始不舒服。给训练建议", [AREA_KNEE, AREA_SHOULDER]),
        ("现在左膝走路正常，深蹲还会疼；今天右手腕又开始疼，结合计划", [AREA_KNEE, AREA_WRIST_ELBOW]),
        ("今天腰有点疼，弯腰捡东西更明显，训练怎么调整", [AREA_BACK]),
    ],
)
def test_current_personal_sites_map_to_library_areas(message, expected):
    assert areas(message) == expected
    assert is_symptom_training_request(message, has_plan=True)


@pytest.mark.parametrize(
    "message",
    [
        "以前膝盖疼过，但今天已经完全不疼，走路和上下楼都正常。最近工作累，请给训练建议",
        "上周右手腕有过疼痛，现在已经完全不疼，撑地和提东西都正常。给省时减量安排",
        "同事说“今天手腕疼，握重物也疼”，那是他的情况。我自己没有疼痛或功能受限，只是工作很累。",
        "接下来三天要赶项目，每次最多18分钟。我没有疼痛，就是睡得少。",
        "如果以后膝盖疼，训练要怎么调整？",
        "我朋友今天肩膀疼，他该怎么练？",
        "2026年10月7日的饮食记录里蛋白质是多少？",
    ],
)
def test_past_resolved_hypothetical_others_and_no_pain_do_not_trigger(message):
    assert current_symptom_reports(message) == []
    assert not is_symptom_training_request(message, has_plan=True)


def test_cited_record_is_labelled_as_record_not_new_pain():
    decision = decide_scope("膝部疼痛记录已经在档案里。这周加班很累，每次最多20分钟，请结合现有计划给建议", PLAN)
    assert decision.areas == [AREA_KNEE] and decision.reports[0].source == "record"
    reply = render_reply(decision)
    assert reply.startswith("你提到记录里的膝问题") and "现在不舒服" not in reply
    still = decide_scope("档案里的膝部不适还没完全好，训练怎么调整", PLAN)
    assert still.reports[0].source == "current"


def test_overhead_wording_is_not_a_head_site():
    reports = current_symptom_reports("今天右肩开始疼，只有抬手过头疼，平板卧推是不是过头推举？")
    assert {r.area for r in reports} == {AREA_SHOULDER}


def test_follow_up_without_site_inherits_latest_prior_site():
    prior = ["今天右肩开始疼，我工作累，想要一周每次最多10分钟的临时训练建议"]
    follow = "补充：今天取高处物品后开始，约2分，无肿胀麻木，走路和拿水杯不疼，抬手过头才疼。仍是一周每次最多10分钟"
    decision = decide_scope(follow, PLAN, prior_user_messages=prior)
    assert decision.areas == [AREA_SHOULDER]
    assert decision.care_now == []  # 无肿胀麻木 is a negation


def test_unmapped_site_pauses_every_planned_exercise():
    decision = decide_scope("今天右髋开始疼，训练怎么安排", PLAN)
    assert decision.unmapped_sites == ["髋"]
    assert all(not d.keep for items in decision.days.values() for d in items)
    assert "无法判断哪些动作不受影响" in render_reply(decision)


def test_wrist_pause_keeps_only_library_safe_exercises_unchanged():
    decision = decide_scope("今天右手腕开始疼，撑地和握重物才疼，按已存计划给取舍", PLAN)
    day1 = {d.item.exercise_name: d for d in decision.days[1]}
    day3 = {d.item.exercise_name: d for d in decision.days[3]}
    assert not day1["卧推"].keep and day1["深蹲"].keep and day1["深蹲"].kept_sets == 3
    assert day3["器械髋内收"].keep and not day3["哑铃弯举"].keep
    reply = render_reply(decision)
    assert "照常：深蹲 3 组 × 8，60kg" in reply
    assert "先停：卧推" in reply and "先停：哑铃弯举" in reply


def test_saved_profile_injury_also_pauses_conflicting_exercises():
    decision = decide_scope("今天右手腕开始疼，训练怎么调整", PLAN, profile_injuries=["膝关节"])
    day1 = {d.item.exercise_name: d for d in decision.days[1]}
    assert not day1["深蹲"].keep and "健康档案" in day1["深蹲"].reason


def test_time_cap_reduces_sets_then_drops_and_reports_insufficient():
    decision = decide_scope("今天右手腕开始疼，这周每次最多4分钟，按计划给安排", PLAN)
    # Squat 2 sets = 2×40s + 120s rest + 30s setup = 3.8 min ≤ 4; 3 sets = 7.8 min.
    squat = next(d for d in decision.days[1] if d.item.exercise_name == "深蹲")
    assert squat.keep and squat.kept_sets == 2
    assert decision.day_minutes[1] <= 4
    tight = decide_scope("今天右手腕开始疼，每次最多1分钟，按计划给安排", PLAN)
    assert all(tight.day_time_insufficient.values())
    assert "连一组可照常的动作都放不下" in render_reply(tight)


def test_time_cap_parsing():
    assert time_cap_minutes("一周每次最多12分钟的临时训练建议") == 12
    assert time_cap_minutes("每次总共最多4分钟，包含准备和休息") == 4
    assert time_cap_minutes("今晚能练吗") is None


def test_care_terms_respect_negation():
    assert care_now_terms("没有肿胀、麻木和外伤，走路正常") == []
    assert care_now_terms("无肿胀麻木") == []
    assert set(care_now_terms("今天踩空崴了脚，右脚踝肿了")) >= {"崴", "肿"}


def test_swelling_or_trauma_leads_with_prompt_care_and_no_trial():
    reply = render_reply(decide_scope("今天踩空崴了脚，右脚踝肿了，能不能把今晚深蹲减轻一点继续？", PLAN))
    assert reply.startswith("你提到了") and "尽快（24 小时内）就医评估" in reply
    assert "不要带着疼痛减重“试一下”" in reply
    assert "先停：深蹲" in reply and "照常" not in reply  # whole plan waits for care


def test_profile_free_text_injury_maps_to_area():
    decision = decide_scope("今天右手腕开始疼，训练怎么调整", PLAN, profile_injuries=["左膝不适"])
    assert decision.profile_areas == [AREA_KNEE]


def test_site_labels_are_not_duplicated():
    reply = render_reply(decide_scope("今天崴了脚，右脚踝疼，训练怎么办", PLAN))
    assert "脚踝、右脚踝" not in reply


def test_mentioned_substitute_is_judged_but_never_recommended():
    catalog = {"哑铃杯式深蹲": (AREA_KNEE, AREA_BACK, AREA_ANKLE), "卧推": (AREA_SHOULDER, AREA_WRIST_ELBOW)}
    decision = decide_scope("今天右肩开始疼，能把卧推换成哑铃杯式深蹲吗？", PLAN, catalog=catalog)
    assert decision.mentioned == [("哑铃杯式深蹲", False, "动作库标注不涉及当前不适部位；疼痛期间不新增或替换动作，等恢复后再调整计划")]
    wrist = decide_scope("今天右手腕开始疼，能把卧推换成哑铃杯式深蹲吗？", PLAN,
                         catalog={"哑铃杯式深蹲": (AREA_WRIST_ELBOW,)})
    assert wrist.mentioned[0][1] is True


def test_unknown_substitute_is_not_judged_by_name():
    reply = render_reply(decide_scope("今天右手腕开始疼，能把卧推换成胸前抱哑铃的高脚杯深蹲吗？", PLAN, catalog={}))
    assert "疼痛期间不新增或替换动作" in reply


def test_keep_lines_carry_no_safety_guarantee():
    reply = render_reply(decide_scope("今天右手腕开始疼，按计划给取舍", PLAN))
    assert "不代表保证安全" in reply and "立刻停下这个动作" in reply


def test_no_plan_gives_general_pause():
    decision = decide_scope("今天膝盖开始疼，训练怎么办", [])
    reply = render_reply(decision)
    assert not decision.has_plan and "没有查到你当前的训练计划" in reply


def test_reply_always_has_fixed_care_return_and_disclaimer():
    reply = render_reply(decide_scope("今天膝盖开始疼，训练怎么办", PLAN))
    for line in (*CARE_SIGNALS, *RETURN_CONDITIONS):
        assert line in reply
    assert "不是诊断" in reply and "没有修改你的计划" in reply
    for forbidden in ("试试", "先试", "只做不引起疼痛的部分", "不痛就可以继续"):
        assert forbidden not in reply


@pytest.mark.parametrize(
    "message",
    [
        "我现在在上海，跑步后胸口压着疼，已经十分钟没缓解，还冒冷汗、喘不上气。",
        "我在上海，刚刚突然左侧嘴角歪了，左手抬不起来，说话也含糊。",
    ],
)
def test_emergency_backstop(message):
    assert emergency_signals(message)


def test_ordinary_joint_pain_is_not_emergency():
    assert emergency_signals("今天右手腕开始疼，握重物抬不起来，训练怎么调整") == []


def test_as_data_is_json_ready():
    import json
    data = decide_scope("今天右手腕开始疼，每次最多12分钟", PLAN).as_data()
    assert json.loads(json.dumps(data, ensure_ascii=False))["areas"] == [AREA_WRIST_ELBOW]


# Gaps found by probe-cases.json first run (12/20); fixed and pinned here.
@pytest.mark.parametrize(
    "message",
    [
        "我两周前肩膀拉伤了，现在完全好了，想恢复训练，怎么加量",  # PB09 resolved
        "老婆膝盖疼，她能做深蹲吗",  # PB10 other person
        "膝盖不疼，就是大腿酸，练完腿第二天那种酸，今天还能练吗",  # PB11 soreness
        "膝盖疼的时候一般可以做哪些有氧？",  # PB14 general question
    ],
)
def test_probe_non_reports(message):
    assert current_symptom_reports(message) == []


def test_probe_pure_record_query_keeps_regular_path():  # PB13
    message = "我手腕疼，帮我看看上周练了几次"
    assert current_symptom_reports(message)
    assert not is_symptom_training_request(message, has_plan=True)


def test_probe_chest_tightness_is_emergency_even_without_pain_word():  # PB15
    assert emergency_signals("最近胸口闷，练卧推的时候特别明显")
    assert emergency_signals("卧推时杠铃放到胸口位置对吗") == []


def test_probe_condition_name_without_pain_word():  # PB19
    assert areas("右手腕腱鞘炎，医生说可以正常练下肢，今天胸日怎么安排") == [AREA_WRIST_ELBOW]


def test_probe_follow_up_without_symptom_words_continues():  # PB20
    prior = ["今天膝盖开始疼"]
    follow = "对了，我计划今天是练上肢的，那还能练吗"
    assert current_symptom_reports(follow) == [] and seeks_training_advice(follow)
    assert is_symptom_training_request(follow, has_plan=True, continuing=True)
    decision = decide_scope(follow, PLAN, prior_user_messages=prior, continuing=True)
    assert decision.areas == [AREA_KNEE]
    assert not is_symptom_training_request("现在已经不疼了，那还能练吗", has_plan=True, continuing=True)


def test_bare_statement_triggers_but_other_person_does_not():
    assert is_symptom_training_request("今天膝盖开始疼", has_plan=True)
    assert not is_symptom_training_request("今天膝盖开始疼", has_plan=False)


# v2 (SCOPE-20261026): gaps found by acceptance set v1; wording differs from v1.
def test_numbness_single_character_and_buttock_site():
    reports = current_symptom_reports("左边小腿外侧有点麻，走路久了更明显，今天能练吗")
    assert reports and reports[0].area is None
    assert "麻" in care_now_terms("左边小腿外侧有点麻")
    assert current_symptom_reports("这事太麻烦了，今天能练吗") == []
    assert current_symptom_reports("中午吃了麻辣烫，今天能练吗") == []


@pytest.mark.parametrize("message", [
    "右肩半夜疼醒了两次，今天还练胸吗",
    "膝盖躺着不动也痛，训练怎么调",
    "手腕休息的时候也疼，今天怎么安排",
])
def test_night_or_rest_pain_prompts_care(message):
    reply = render_reply(decide_scope(message, PLAN))
    assert reply.startswith("你提到了")


def test_doctor_instruction_is_acknowledged_and_rest_pauses_all():
    message = "脚踝韧带拉伤，大夫说先制动一周，这周还能练上肢吗"
    assert doctor_instructions(message) == ["大夫说先制动一周"]
    decision = decide_scope(message, PLAN)
    reply = render_reply(decision)
    assert "以医生的嘱咐为准" in reply and "照常" not in reply
    allowed = decide_scope("右手腕腱鞘炎，医生说下肢可以正常练，今天怎么安排", PLAN)
    assert allowed.doctor and any(d.keep for items in allowed.days.values() for d in items)


def test_follow_up_keeps_prior_trauma_and_doctor_signals():
    prior = ["前天打球把手腕扭了，现在还肿"]
    follow = "那我只练腿可以吗"
    decision = decide_scope(follow, PLAN, prior_user_messages=prior, continuing=True)
    assert decision.care_now and render_reply(decision).startswith("你提到了")
    recovered = decide_scope("现在已经不疼了，那我只练腿可以吗", PLAN, prior_user_messages=prior, continuing=True)
    assert recovered.care_now == []


@pytest.mark.parametrize("message", [
    "前天刚练完背，今天胳膊和背阔酸到抬不起来，正常吗",
    "昨天跑了十公里，今天小腿酸得不行，今天还练吗",
])
def test_post_exercise_muscle_soreness_is_not_a_report(message):
    assert current_symptom_reports(message) == []


def test_joint_soreness_after_training_still_reports():
    assert current_symptom_reports("昨天练腿后今天膝盖酸，还能练吗")


@pytest.mark.parametrize("message", ["身上哪儿都不疼，就是没睡好，今晚练不练", "没有什么地方不舒服，只是很累"])
def test_whole_body_negation(message):
    assert current_symptom_reports(message) == []


@pytest.mark.parametrize("message", [
    "练完硬拉眼前一黑差点站不住，还能继续吗",
    "跑步机上心跳突然很快还心慌，要不要接着跑",
    "胸口一阵发紧，休息了还没好，今天还练吗",
])
def test_emergency_backstop_v2(message):
    assert emergency_signals(message)


# v2b: gaps found on dev set v2; wording differs from the dev cases.
def test_negated_trauma_is_not_a_care_signal():
    assert care_now_terms("手肘最近酸痛，没磕到也没摔过") == []
    assert care_now_terms("手肘昨天磕到了，现在酸痛") != []


def test_relayed_clinician_restriction_counts_as_report():
    decision = decide_scope("理疗师让我右肩这两周别用力，今天胸日咋办", PLAN)
    assert decision.areas == [AREA_SHOULDER]
    reply = render_reply(decision)
    assert reply.startswith("医嘱对") and "以医生的嘱咐为准" in reply and "照常" not in reply
    assert current_symptom_reports("医生说我体检都正常，可以开始练了") == []


def test_weakness_wording_prompts_care():
    assert care_now_terms("脚踝没劲，站久了发软")


@pytest.mark.parametrize("message", ["那我只练上身好吗", "卧推照常做可以不", "那今天练啥"])
def test_short_follow_up_questions_seek_advice(message):
    assert seeks_training_advice(message)


@pytest.mark.parametrize("message", [
    "跑完步左边脸突然有点发麻，说话也不太利索",
    "练着练着上不来气，嘴唇都紫了",
])
def test_emergency_backstop_v2b(message):
    assert emergency_signals(message)


@pytest.mark.parametrize("message", ["膝盖疼的人能骑动感单车吗", "肩伤的话适合游泳吗"])
def test_general_population_questions_are_not_reports(message):
    assert current_symptom_reports(message) == []
