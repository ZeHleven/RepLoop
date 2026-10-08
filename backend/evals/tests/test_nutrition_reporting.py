import pytest

from app.services.agent_intent import IntentResolution
from app.services.agent_query_reports import report_for_resolution, render_nutrition_history


def resolution(query, **kwargs):
    return IntentResolution(primary_intent="nutrition_history_query", intent_domain="nutrition",
                            evidence_requirements=["nutrition_history"], resolved_query=query,
                            confidence=1, **kwargs)


def test_plain_history_uses_same_scoped_data_for_text_and_card():
    report = report_for_resolution(resolution("最近有记录的饮食日期和营养汇总"), ["nutrition.list_history"])
    assert report.kind == "nutrition_history"
    data = {"days": [{"date": day, "total_calories": 130.0, "total_protein_g": 3.0,
                     "total_carbs_g": 28.0, "total_fat_g": 1.0} for day in ("2026-09-29", "2026-08-01")],
            "scope": {"as_of": "2026-09-29", "requested_limit": 30,
                      "first_logged_date": "2026-08-01", "last_logged_date": "2026-09-29"}}
    reply = render_nutrition_history(data)
    assert "近30天" not in reply
    assert "2026-08-01" in reply and "130 千卡" in reply


@pytest.mark.parametrize("query", [
    "最近30天的饮食", "上周的饮食", "最近三个饮食日期", "饮食是否合理，给我建议",
    "过去饮食平均热量", "比较这两天饮食", "我的饮食趋势如何",
])
def test_formatter_does_not_swallow_a_different_user_goal(query):
    assert report_for_resolution(resolution(query), ["nutrition.list_history"]) is None


def test_assessment_and_mixed_evidence_keep_semantic_execution():
    assert report_for_resolution(resolution("近期饮食", request_kind="assessment"), ["nutrition.list_history"]) is None
    assert report_for_resolution(resolution("饮食和资料"), ["nutrition.list_history", "profile.get_summary"]) is None
