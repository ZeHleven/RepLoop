import unittest
from metrics import summarize, distribution, clock_interval


def task(identity, **values):
    return {"id": identity, "source_commit": "a", "environment": "local_tcp", "model_mode": "live",
            "data_source": "synthetic", "cohort": "serial", "collection_complete": True,
            "active_ms": 20, "turns": [], **values}


class MetricsTests(unittest.TestCase):
    def test_completed_run_is_not_completed_task(self):
        data = summarize([task("a", status="completed")], [])
        self.assertEqual(data["groups"][0]["outcomes"], {"unknown": 1})

    def test_failure_cost_is_amortized_over_successes(self):
        calls = [{"id": "1", "task_id": "a", "cost_nano": 100, "usage_complete": True},
                 {"id": "2", "task_id": "b", "cost_nano": 200, "usage_complete": True}]
        data = summarize([task("a"), task("b")], calls,
                         {"a": {"outcome": "completed", "evidence": "SQL"}, "b": {"outcome": "failed", "evidence": "reply"}})
        self.assertEqual(data["groups"][0]["amortized_cost_per_completed_task_cny"], 3e-7)

    def test_unknown_usage_is_not_zero(self):
        data = summarize([task("a")], [{"id": "1", "task_id": "a", "cost_nano": None}])
        self.assertIsNone(data["groups"][0]["total_cost_cny"])

    def test_unstarted_task_is_in_denominator(self):
        data = summarize([task("a"), task("b", collection_complete=False, active_ms=None)], [],
                         {"a": {"outcome": "completed", "evidence": "reply"}})
        self.assertEqual(data["groups"][0]["task_completion"]["rate"], .5)
        self.assertFalse(data["groups"][0]["cost_complete"])

    def test_versions_and_modes_are_not_pooled(self):
        rows = [task("a"), task("b", source_commit="b"), task("c", model_mode="offline"), task("d", cohort="concurrent_2")]
        self.assertEqual(len(summarize(rows, [])["groups"]), 4)

    def test_retries_are_each_counted(self):
        calls = [{"id": str(n), "task_id": "a", "cost_nano": None, "status_code": 503} for n in range(3)]
        group = summarize([task("a")], calls)["groups"][0]
        self.assertEqual((group["provider_calls"], group["provider_errors"]), (3, 3))

    def test_missing_human_feedback_stays_unknown(self):
        data = summarize([task("a")], [])
        self.assertEqual(data["human_feedback"]["n"], 0)
        self.assertIsNone(data["human_feedback"]["corrections"])

    def test_synthetic_feedback_is_rejected(self):
        with self.assertRaises(ValueError):
            summarize([], [], feedback=[{"data_source": "synthetic", "correction_count": 0}])

    def test_human_unknowns_do_not_lower_corrections(self):
        feedback = [{"source_commit": "a", "task_id": str(n), "data_source": "human_natural_use", "evidence_ref": "user-feedback",
                     "correction_count": value} for n, value in enumerate([2, None])]
        values = summarize([], [], feedback=feedback)["human_feedback"]["groups"][0]["correction_count"]
        self.assertEqual((values["mean"], values["unknown_tasks"]), (2, 1))

    def test_negative_and_clock_jumps_are_excluded(self):
        self.assertIsNone(clock_interval(2, 1)["ms"])
        self.assertIsNone(clock_interval(1, 2, wall_delta_ms=2000)["ms"])
        self.assertEqual(clock_interval(1, 2, wall_delta_ms=1001)["ms"], 1000)

    def test_small_sample_p95_is_explicit(self):
        result = distribution([1, 2, 3, None, -1, float("nan")])
        self.assertEqual((result["n"], result["p95"], result["small_sample"]), (3, 3, True))

    def test_duplicate_and_unattributed_calls_fail(self):
        for calls in [[{"id": "1", "task_id": "missing"}], [{"id": "1", "task_id": "a"}] * 2]:
            with self.assertRaises(ValueError):
                summarize([task("a")], calls)

    def test_asserted_success_needs_evidence(self):
        with self.assertRaises(ValueError):
            summarize([task("a")], [], {"a": {"outcome": "completed"}})


if __name__ == "__main__":
    unittest.main()
