import pytest
import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from app.config import settings


async def get_token(client, email):
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": "pass1234"})
    return resp.json()["access_token"]


async def complete_onboarding(client, token, *, location="gym"):
    resp = await client.put(
        "/api/v1/profile",
        json={
            "age": 30,
            "gender": "prefer_not_to_say",
            "height_cm": 170,
            "weight_kg": 65,
            "experience_level": "beginner",
            "primary_goal": "general_fitness",
            "training_days_per_week": 2,
            "session_duration_min": 40,
            "training_location": location,
            "diet_restriction": "none",
            "injuries": [],
            "chronic_conditions": [],
            "onboarding_completed": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_plan(client):
    token = await get_token(client, "wo1@example.com")
    resp = await client.post(
        "/api/v1/workouts/plans",
        json={"name": "减脂计划", "goal": "减脂", "duration_weeks": 8, "days_per_week": 4},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "减脂计划"
    assert data["goal"] == "减脂"
    assert data["ai_generated"] is False


@pytest.mark.asyncio
async def test_list_plans_empty(client):
    token = await get_token(client, "wo2@example.com")
    resp = await client.get("/api/v1/workouts/plans", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_plans_returns_created(client):
    token = await get_token(client, "wo3@example.com")
    first = await client.post(
        "/api/v1/workouts/plans",
        json={"name": "计划A"},
        headers={"Authorization": f"Bearer {token}"},
    )
    second = await client.post(
        "/api/v1/workouts/plans",
        json={"name": "计划B"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/api/v1/workouts/plans", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert first.status_code == 201
    assert second.status_code == 409
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_health_update_marks_active_plan_for_review_and_blocks_new_session(
    client, db_session
):
    from app.models.exercise import Exercise

    exercise = Exercise(
        id="health-review-squat",
        name_zh="杠铃深蹲",
        name_en="Health Review Squat",
        category="力量",
        muscle_primary=["quads"],
        equipment=["barbell"],
        difficulty="初级",
        movement_pattern="squat",
        is_active=True,
    )
    db_session.add(exercise)
    await db_session.commit()

    token = await get_token(client, "health-review@example.com")
    await complete_onboarding(client, token)
    headers = {"Authorization": f"Bearer {token}"}
    plan_response = await client.post(
        "/api/v1/workouts/plans",
        json={
            "name": "健康复核测试",
            "days_per_week": 1,
            "exercises": [{
                "exercise_id": exercise.id,
                "day_of_week": 1,
                "sets": 3,
                "reps": "8-12",
                "rest_seconds": 90,
                "order_index": 0,
            }],
        },
        headers=headers,
    )
    assert plan_response.status_code == 201
    assert plan_response.json()["safety_status"] == "compatible"

    profile_response = await client.put(
        "/api/v1/profile",
        json={"injuries": ["膝关节"]},
        headers=headers,
    )
    assert profile_response.status_code == 200
    assert profile_response.json()["active_plan_safety_status"] == "needs_review"
    assert profile_response.json()["active_plan_safety_reasons"]

    listed = await client.get("/api/v1/workouts/plans", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["safety_status"] == "needs_review"

    started = await client.post(
        "/api/v1/workouts/sessions/start",
        json={"plan_id": plan_response.json()["id"], "day_of_week": 1},
        headers=headers,
    )
    assert started.status_code == 409
    assert started.json()["detail"]["code"] == "plan_health_review_required"


@pytest.mark.asyncio
async def test_list_plans_includes_exercise_details(client, db_session):
    from app.models.exercise import Exercise

    exercise = Exercise(
        id="router-exercise-1",
        name_zh="高脚杯深蹲",
        name_en="Goblet Squat",
        category="力量",
        difficulty="初级",
    )
    db_session.add(exercise)
    await db_session.commit()

    token = await get_token(client, "wo-details@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/api/v1/workouts/plans",
        json={
            "name": "带动作的计划",
            "exercises": [{
                "exercise_id": exercise.id,
                "day_of_week": 1,
                "sets": 3,
                "reps": "8-12",
            }],
        },
        headers=headers,
    )

    resp = await client.get("/api/v1/workouts/plans", headers=headers)

    assert resp.status_code == 200
    exercises = resp.json()[0]["exercises"]
    assert len(exercises) == 1
    assert exercises[0]["exercise_name"] == "高脚杯深蹲"


@pytest.mark.asyncio
async def test_generate_plan_accepts_empty_body_and_returns_details(client, db_session):
    from app.models.exercise import Exercise

    exercise = Exercise(
        id="router-ai-exercise-1",
        name_zh="俯卧撑",
        name_en="Push-up",
        category="力量",
        difficulty="初级",
    )
    db_session.add(exercise)
    await db_session.commit()
    token = await get_token(client, "wo-generate@example.com")
    ai_payload = {
        "name": "AI 新手计划",
        "goal": "general_fitness",
        "days_per_week": 3,
        "duration_weeks": 4,
        "exercises": [{
            "exercise_id": exercise.id,
            "day_of_week": 1,
            "sets": 3,
            "reps": "10",
            "rest_seconds": 60,
            "order_index": 0,
        }],
    }

    with patch(
        "app.services.planner.chat_completion",
        new=AsyncMock(return_value=json.dumps(ai_payload)),
    ):
        resp = await client.post(
            "/api/v1/workouts/plans/generate",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 201
    assert resp.json()["exercises"][0]["exercise_name"] == "俯卧撑"


@pytest.mark.asyncio
async def test_personalized_preview_confirm_and_start_closes_first_plan_loop(client, db_session):
    from app.models.exercise import Exercise

    exercises = [
        Exercise(
            id=f"personalized-{index}",
            name_zh=name,
            name_en=f"Personalized {index}",
            category="力量",
            muscle_primary=["core"],
            difficulty="初级",
            movement_pattern=pattern,
            equipment=["bodyweight"],
            rep_range_min=8,
            rep_range_max=15,
            sets_range_min=2,
            sets_range_max=4,
            is_active=True,
        )
        for index, (name, pattern) in enumerate([
            ("俯卧撑", "push"),
            ("徒手深蹲", "squat"),
            ("俯卧划臂", "pull"),
            ("平板支撑", "isometric"),
        ], start=1)
    ]
    db_session.add_all(exercises)
    await db_session.commit()

    token = await get_token(client, "personalized-loop@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await complete_onboarding(client, token, location="home")

    preview_resp = await client.post(
        "/api/v1/workouts/plans/personalized/preview",
        json={"days_per_week": 2, "session_duration_min": 40},
        headers=headers,
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert {item["day_of_week"] for item in preview["exercises"]} == {1, 4}
    preview["exercises"][0]["sets"] = 3

    confirm_resp = await client.post(
        "/api/v1/workouts/plans/personalized/confirm",
        json={
            key: preview[key]
            for key in (
                "name",
                "goal",
                "duration_weeks",
                "days_per_week",
                "session_duration_min",
                "rationale",
                "safety_notes",
                "exercises",
            )
        },
        headers=headers,
    )
    assert confirm_resp.status_code == 201
    plan = confirm_resp.json()
    assert plan["ai_generated"] is True
    assert plan["is_active"] is True
    assert plan["exercises"][0]["sets"] == 3

    first_day = min(item["day_of_week"] for item in plan["exercises"])
    start_resp = await client.post(
        "/api/v1/workouts/sessions/start",
        json={"plan_id": plan["id"], "day_of_week": first_day},
        headers=headers,
    )
    assert start_resp.status_code == 201
    assert start_resp.json()["status"] == "in_progress"
    assert start_resp.json()["exercises"]


@pytest.mark.asyncio
async def test_personalized_preview_requires_completed_onboarding(client):
    token = await get_token(client, "personalized-incomplete@example.com")
    resp = await client.post(
        "/api/v1/workouts/plans/personalized/preview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "健康筛查" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_plan_detail(client):
    token = await get_token(client, "wo4@example.com")
    create_resp = await client.post(
        "/api/v1/workouts/plans",
        json={"name": "详情计划"},
        headers={"Authorization": f"Bearer {token}"},
    )
    plan_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/workouts/plans/{plan_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == plan_id
    assert "exercises" in resp.json()


@pytest.mark.asyncio
async def test_get_plan_not_found(client):
    token = await get_token(client, "wo5@example.com")
    resp = await client.get("/api/v1/workouts/plans/nonexistent", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_plan(client):
    token = await get_token(client, "wo6@example.com")
    create_resp = await client.post(
        "/api/v1/workouts/plans",
        json={"name": "待删除计划"},
        headers={"Authorization": f"Bearer {token}"},
    )
    plan_id = create_resp.json()["id"]
    del_resp = await client.delete(f"/api/v1/workouts/plans/{plan_id}", headers={"Authorization": f"Bearer {token}"})
    assert del_resp.status_code == 409
    get_resp = await client.get(f"/api/v1/workouts/plans/{plan_id}", headers={"Authorization": f"Bearer {token}"})
    assert get_resp.status_code == 200


@pytest.mark.asyncio
async def test_log_session(client):
    token = await get_token(client, "wo7@example.com")
    resp = await client.post(
        "/api/v1/workouts/sessions",
        json={"trained_at": str(date.today()), "duration_min": 45, "notes": "感觉不错"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["duration_min"] == 45
    assert data["notes"] == "感觉不错"


@pytest.mark.asyncio
async def test_list_sessions(client):
    token = await get_token(client, "wo8@example.com")
    await client.post(
        "/api/v1/workouts/sessions",
        json={"trained_at": str(date.today())},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/api/v1/workouts/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_plans_isolated_between_users(client):
    token1 = await get_token(client, "wo9a@example.com")
    token2 = await get_token(client, "wo9b@example.com")
    await client.post("/api/v1/workouts/plans", json={"name": "用户1计划"}, headers={"Authorization": f"Bearer {token1}"})
    resp = await client.get("/api/v1/workouts/plans", headers={"Authorization": f"Bearer {token2}"})
    assert resp.json() == []


@pytest.mark.asyncio
async def test_workout_execution_lifecycle_and_progress(client, db_session):
    from app.models.exercise import Exercise

    exercise = Exercise(
        id="execution-exercise-1",
        name_zh="杠铃深蹲",
        name_en="Barbell Squat",
        category="力量",
        difficulty="中级",
    )
    db_session.add(exercise)
    await db_session.commit()

    token = await get_token(client, "execution@example.com")
    await complete_onboarding(client, token)
    headers = {"Authorization": f"Bearer {token}"}
    plan_resp = await client.post(
        "/api/v1/workouts/plans",
        json={
            "name": "力量训练",
            "exercises": [{
                "exercise_id": exercise.id,
                "day_of_week": 1,
                "sets": 3,
                "reps": "8-10",
                "rest_seconds": 120,
                "order_index": 0,
            }],
        },
        headers=headers,
    )
    plan_id = plan_resp.json()["id"]

    start_resp = await client.post(
        "/api/v1/workouts/sessions/start",
        json={"plan_id": plan_id, "day_of_week": 1},
        headers=headers,
    )
    assert start_resp.status_code == 201
    started = start_resp.json()
    assert started["status"] == "in_progress"
    assert started["plan_name"] == "力量训练"
    assert started["exercises"][0]["exercise_name"] == "杠铃深蹲"
    assert started["exercises"][0]["target_sets"] == 3
    session_id = started["id"]
    session_exercise_id = started["exercises"][0]["id"]

    active_resp = await client.get(
        "/api/v1/workouts/sessions/active", headers=headers
    )
    assert active_resp.status_code == 200
    assert active_resp.json()["id"] == session_id

    set_resp = await client.put(
        f"/api/v1/workouts/sessions/{session_id}/exercises/"
        f"{session_exercise_id}/sets/1",
        json={"reps": 10, "weight_kg": 20},
        headers=headers,
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["total_sets"] == 1
    assert set_resp.json()["total_reps"] == 10
    assert set_resp.json()["total_volume_kg"] == 200
    assert set_resp.json()["exercises"][0]["sets_data"][0]["is_personal_record"] is True

    update_resp = await client.put(
        f"/api/v1/workouts/sessions/{session_id}/exercises/"
        f"{session_exercise_id}/sets/1",
        json={"reps": 8, "weight_kg": 25},
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["total_sets"] == 1
    assert update_resp.json()["total_reps"] == 8
    assert update_resp.json()["total_volume_kg"] == 200
    assert update_resp.json()["exercises"][0]["personal_best_weight_kg"] == 25

    complete_resp = await client.post(
        f"/api/v1/workouts/sessions/{session_id}/complete",
        json={},
        headers=headers,
    )
    assert complete_resp.status_code == 200
    completed = complete_resp.json()
    assert completed["status"] == "completed"
    assert completed["duration_min"] >= 1
    assert completed["completed_at"] is not None
    assert completed["adaptive_adjustment_status"] == "pending_confirmation"
    assert completed["adaptive_adjustment_proposal"]["proposal_type"] == "plan_adjustment_v2"
    assert completed["adjustments"][0]["action"] == "decrease_weight_and_sets"
    assert completed["adjustments"][0]["after"]["recommended_weight_kg"] == 22.5

    unchanged_plan = await client.get(
        f"/api/v1/workouts/plans/{plan_id}", headers=headers
    )
    assert unchanged_plan.json()["exercises"][0]["sets"] == 3
    assert unchanged_plan.json()["exercises"][0]["recommended_weight_kg"] is None

    proposal = completed["adaptive_adjustment_proposal"]
    from app.models.agent import AgentProposal
    from app.schemas.plan_management_proposal import PlanAdjustmentPayloadV2
    stored_proposal = await db_session.get(AgentProposal, proposal["id"])
    PlanAdjustmentPayloadV2.model_validate(stored_proposal.payload_data)
    confirmed = await client.post(
        f"/api/v1/proposals/{proposal['id']}/confirm",
        json={
            "expected_version": proposal["version"],
            "client_request_id": "complete-adjustment-confirm-1",
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    next_plan_id = confirmed.json()["result_plan_id"]
    revised = (await client.get(f'/api/v1/workouts/plans/{next_plan_id}', headers=headers)).json()
    assert revised['weekly_completed_days'] == 1

    active_after = await client.get(
        "/api/v1/workouts/sessions/active", headers=headers
    )
    assert active_after.json() is None

    history_resp = await client.get("/api/v1/workouts/sessions", headers=headers)
    assert history_resp.status_code == 200
    history_entry = next(
        item for item in history_resp.json() if item["id"] == session_id
    )
    assert history_entry["exercises"][0]["sets_data"][0]["set_number"] == 1

    from app.services.training_lifecycle import training_week
    # A plan revision preserves this week's completion; the next occurrence is next week.
    with patch('app.routers.workouts.training_week', return_value=training_week() + timedelta(days=7)):
        next_start_resp = await client.post(
            "/api/v1/workouts/sessions/start",
            json={"plan_id": next_plan_id, "day_of_week": 1},
            headers=headers,
        )
    assert next_start_resp.status_code == 201
    next_session = next_start_resp.json()
    next_exercise = next_session["exercises"][0]
    assert next_exercise["previous_sets_data"][0]["weight_kg"] == 25
    assert next_exercise["previous_sets_data"][0]["reps"] == 8
    assert next_exercise["personal_best_weight_kg"] == 25
    assert next_exercise["target_weight_kg"] == 22.5
    assert next_exercise["target_sets"] == 2

    lower_set_resp = await client.put(
        f"/api/v1/workouts/sessions/{next_session['id']}/exercises/"
        f"{next_exercise['id']}/sets/1",
        json={"reps": 8, "weight_kg": 20},
        headers=headers,
    )
    assert lower_set_resp.status_code == 200
    assert lower_set_resp.json()["exercises"][0]["sets_data"][0]["is_personal_record"] is False

    record_set_resp = await client.put(
        f"/api/v1/workouts/sessions/{next_session['id']}/exercises/"
        f"{next_exercise['id']}/sets/2",
        json={"reps": 6, "weight_kg": 30},
        headers=headers,
    )
    assert record_set_resp.status_code == 200
    recorded_sets = record_set_resp.json()["exercises"][0]["sets_data"]
    assert recorded_sets[1]["is_personal_record"] is True
    assert record_set_resp.json()["exercises"][0]["personal_best_weight_kg"] == 30

    abandon_next = await client.delete(
        f"/api/v1/workouts/sessions/{next_session['id']}", headers=headers
    )
    assert abandon_next.status_code == 204

    progress_resp = await client.get(
        "/api/v1/workouts/sessions/progress?weeks=4", headers=headers
    )
    assert progress_resp.status_code == 200
    progress = progress_resp.json()
    assert progress["total_sessions"] >= 1
    assert progress["total_sets"] >= 1
    assert progress["total_volume_kg"] >= 200
    assert len(progress["weekly"]) == 4

    delete_plan_resp = await client.delete(
        f"/api/v1/workouts/plans/{next_plan_id}", headers=headers
    )
    assert delete_plan_resp.status_code == 409
    history_after_delete = await client.get(
        "/api/v1/workouts/sessions", headers=headers
    )
    saved_session = next(
        item for item in history_after_delete.json() if item["id"] == session_id
    )
    assert saved_session["plan_id"] == plan_id
    assert saved_session["plan_name"] == "力量训练"


@pytest.mark.asyncio
async def test_completion_feedback_replaces_pain_conflicting_exercise(client, db_session):
    from app.models.exercise import Exercise

    squat = Exercise(
        id="adaptive-pain-squat",
        name_zh="杠铃深蹲",
        name_en="Adaptive Pain Squat",
        category="力量",
        muscle_primary=["quads"],
        equipment=["barbell"],
        difficulty="初级",
        movement_pattern="squat",
        is_active=True,
    )
    bridge = Exercise(
        id="adaptive-pain-bridge",
        name_zh="臀桥",
        name_en="Adaptive Pain Bridge",
        category="力量",
        muscle_primary=["glutes"],
        equipment=["bodyweight"],
        difficulty="初级",
        movement_pattern="hinge",
        is_active=True,
    )
    db_session.add_all([squat, bridge])
    await db_session.commit()

    token = await get_token(client, "adaptive-pain@example.com")
    await complete_onboarding(client, token, location="gym")
    headers = {"Authorization": f"Bearer {token}"}
    plan = (await client.post(
        "/api/v1/workouts/plans",
        json={
            "name": "疼痛替换测试",
            "exercises": [{
                "exercise_id": squat.id,
                "day_of_week": 2,
                "sets": 2,
                "reps": "8-10",
                "rest_seconds": 90,
                "order_index": 0,
            }],
        },
        headers=headers,
    )).json()
    session = (await client.post(
        "/api/v1/workouts/sessions/start",
        json={"plan_id": plan["id"], "day_of_week": 2},
        headers=headers,
    )).json()
    session_exercise = session["exercises"][0]
    for set_number in (1, 2):
        response = await client.put(
            f"/api/v1/workouts/sessions/{session['id']}/exercises/"
            f"{session_exercise['id']}/sets/{set_number}",
            json={"reps": 10, "weight_kg": 30},
            headers=headers,
        )
        assert response.status_code == 200

    completed = await client.post(
        f"/api/v1/workouts/sessions/{session['id']}/complete",
        json={
            "difficulty_feedback": "just_right",
            "perceived_exertion": 7,
            "energy_level": 3,
            "pain_level": 5,
            "pain_areas": ["膝关节"],
            "feedback_notes": "深蹲时膝部疼痛",
        },
        headers=headers,
    )

    assert completed.status_code == 200
    data = completed.json()
    assert data["feedback"]["pain_level"] == 5
    assert data["adjustments"][0]["action"] == "replace_exercise"
    assert data["adjustments"][0]["safety_priority"] is True
    assert data["adaptive_adjustment_status"] == "pending_confirmation"
    replacement_id = data["adjustments"][0]["after"]["exercise_id"]
    assert replacement_id != squat.id

    updated_plan = await client.get(
        f"/api/v1/workouts/plans/{plan['id']}", headers=headers
    )
    assert updated_plan.status_code == 200
    assert updated_plan.json()["exercises"][0]["exercise_id"] == squat.id

    proposal = data["adaptive_adjustment_proposal"]
    confirmed = await client.post(
        f"/api/v1/proposals/{proposal['id']}/confirm",
        json={
            "expected_version": proposal["version"],
            "client_request_id": "pain-adjustment-confirm-1",
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    applied_plan = await client.get(
        f"/api/v1/workouts/plans/{confirmed.json()['result_plan_id']}",
        headers=headers,
    )
    assert applied_plan.json()["exercises"][0]["exercise_id"] == replacement_id


@pytest.mark.asyncio
async def test_new_completion_supersedes_only_older_adaptive_proposal(
    client, db_session
):
    from app.models.exercise import Exercise

    exercise = Exercise(
        id="adaptive-supersede-exercise",
        name_zh="自适应划船",
        name_en="Adaptive Supersede Row",
        category="力量",
        difficulty="初级",
        is_active=True,
    )
    db_session.add(exercise)
    await db_session.commit()
    token = await get_token(client, "adaptive-supersede@example.com")
    await complete_onboarding(client, token)
    headers = {"Authorization": f"Bearer {token}"}
    plan = (await client.post(
        "/api/v1/workouts/plans",
        json={
            "name": "自适应替代测试",
            "exercises": [{
                "exercise_id": exercise.id,
                "day_of_week": 3,
                "sets": 3,
                "reps": "8-10",
                "recommended_weight_kg": 30,
            }],
        },
        headers=headers,
    )).json()

    proposal_ids = []
    from app.services.training_lifecycle import training_week
    for occurrence in range(2):
        with patch('app.routers.workouts.training_week', return_value=training_week() + timedelta(weeks=occurrence)):
            session = (await client.post(
                "/api/v1/workouts/sessions/start",
                json={"plan_id": plan["id"], "day_of_week": 3},
                headers=headers,
            )).json()
        recorded = await client.put(
            f"/api/v1/workouts/sessions/{session['id']}/exercises/"
            f"{session['exercises'][0]['id']}/sets/1",
            json={"reps": 8, "weight_kg": 30},
            headers=headers,
        )
        assert recorded.status_code == 200
        completed = await client.post(
            f"/api/v1/workouts/sessions/{session['id']}/complete",
            json={},
            headers=headers,
        )
        assert completed.json()["adaptive_adjustment_status"] == "pending_confirmation"
        proposal_ids.append(completed.json()["adaptive_adjustment_proposal"]["id"])

    assert proposal_ids[0] != proposal_ids[1]
    old = await client.get(f"/api/v1/proposals/{proposal_ids[0]}", headers=headers)
    latest = await client.get(f"/api/v1/proposals/{proposal_ids[1]}", headers=headers)
    assert old.json()["status"] == "stale"
    assert latest.json()["status"] == "pending_confirmation"


@pytest.mark.asyncio
async def test_explicit_plan_proposal_is_not_overwritten_by_workout_completion(
    client, db_session
):
    from app.models.exercise import Exercise

    exercise = Exercise(
        id="adaptive-explicit-conflict-exercise",
        name_zh="自适应推举",
        name_en="Adaptive Explicit Press",
        category="力量",
        difficulty="初级",
        is_active=True,
    )
    db_session.add(exercise)
    await db_session.commit()
    token = await get_token(client, "adaptive-explicit@example.com")
    await complete_onboarding(client, token)
    headers = {"Authorization": f"Bearer {token}"}
    plan = (await client.post(
        "/api/v1/workouts/plans",
        json={
            "name": "显式提案保护测试",
            "exercises": [{
                "exercise_id": exercise.id,
                "day_of_week": 5,
                "sets": 3,
                "reps": "8-10",
                "recommended_weight_kg": 20,
            }],
        },
        headers=headers,
    )).json()
    with patch.object(settings, "MANUAL_PLAN_PROPOSALS_ENABLED", True):
        context = (await client.get(
            f"/api/v1/workouts/plans/{plan['id']}/edit-context",
            headers=headers,
        )).json()
        candidate = {
            "duration_weeks": context["base_plan"]["duration_weeks"] + 1,
            "training_days": context["base_plan"]["training_days"],
            "exercises": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"exercise_name", "category"}
                }
                for item in context["base_plan"]["exercises"]
            ],
        }
        manual = await client.post(
            f"/api/v1/workouts/plans/{plan['id']}/adjustment-proposals",
            json={
                "client_request_id": "manual-before-completion-1",
                "expected_base_fingerprint": context["base_plan_fingerprint"],
                "candidate": candidate,
            },
            headers=headers,
        )
    assert manual.status_code == 201

    session = (await client.post(
        "/api/v1/workouts/sessions/start",
        json={"plan_id": plan["id"], "day_of_week": 5},
        headers=headers,
    )).json()
    await client.put(
        f"/api/v1/workouts/sessions/{session['id']}/exercises/"
        f"{session['exercises'][0]['id']}/sets/1",
        json={"reps": 8, "weight_kg": 20},
        headers=headers,
    )
    completed = await client.post(
        f"/api/v1/workouts/sessions/{session['id']}/complete",
        json={},
        headers=headers,
    )
    assert completed.status_code == 200
    assert completed.json()["adaptive_adjustment_status"] == "blocked_by_existing"
    assert completed.json()["adaptive_adjustment_proposal"] is None
    preserved = await client.get(
        f"/api/v1/proposals/{manual.json()['id']}", headers=headers
    )
    assert preserved.json()["status"] == "pending_confirmation"


@pytest.mark.asyncio
async def test_empty_session_cannot_complete_and_can_be_abandoned(client, db_session):
    from app.models.exercise import Exercise

    exercise = Exercise(
        id="execution-exercise-2",
        name_zh="俯卧撑",
        name_en="Push-up",
        category="力量",
        difficulty="初级",
    )
    db_session.add(exercise)
    await db_session.commit()

    token = await get_token(client, "execution-empty@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    plan = (await client.post(
        "/api/v1/workouts/plans",
        json={
            "name": "空会话测试",
            "exercises": [{"exercise_id": exercise.id, "day_of_week": 2}],
        },
        headers=headers,
    )).json()
    session = (await client.post(
        "/api/v1/workouts/sessions/start",
        json={"plan_id": plan["id"], "day_of_week": 2},
        headers=headers,
    )).json()

    complete_resp = await client.post(
        f"/api/v1/workouts/sessions/{session['id']}/complete",
        json={},
        headers=headers,
    )
    assert complete_resp.status_code == 409

    second_start = await client.post(
        "/api/v1/workouts/sessions/start",
        json={"plan_id": plan["id"], "day_of_week": 2},
        headers=headers,
    )
    assert second_start.status_code == 409

    abandon_resp = await client.delete(
        f"/api/v1/workouts/sessions/{session['id']}", headers=headers
    )
    assert abandon_resp.status_code == 204
    active_resp = await client.get(
        "/api/v1/workouts/sessions/active", headers=headers
    )
    assert active_resp.json() is None
