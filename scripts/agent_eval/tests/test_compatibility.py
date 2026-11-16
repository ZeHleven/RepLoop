"""Offline checks for legacy entry points and isolated container-style imports."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import httpx

ROOT = Path(__file__).resolve().parents[3]
COMMON = ROOT / "scripts/agent_eval"
OBSERVERS = [ROOT / "scripts/agent_experience"] + [
    ROOT / "scripts" / name / "observer"
    for name in (
        "agent_query_fix", "history_intent_release", "next_workout_prep",
        "prep_advice_quality", "prep_release_acceptance", "rest_release_acceptance",
    )
]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-eval-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def run_python(self, *args, cwd=None, pythonpath=None):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        if pythonpath is not None:
            env["PYTHONPATH"] = str(pythonpath)
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", *map(str, args)],
            cwd=cwd or self.directory, env=env, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_legacy_metrics_cli_from_another_working_directory(self):
        task = {"id": "合成任务", "source_commit": "test", "environment": "local",
                "model_mode": "offline", "data_source": "synthetic", "cohort": "serial",
                "collection_complete": True, "active_ms": 20, "turns": []}
        raw = {"tasks": [task], "provider_calls": [
            {"id": "http-1", "task_id": task["id"], "cost_nano": 100,
             "usage_complete": True, "status_code": 200},
        ]}
        (self.directory / "run.json").write_text(json.dumps(raw), encoding="utf-8")
        (self.directory / "reviews.json").write_text(json.dumps({
            task["id"]: {"outcome": "completed", "evidence": "synthetic fixture"},
        }), encoding="utf-8")
        (self.directory / "feedback.json").write_text("[]", encoding="utf-8")
        for observer in OBSERVERS:
            with self.subTest(observer=observer):
                self.run_python(observer / "metrics.py", "run.json", "--reviews", "reviews.json",
                                "--feedback", "feedback.json", "--out", "metrics.json")
                group = json.loads((self.directory / "metrics.json").read_text())["groups"][0]
                self.assertEqual(group["outcomes"], {"completed": 1})
                self.assertEqual(group["total_cost_cny"], 1e-7)
                self.assertEqual(group["provider_calls"], 1)

    def test_imported_entries_share_request_context_and_request_cap(self):
        first = load(OBSERVERS[0] / "capture.py", "first_capture")
        metrics = load(OBSERVERS[0] / "metrics.py", "first_metrics")
        for index, observer in enumerate(OBSERVERS):
            with self.subTest(observer=observer):
                module = load(observer / "capture.py", f"capture_{index}")
                self.assertIs(module.RUN_CONTEXT, first.RUN_CONTEXT)
                self.assertIs(module.ProviderCapture, first.ProviderCapture)
                self.assertIs(load(observer / "metrics.py", f"metrics_{index}").summarize, metrics.summarize)
                capture = module.ProviderCapture(self.directory, mode="offline", max_requests=1)
                request = httpx.Request("POST", "https://offline.invalid", json={"model": "synthetic"})
                with self.assertRaisesRegex(RuntimeError, "unattributed_model_request"):
                    capture.begin(request, 100)
                token = first.RUN_CONTEXT.set({"task_id": "cross-entry", "run_id": None})
                try:
                    self.assertEqual(capture.begin(request, 100)["task_id"], "cross-entry")
                    with self.assertRaisesRegex(RuntimeError, "request_or_cost_budget"):
                        capture.begin(request, 100)
                finally:
                    first.RUN_CONTEXT.reset(token)

    def test_isolated_flat_and_nested_observer_mounts(self):
        support = self.directory / "eval_support"
        shutil.copytree(COMMON, support / "agent_eval", ignore=shutil.ignore_patterns("tests", "__pycache__"))
        source = self.directory / "source/backend"
        source.mkdir(parents=True)
        probe = """
import pathlib, sys
sys.path.insert(0, sys.argv[1])
import capture, metrics
import agent_eval.capture as shared
from agent_eval import source_manifest
assert pathlib.Path(shared.__file__).parent == pathlib.Path(sys.argv[2])
assert capture.RUN_CONTEXT is shared.RUN_CONTEXT
assert capture.ProviderCapture is shared.ProviderCapture
assert metrics.clock_interval(1, 2)["ms"] == 1000
assert set(source_manifest()) == {"__init__.py", "capture.py", "metrics.py"}
"""
        for index, observer in enumerate(OBSERVERS):
            with self.subTest(observer=observer):
                layout = "harness/observer" if "release_acceptance" in str(observer) else "observer"
                mounted = self.directory / str(index) / layout
                mounted.mkdir(parents=True)
                for name in ("capture.py", "metrics.py"):
                    shutil.copyfile(observer / name, mounted / name)
                self.run_python("-c", probe, mounted, support / "agent_eval", cwd=source, pythonpath=support)

    def test_freeze_detects_a_changed_shared_dependency(self):
        package = self.directory / "agent_eval"
        shutil.copytree(COMMON, package, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        isolated = load(package / "__init__.py", "isolated_manifest")
        before = isolated.source_manifest()
        self.assertEqual(set(before), {"__init__.py", "capture.py", "metrics.py"})
        with (package / "capture.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# synthetic code change\n")
        after = isolated.source_manifest()
        self.assertNotEqual(before["capture.py"], after["capture.py"])
        self.assertEqual(before["metrics.py"], after["metrics.py"])


if __name__ == "__main__":
    unittest.main()
