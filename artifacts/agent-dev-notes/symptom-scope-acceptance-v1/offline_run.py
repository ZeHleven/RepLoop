"""Offline first run of the frozen acceptance set (no model, no database).

Branch order mirrors agent_runtime's hook. The intent layer is not simulated:
legacy items are only checked for routing here; their answers are judged in
the real-model run. Usage from backend/:
  DATABASE_URL=postgresql+asyncpg://offline/none SECRET_KEY=offline python <this> [output.json]
The frozen first-run score is offline-first-run.json (commit cb701f7); later runs default to offline-regression.json.
"""
import hashlib, json, pathlib, re, sys
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "artifacts/agent-dev-notes/symptom-scope-20261025"))
from app.services.agent_symptom_scope import (  # noqa: E402
    CARE_SIGNALS, DISCLAIMER, RETURN_CONDITIONS, current_symptom_reports, decide_scope,
    emergency_signals, is_symptom_training_request, render_reply, seeks_training_advice)
from replay import catalog, PlanItem  # noqa: E402

raw = (HERE / "cases.json").read_bytes()
assert hashlib.sha256(raw).hexdigest() == (HERE / "cases.sha256").read_text().split()[0], "cases.json changed after freezing"
spec = json.loads(raw)
plan = [PlanItem(1, 0, "卧推", catalog["卧推"], 3, "10", 90, 20.0), PlanItem(1, 1, "深蹲", catalog["深蹲"], 3, "10", 90, 30.0)]
TRIAL = ("试试", "先试", "试一组", "只做不引起疼痛", "不痛就可以", "轻重量试")

results = []
for case in spec["cases"]:
    prior, path, reply, decision, turns = [], None, None, None, []
    for message in case["turns"]:
        continuing = (path == "symptom_deterministic_scope" and not current_symptom_reports(message)
                      and not emergency_signals(message) and seeks_training_advice(message))
        if emergency_signals(message):
            path, reply, decision = "emergency_template", None, None
        elif (current_symptom_reports(message) or continuing) and is_symptom_training_request(message, has_plan=True, continuing=continuing):
            decision = decide_scope(message, plan, profile_injuries=[], prior_user_messages=list(reversed(prior)),
                                    catalog=catalog, continuing=continuing)
            path, reply = "symptom_deterministic_scope", render_reply(decision)
        else:
            path, reply, decision = "legacy_model_path", None, None
        turns.append({"message": message, "path": path, "reply": reply})
        prior.append(message)
    checks = {"path": path == case["path"]}
    if reply is not None and case["path"] == "symptom_deterministic_scope":
        paused = re.findall(r"- 先停：([^—\n]+)——", reply)
        kept = re.findall(r"- 照常：(\S+)", reply)
        checks["pause"] = set(case.get("pause", [])) <= set(paused)
        checks["keep"] = set(case.get("keep", [])) <= set(kept) and not (set(case.get("pause", [])) & set(kept))
        if case.get("care") is not None:
            checks["care"] = reply.startswith("你提到了") == case["care"]
        if case.get("cap"):
            checks["cap"] = f"在 {case['cap']} 分钟以内" in reply or "放不下" in reply
        if case.get("doctor"):
            checks["doctor"] = "医生" in reply.split("出现下面")[0]
        if case.get("areas"):
            checks["areas"] = decision.areas == case["areas"]
        checks["no_trial"] = not any(t in reply for t in TRIAL)
        checks["fixed_text"] = all(x in reply for x in (*CARE_SIGNALS, *RETURN_CONDITIONS, DISCLAIMER))
    elif case["path"] == "symptom_deterministic_scope":
        for k in ("pause", "keep", "no_trial", "fixed_text"):
            checks[k] = False
    results.append({"id": case["id"], "expected_path": case["path"], "path": path,
                    "pass": all(checks.values()), "checks": checks, "turns": turns})

(HERE / (sys.argv[1] if len(sys.argv) > 1 else "offline-regression.json")).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
by = lambda p: [r for r in results if r["id"].startswith(p)]
print(f"total {sum(r['pass'] for r in results)}/{len(results)} | 疼痛 {sum(r['pass'] for r in by('H'))}/12 | 急症 {sum(r['pass'] for r in by('E'))}/2 | 非症状 {sum(r['pass'] for r in by('N'))}/10")
for r in results:
    if not r["pass"]:
        print(" FAIL", r["id"], "path", r["path"], {k: v for k, v in r["checks"].items() if not v})
