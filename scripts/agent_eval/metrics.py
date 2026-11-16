"""Offline task-level metrics. Unknown evidence is never silently counted as zero."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from typing import Any

OUTCOMES = {"completed", "partial", "failed", "awaiting_input", "awaiting_confirmation", "reasonable_refusal", "unknown"}
GROUP_FIELDS = ("source_commit", "environment", "model_mode", "data_source", "cohort")


def distribution(values):
    valid = sorted(float(v) for v in values if isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0)
    n = len(valid)
    def q(p):
        return round(valid[max(0, math.ceil(n * p) - 1)], 3) if n else None
    return {"n": n, "p50": q(.5), "p95": q(.95), "max": valid[-1] if n else None,
            "method": "nearest_rank", "interpretation": "descriptive_sample_only", "small_sample": n < 100}


def clock_interval(start, end, *, wall_delta_ms=None, max_deviation_ms=50):
    if start is None or end is None:
        return {"ms": None, "reason": "missing_clock"}
    delta = (end - start) * 1000
    if not math.isfinite(delta) or delta < 0:
        return {"ms": None, "reason": "invalid_monotonic_clock"}
    if wall_delta_ms is not None and (not math.isfinite(wall_delta_ms) or abs(wall_delta_ms - delta) > max_deviation_ms):
        return {"ms": None, "reason": "clock_disagreement", "raw_monotonic_ms": delta, "raw_wall_ms": wall_delta_ms}
    return {"ms": round(delta, 3), "reason": None}


def summarize(tasks, calls, reviews=None, feedback=None):
    reviews = reviews or {}
    feedback = feedback or []
    task_ids = [t["id"] for t in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("duplicate_task_id")
    if set(reviews) - set(task_ids):
        raise ValueError("review_for_unknown_task")
    call_ids = [c["id"] for c in calls]
    if len(call_ids) != len(set(call_ids)):
        raise ValueError("duplicate_provider_call_id")
    by_task = defaultdict(list)
    for call in calls:
        if call.get("task_id") not in task_ids:
            raise ValueError("unattributed_provider_call")
        by_task[call["task_id"]].append(call)
    groups = defaultdict(list)
    for task in tasks:
        if any(not task.get(k) for k in GROUP_FIELDS):
            raise ValueError("missing_group_identity")
        groups[tuple(task[k] for k in GROUP_FIELDS)].append(task)
    result = []
    for key, rows in groups.items():
        ids = {r["id"] for r in rows}
        provider = [c for t in ids for c in by_task[t]]
        counts = Counter()
        for row in rows:
            review = reviews.get(row["id"], {})
            outcome = review.get("outcome", "unknown")
            if outcome not in OUTCOMES or (outcome != "unknown" and not review.get("evidence")):
                raise ValueError("outcome_requires_evidence")
            counts[outcome] += 1
        settled = [c for c in provider if c.get("cost_nano") is not None and c.get("usage_complete") is True]
        complete = len(settled) == len(provider) and all(t.get("collection_complete") for t in rows)
        observed = sum(c["cost_nano"] for c in settled) / 1e9
        turns = [turn for row in rows for turn in row.get("turns", [])]
        result.append({
            **dict(zip(GROUP_FIELDS, key)), "tasks": len(rows), "outcomes": dict(counts),
            "started_tasks": sum(bool(t.get("turns")) for t in rows),
            "unexecuted_tasks": sum(not t.get("turns") for t in rows),
            "benchmark_complete": all(t.get("collection_complete") for t in rows) and counts["unknown"] == 0,
            "task_completion": {"numerator": counts["completed"], "denominator": len(rows), "rate": counts["completed"] / len(rows)},
            "task_active_ms": distribution(t.get("active_ms") for t in rows),
            "completed_task_active_ms": distribution(t.get("active_ms") for t in rows if reviews.get(t["id"], {}).get("outcome") == "completed"),
            "turn_visible_ms": distribution(t.get("visible_timing", {}).get("ms") for t in turns),
            "queue_ms": distribution(t.get("queue_ms") for t in turns),
            "processing_ms": distribution(t.get("processing_ms") for t in turns),
            "invalid_visible_timings": sum(t.get("visible_timing", {}).get("ms") is None for t in turns),
            "provider_calls": len(provider), "provider_usage_complete": len(settled),
            "provider_errors": sum(c.get("error_type") is not None or c.get("status_code", 0) >= 400 for c in provider),
            "observed_cost_cny": round(observed, 9), "cost_complete": complete,
            "total_cost_cny": round(observed, 9) if complete else None,
            "amortized_cost_per_completed_task_cny": round(observed / counts["completed"], 9) if complete and counts["completed"] else None,
            "known_input_tokens": sum(c.get("input_tokens") or 0 for c in settled),
            "known_output_tokens": sum(c.get("output_tokens") or 0 for c in settled),
            "cost_basis": "application budget ledger tariff; conservative cache-miss estimate, not provider invoice",
        })
    humans = []
    seen = set()
    for item in feedback:
        if item.get("data_source") != "human_natural_use" or not item.get("evidence_ref"):
            raise ValueError("feedback_requires_human_source_and_evidence")
        identity = (item.get("source_commit"), item.get("task_id"))
        if None in identity or identity in seen:
            raise ValueError("feedback_missing_identity_or_duplicate")
        seen.add(identity)
        for name in ("correction_count", "repeated_explanation_count"):
            value = item.get(name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("invalid_feedback_count")
        rating = item.get("satisfaction_1_to_5")
        if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
            raise ValueError("invalid_satisfaction_rating")
        if item.get("explicit_abandonment") is not None and type(item["explicit_abandonment"]) is not bool:
            raise ValueError("invalid_abandonment_observation")
        humans.append(item)
    human_groups = []
    for commit in sorted({h["source_commit"] for h in humans}):
        rows = [h for h in humans if h["source_commit"] == commit]
        item = {"source_commit": commit, "n": len(rows)}
        for name in ("correction_count", "repeated_explanation_count"):
            observed = [h[name] for h in rows if h.get(name) is not None]
            item[name] = {"observed_tasks": len(observed), "total": sum(observed) if observed else None,
                          "mean": sum(observed) / len(observed) if observed else None, "unknown_tasks": len(rows) - len(observed)}
        item["explicit_abandonment"] = {"count": sum(h.get("explicit_abandonment") is True for h in rows),
                                       "observed_tasks": sum(type(h.get("explicit_abandonment")) is bool for h in rows)}
        item["phone_send_to_render_ms"] = distribution(h.get("phone_send_to_render_ms") for h in rows)
        item["user_reported_outcomes"] = dict(Counter(h.get("user_reported_outcome") or "unknown" for h in rows))
        ratings = [h["satisfaction_1_to_5"] for h in rows if h.get("satisfaction_1_to_5") is not None]
        item["satisfaction_1_to_5"] = {"observed_tasks": len(ratings), "counts": dict(Counter(ratings)),
                                       "mean": sum(ratings)/len(ratings) if ratings else None}
        human_groups.append(item)
    return {"groups": result, "human_feedback": {"n": len(humans), "groups": human_groups,
            "corrections": None if not humans else "see per-version groups", "satisfaction": None if not humans else "see per-version groups",
            "phone_end_to_end": None if not humans else "see per-version groups"}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("run", type=Path)
    p.add_argument("--reviews", type=Path)
    p.add_argument("--feedback", type=Path)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    raw = json.loads(a.run.read_text(encoding="utf-8"))
    reviews = json.loads(a.reviews.read_text(encoding="utf-8")) if a.reviews else {}
    feedback = json.loads(a.feedback.read_text(encoding="utf-8")) if a.feedback else []
    data = summarize(raw["tasks"], raw["provider_calls"], reviews, feedback)
    a.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"groups": len(data["groups"]), "human_feedback_n": data["human_feedback"]["n"]}))


if __name__ == "__main__":
    main()
