"""Offline replay of the frozen 30+10 instances through the deterministic path.

Pure functions only: no model, no database. Plan and catalog come from the
frozen benchmark fixtures and backend/app/data/exercise_catalog_v1.json.
Run from backend/ (settings need placeholders, nothing connects): DATABASE_URL=postgresql+asyncpg://offline/none SECRET_KEY=offline python ../artifacts/agent-dev-notes/symptom-scope-20261025/replay.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.agent_symptom_scope import (  # noqa: E402
    KNOWN_AREAS, PlanItem, decide_scope, emergency_signals, is_symptom_training_request, render_reply,
)
from app.services.personalized_planner import INJURY_RISK_KEYWORDS  # noqa: E402

SRC = ROOT / "artifacts/agent-dev-notes/trial-before-clarification-acceptance-2b-20261018"
catalog_rows = json.loads((ROOT / "backend/app/data/exercise_catalog_v1.json").read_text(encoding="utf-8"))


def areas_of(fields):
    declared = set(fields.get("contraindications") or [])
    keyword = {a for a in KNOWN_AREAS if any(k in fields["name_zh"] for k in INJURY_RISK_KEYWORDS.get(a, ()))}
    return tuple(a for a in KNOWN_AREAS if a in declared | keyword)


catalog = {r["fields"]["name_zh"]: areas_of(r["fields"]) for r in catalog_rows}


def main():
    review = json.loads((SRC / "review-notes.json").read_text(encoding="utf-8"))
    old = {x["id"]: x for x in review["dev"] + review["holdout"]}
    out = []
    for split in ("dev", "holdout"):
        bench = json.loads((SRC / split / "benchmark.json").read_text(encoding="utf-8"))
        plan = [PlanItem(e["day_of_week"], e["order_index"], e["exercise_name"], catalog[e["exercise_name"]],
                         e["sets"], e["reps"], e["rest_seconds"], e.get("recommended_weight_kg"))
                for e in bench["plan"]["exercises"]]
        for case in bench["cases"]:
            injuries = (case.get("profile") or {}).get("injuries", bench["profile"].get("injuries", []))
            prior, turns = [], []
            for message in case["turns"]:
                emergency = emergency_signals(message)
                triggered = is_symptom_training_request(message, has_plan=True)
                decision = decide_scope(message, plan, profile_injuries=injuries,
                                        prior_user_messages=list(reversed(prior)), catalog=catalog)
                turns.append({
                    "message": message,
                    "path": "emergency_template" if emergency else "symptom_deterministic_scope" if triggered else "legacy_model_path",
                    "decision": decision.as_data() if triggered and not emergency else None,
                    "reply": render_reply(decision) if triggered and not emergency else None,
                })
                prior.append(message)
            out.append({
                "id": case["id"], "split": split, "required": case.get("required"), "gold_notes": case.get("gold_notes"),
                "old_results": {k: {"pass": v["pass"], "category": v["category"]} for k, v in old.items() if k.startswith(case["id"] + "-")},
                "turns": turns,
            })
    (HERE / "replay.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["# 旧 40 实例离线重放：确定性路径交付原文", "",
             "由 replay.py 生成；纯函数、无模型、无数据库。计划与动作库来自冻结验收夹具。", ""]
    for item in out:
        lines.append(f"## {item['id']}（{item['split']}）旧结果：" + "，".join(f"{k} {'通过' if v['pass'] else v['category']}" for k, v in item["old_results"].items()))
        for i, t in enumerate(item["turns"], 1):
            lines.append(f"\n**第{i}轮用户**：{t['message']}\n\n**路径**：`{t['path']}`\n")
            if t["reply"]:
                lines.append("```text\n" + t["reply"] + "\n```")
        lines.append("")
    (HERE / "replay.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps([[i["id"], [t["path"] for t in i["turns"]]] for i in out], ensure_ascii=False))


if __name__ == "__main__":
    main()
