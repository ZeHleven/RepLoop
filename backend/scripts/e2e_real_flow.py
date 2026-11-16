"""Run the real HTTP fitness journey against a running local stack."""

from __future__ import annotations

import argparse
import json
import re
import uuid

import httpx


def target_reps(value: str | None) -> int:
    numbers = [int(item) for item in re.findall(r"\d+", value or "")]
    return max(numbers) if numbers else 10


def require(response: httpx.Response, expected: int) -> dict | list | None:
    if response.status_code != expected:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text[:500]}"
        )
    if not response.content:
        return None
    return response.json()


def run(base_url: str) -> dict:
    base_url = base_url.rstrip("/")
    api_url = f"{base_url}/api/v1"
    email = f"e2e-{uuid.uuid4().hex[:12]}@example.com"

    with httpx.Client(timeout=30) as client:
        health = require(client.get(f"{base_url}/health"), 200)
        auth = require(
            client.post(
                f"{api_url}/auth/register",
                json={"email": email, "password": "E2eOnly-Password-2026"},
            ),
            201,
        )
        headers = {"Authorization": f"Bearer {auth['access_token']}"}

        profile = require(
            client.put(
                f"{api_url}/profile",
                headers=headers,
                json={
                    "age": 30,
                    "gender": "prefer_not_to_say",
                    "height_cm": 170,
                    "weight_kg": 65,
                    "experience_level": "beginner",
                    "primary_goal": "general_fitness",
                    "training_days_per_week": 2,
                    "session_duration_min": 30,
                    "training_location": "home",
                    "diet_restriction": "none",
                    "injuries": [],
                    "chronic_conditions": [],
                    "onboarding_completed": True,
                },
            ),
            200,
        )

        preview = require(
            client.post(
                f"{api_url}/workouts/plans/personalized/preview",
                headers=headers,
                json={
                    "goal": "general_fitness",
                    "duration_weeks": 4,
                    "days_per_week": 2,
                    "session_duration_min": 30,
                },
            ),
            200,
        )
        confirmation = {
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
        }
        plan = require(
            client.post(
                f"{api_url}/workouts/plans/personalized/confirm",
                headers=headers,
                json=confirmation,
            ),
            201,
        )

        day_of_week = min(item["day_of_week"] for item in plan["exercises"])
        session = require(
            client.post(
                f"{api_url}/workouts/sessions/start",
                headers=headers,
                json={"plan_id": plan["id"], "day_of_week": day_of_week},
            ),
            201,
        )

        recorded = session
        for exercise in session["exercises"]:
            for set_number in range(1, (exercise["target_sets"] or 1) + 1):
                recorded = require(
                    client.put(
                        f"{api_url}/workouts/sessions/{session['id']}"
                        f"/exercises/{exercise['id']}/sets/{set_number}",
                        headers=headers,
                        json={
                            "reps": target_reps(exercise["target_reps"]),
                            "weight_kg": 20,
                        },
                    ),
                    200,
                )

        completed = require(
            client.post(
                f"{api_url}/workouts/sessions/{session['id']}/complete",
                headers=headers,
                json={
                    "duration_min": 30,
                    "difficulty_feedback": "too_easy",
                    "perceived_exertion": 5,
                    "energy_level": 5,
                    "pain_level": 0,
                    "pain_areas": [],
                    "feedback_notes": "real-environment-e2e",
                },
            ),
            200,
        )
        history = require(
            client.get(f"{api_url}/workouts/sessions", headers=headers),
            200,
        )
        progress = require(
            client.get(
                f"{api_url}/workouts/sessions/progress?weeks=4", headers=headers
            ),
            200,
        )
        next_session = require(
            client.post(
                f"{api_url}/workouts/sessions/start",
                headers=headers,
                json={"plan_id": plan["id"], "day_of_week": day_of_week},
            ),
            201,
        )

        adjusted_weights = [
            item["target_weight_kg"] for item in next_session["exercises"]
        ]
        previous_sets_present = all(
            bool(item["previous_sets_data"]) for item in next_session["exercises"]
        )
        personal_record_present = any(
            item.get("is_personal_record")
            for exercise in recorded["exercises"]
            for item in exercise["sets_data"]
        )

        assert health == {"status": "ok"}
        assert profile["onboarding_completed"] is True
        assert completed["status"] == "completed"
        assert completed["adjustments"]
        assert history and history[0]["id"] == completed["id"]
        assert progress["total_sessions"] >= 1
        assert adjusted_weights and all(
            weight is not None and weight > 20 for weight in adjusted_weights
        )
        assert previous_sets_present
        assert personal_record_present

        require(
            client.delete(
                f"{api_url}/workouts/sessions/{next_session['id']}",
                headers=headers,
            ),
            204,
        )

        return {
            "status": "passed",
            "transport": base_url,
            "profile_onboarding": "passed",
            "personalized_plan": "passed",
            "recorded_exercises": len(session["exercises"]),
            "recorded_sets": completed["total_sets"],
            "personal_record": "passed",
            "completion_feedback": "passed",
            "adjustment_actions": [
                item["action"] for item in completed["adjustments"]
            ],
            "next_workout_adjusted_weight": adjusted_weights,
            "previous_sets_carried": previous_sets_present,
            "history_and_progress": "passed",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    print(json.dumps(run(args.base_url), ensure_ascii=False, indent=2))
