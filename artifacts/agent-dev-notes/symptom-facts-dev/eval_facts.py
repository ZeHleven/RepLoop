"""Extraction-level dev eval (docs/agent/16 step 3). Dev data, not a score.

For every turn: one live extract_symptom_facts call, then the same routing
and deterministic decision as agent_runtime (intent-layer high risk is not
simulated). Also records what the rule detector alone would route.
Usage from backend/: DEEPSEEK_API_KEY=... DATABASE_URL=postgresql+asyncpg://offline/none SECRET_KEY=offline \
  python <this> [label]
"""
import asyncio, importlib, json, pathlib, re, sys, time
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402
from app.services.agent_symptom_facts import extract_symptom_facts  # noqa: E402
from app.services.agent_symptom_scope import (  # noqa: E402
    KNOWN_AREAS, PlanItem, current_symptom_reports, decide_scope, emergency_signals,
    is_symptom_training_request, render_reply, seeks_training_advice)
from app.services.personalized_planner import INJURY_RISK_KEYWORDS  # noqa: E402

AD = ROOT / "artifacts/agent-dev-notes"
MAX_CNY = 2.5
TRIAL = ("试试", "先试", "试一组", "试一下", "只做不引起疼痛", "不痛就可以", "轻重量试", "感受一下")
NEG_TRIAL = re.compile(r"(?:不要|别|不能|不允许|禁止)[^。；;\n]{0,16}?[“\"]?(?:试一下|试试|先试)[”\"]?")

rows = json.loads((ROOT / "backend/app/data/exercise_catalog_v1.json").read_text(encoding="utf-8"))
def areas_of(f):
    kw = {a for a in KNOWN_AREAS if any(k in f["name_zh"] for k in INJURY_RISK_KEYWORDS.get(a, ()))}
    return tuple(a for a in KNOWN_AREAS if a in set(f.get("contraindications") or []) | kw)
catalog = {r["fields"]["name_zh"]: areas_of(r["fields"]) for r in rows}
PLAN = [PlanItem(1, 0, "卧推", catalog["卧推"], 3, "10", 90, 20.0), PlanItem(1, 1, "深蹲", catalog["深蹲"], 3, "10", 90, 30.0)]


def load_cases():
    out = []
    old_scope = {"WX24", "WX30", "WX31", "WX33", "WX34", "WX35", "SV01", "SV03", "SV05", "SV06", "SV07", "SV08", "HO05", "HO06"}
    for split in ("dev", "holdout"):
        b = json.loads((AD / f"trial-before-clarification-acceptance-2b-20261018/{split}/benchmark.json").read_text(encoding="utf-8"))
        for c in b["cases"]:
            path = "symptom_deterministic_scope" if c["id"] in old_scope else "emergency_template" if c["id"] in {"HO09", "HO10"} else "legacy_model_path"
            out.append({"set": "old40", "id": c["id"], "turns": c["turns"], "path": path, **({"care": True} if c["id"] == "HO06" else {})})
    for c in json.loads((AD / "symptom-scope-20261025/probe-cases.json").read_text(encoding="utf-8"))["cases"]:
        out.append({"set": "probe", "id": c["id"], "turns": c["turns"], "path": c["expect_path"]})
    for name, rel in (("v1", "symptom-scope-acceptance-v1/cases.json"), ("v2", "symptom-scope-acceptance-v2/cases.json"),
                      ("holdout-v1", "symptom-scope-holdout-v1/cases.json")):
        for c in json.loads((AD / rel).read_text(encoding="utf-8"))["cases"]:
            out.append({"set": name, **c})
    return out


class Meter:
    def __init__(self):
        self.req, self.usage = 0, []
    def install(self):
        meter = self
        for name in ("httpx", "httpx2"):
            try:
                mod = importlib.import_module(name)
            except ImportError:
                continue
            orig = mod.AsyncClient.send
            async def send(client, request, _orig=orig, **kw):
                assert request.url.host == "api.deepseek.com"
                if meter.cost() > MAX_CNY:
                    raise RuntimeError("eval budget reached")
                meter.req += 1
                resp = await _orig(client, request, **kw)
                try:
                    u = json.loads(resp.content).get("usage") or {}
                    meter.usage.append((u.get("prompt_tokens", 0), u.get("completion_tokens", 0)))
                except Exception:
                    pass
                return resp
            mod.AsyncClient.send = send
    def cost(self):
        return sum(p * 2 + c * 8 for p, c in self.usage) / 1e6


def rules_path(message, prev_path):
    if emergency_signals(message):
        return "emergency_template"
    cont = prev_path == "symptom_deterministic_scope" and not current_symptom_reports(message) and seeks_training_advice(message)
    if (current_symptom_reports(message) or cont) and is_symptom_training_request(message, has_plan=True, continuing=cont):
        return "symptom_deterministic_scope"
    return "legacy_model_path"


async def run_case(case):
    prior, prev_scope, path, reply, trace = [], None, None, None, []
    rpath = None
    for message in case["turns"]:
        f = await extract_symptom_facts(message, prior_user_messages=list(reversed(prior)),
                                        previous_reply_was_scope=prev_scope is not None)
        rpath = rules_path(message, rpath)
        if emergency_signals(message) or (f is not None and f.emergency):
            path, reply, prev_scope = "emergency_template", None, None
        elif f is None:
            path = rpath
            if path == "symptom_deterministic_scope":
                cont = not current_symptom_reports(message)
                d = decide_scope(message, PLAN, profile_injuries=[], prior_user_messages=list(reversed(prior)),
                                 catalog=catalog, continuing=cont, previous_scope=prev_scope)
                reply, prev_scope = render_reply(d), d.as_data()
            else:
                reply, prev_scope = None, None
        else:
            carry = prev_scope is not None and not f.resolved_now
            scope = (f.purpose == "own_symptom_training" and not f.resolved_now) or (f.continues_previous and carry)
            if scope:
                cont = carry and not f.reports
                d = decide_scope(message, PLAN, profile_injuries=[], prior_user_messages=list(reversed(prior)),
                                 catalog=catalog, continuing=cont, facts=f, previous_scope=prev_scope)
                path, reply, prev_scope = "symptom_deterministic_scope", render_reply(d), d.as_data()
            else:
                path, reply, prev_scope = "legacy_model_path", None, None
        trace.append({"message": message, "facts": f.as_data() if f else None, "path": path, "rules_path": rpath, "reply": reply})
        prior.append(message)
    checks = {"path": path == case["path"]}
    if reply and case["path"] == "symptom_deterministic_scope" and path == case["path"]:
        paused = re.findall(r"- 先停：([^—\n]+)——", reply); kept = re.findall(r"- 照常：(\S+)", reply)
        if case.get("pause") is not None:
            checks["pause"] = set(case["pause"]) <= set(paused)
        if case.get("keep") is not None:
            checks["keep"] = set(case["keep"]) <= set(kept) and not (set(case.get("pause") or []) & set(kept))
        if case.get("care") is not None:
            checks["care"] = reply.startswith("你提到了") == case["care"]
        if case.get("cap"):
            checks["cap"] = f"在 {case['cap']} 分钟以内" in reply or "放不下" in reply or not kept
        if case.get("doctor"):
            checks["doctor"] = "以医生的嘱咐为准" in reply
        checks["no_trial"] = not any(t in NEG_TRIAL.sub("", reply) for t in TRIAL)
    return {"set": case["set"], "id": case["id"], "expected": case["path"], "path": path, "rules_path": rpath,
            "pass": all(checks.values()), "checks": checks, "turns": trace}


async def main(label):
    settings.AGENT_SYMPTOM_FACTS_MODEL_ENABLED = True
    meter = Meter(); meter.install()
    cases = load_cases()
    sem = asyncio.Semaphore(4)
    async def guarded(c):
        async with sem:
            return await run_case(c)
    t0 = time.time()
    results = await asyncio.gather(*(guarded(c) for c in cases))
    out = {"label": label, "cases": len(results), "requests": meter.req, "estimated_cny": round(meter.cost(), 4),
           "seconds": round(time.time() - t0, 1), "results": results}
    (HERE / f"facts-{label}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    by = {}
    for r in results:
        s = by.setdefault(r["set"], [0, 0, 0]); s[0] += r["pass"]; s[1] += 1; s[2] += r["rules_path"] == r["expected"]
    print(f"{label}: model pass {sum(r['pass'] for r in results)}/{len(results)}; requests {meter.req}; est ¥{meter.cost():.4f}")
    for k, (p, n, rp) in by.items():
        print(f"  {k}: model-path+checks {p}/{n} | rules routing {rp}/{n}")
    fails = [(r["set"], r["id"], {k: v for k, v in r["checks"].items() if not v}, r["path"]) for r in results if not r["pass"]]
    print("  fails:", fails)
    none = sum(1 for r in results for t in r["turns"] if t["facts"] is None)
    print("  turns with extraction fallback:", none)

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "run"))
