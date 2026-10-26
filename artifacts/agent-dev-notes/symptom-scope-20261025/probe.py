"""Run probe-cases.json through the same branch order as agent_runtime.

Order mirrors the runtime hook: emergency → template; no current report and
no continuation → legacy; training-related → deterministic scope; else legacy. The intent
layer's own high-risk flag is not simulated (it would only add emergencies).
Usage from backend/: DATABASE_URL=postgresql+asyncpg://offline/none SECRET_KEY=offline python <this> [label]
"""
import json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(HERE))
from app.services.agent_symptom_scope import (  # noqa: E402
    current_symptom_reports, decide_scope, emergency_signals, is_symptom_training_request, render_reply, seeks_training_advice)
from replay import catalog, PlanItem  # noqa: E402

label = sys.argv[1] if len(sys.argv) > 1 else "run"
bench = json.loads((ROOT / "artifacts/agent-dev-notes/trial-before-clarification-acceptance-2b-20261018/dev/benchmark.json").read_text(encoding="utf-8"))
plan = [PlanItem(e["day_of_week"], e["order_index"], e["exercise_name"], catalog[e["exercise_name"]],
                 e["sets"], e["reps"], e["rest_seconds"], e.get("recommended_weight_kg")) for e in bench["plan"]["exercises"]]
cases = json.loads((HERE / "probe-cases.json").read_text(encoding="utf-8"))["cases"]
results = []
for case in cases:
    prior, path, reply = [], None, None
    for message in case["turns"]:
        continuing = (path == "symptom_deterministic_scope" and not current_symptom_reports(message)
                      and not emergency_signals(message) and seeks_training_advice(message))
        if emergency_signals(message):
            path, reply = "emergency_template", None
        elif not (current_symptom_reports(message) or continuing):
            path, reply = "legacy_model_path", None
        elif is_symptom_training_request(message, has_plan=True, continuing=continuing):
            d = decide_scope(message, plan, profile_injuries=[], prior_user_messages=list(reversed(prior)),
                             catalog=catalog, continuing=continuing)
            path, reply = "symptom_deterministic_scope", render_reply(d)
        else:
            path, reply = "legacy_model_path", None
        prior.append(message)
    results.append({"id": case["id"], "expect_path": case["expect_path"], "path": path,
                    "path_ok": path == case["expect_path"], "expect": case["expect"], "reply": reply})
(HERE / f"probe-{label}.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
ok = sum(r["path_ok"] for r in results)
print(f"{label}: path correct {ok}/{len(results)}")
for r in results:
    if not r["path_ok"]:
        print(" MISS", r["id"], "expected", r["expect_path"], "got", r["path"], "|", r["expect"])
