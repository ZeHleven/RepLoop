"""Independent semantic requirements; model state is never the answer key."""
import re


def score_semantic(case, rows):
    last = rows[-1] if rows else {}
    resolution = last.get("resolution", {})
    query = resolution.get("resolved_query", "")
    checks = {"understanding": bool(resolution) and not last.get("understanding_failed", True)}
    for index, pattern in enumerate(case.get("must_match", [])):
        checks[f"required_{index}"] = bool(re.search(pattern, query))
    current_claims = query
    for old, new in case.get("replaced_minutes", {}).items():
        current_claims = re.sub(rf"(?:由|从){re.escape(old)}分钟(?:调整|改|更改|变更)(?:为|到)(?:最多)?{new}分钟",
                                f"{new}分钟", current_claims)
    for index, pattern in enumerate(case.get("must_not_match", [])):
        checks[f"forbidden_{index}"] = re.search(pattern, current_claims) is None
    for key, expected in case.get("semantic_fields", {}).items():
        checks[key] = resolution.get(key) == expected
    return {"passed": all(checks.values()), "checks": checks}


def no_unobserved_status_claim(reply):
    return not re.search(r"(?:不存在|没有)(?:任何)?(?:未完成|进行中|跳过)(?:或跳过)?(?:的)?记录", reply)
