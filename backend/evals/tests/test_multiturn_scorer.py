from evals.multiturn_contracts import score_semantic


def test_missing_goal_and_model_self_asserted_success_fail():
    case = {"must_match": ["30", "周三"], "semantic_fields": {"requested_effect":"read"}}
    rows = [{"understanding_failed":False, "resolution":{"resolved_query":"训练30分钟", "requested_effect":"read"},
             "state":{"all_requirements_complete":True}, "status":"completed"}]
    assert not score_semantic(case, rows)["passed"]


def test_date_equivalence_and_latest_correction():
    case = {"must_match":[r"(?:9月1日|09-01)",r"(?:9月7日|09-07)"], "must_not_match":["40分钟"]}
    for query in ("9月1日至9月7日", "2026-09-01至2026-09-07"):
        assert score_semantic(case,[{"understanding_failed":False,"resolution":{"resolved_query":query}}])["passed"]
    assert not score_semantic(case,[])["passed"]
    assert not score_semantic(case,[{"understanding_failed":False,"resolution":{"resolved_query":"9月1日至9月7日，40分钟"}}])["passed"]


def test_read_requirement_rejects_write_even_with_all_keywords():
    case={"must_match":["30分钟"],"semantic_fields":{"requested_effect":"read"}}
    assert not score_semantic(case,[{"understanding_failed":False,"resolution":{"resolved_query":"30分钟","requested_effect":"update"}}])["passed"]


def test_explicit_replacement_is_not_a_stale_constraint():
    case={"must_match":["35分钟"],"must_not_match":["25分钟"],"replaced_minutes":{"25":35}}
    def score(text): return score_semantic(case,[{"understanding_failed":False,"resolution":{"resolved_query":text}}])["passed"]
    assert score("由25分钟调整为最多35分钟，周五不训练")
    assert not score("保持25分钟，再考虑35分钟")
    assert not score("由35分钟调整为25分钟")


def test_completed_only_observation_cannot_prove_other_statuses_absent():
    from evals.multiturn_contracts import no_unobserved_status_claim
    assert not no_unobserved_status_claim("范围里没有其他场次，也不存在未完成或跳过的记录。")
    assert no_unobserved_status_claim("仅列已完成场次；未纳入进行中、跳过和提前结束记录。")
