"""Offline byte fingerprint check. No model calls, database access, or tests."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
snapshot = json.loads((HERE / "snapshot.json").read_text(encoding="utf8"))
failures = []
for item in snapshot["files"]:
    path = ROOT / item["file"]
    if not path.is_file():
        failures.append({"file": item["file"], "error": "missing"})
    elif hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
        failures.append({"file": item["file"], "error": "sha256 mismatch"})
print(json.dumps({"source_files": snapshot["source_file_count"], "total_files": len(snapshot["files"]), "failures": failures}, ensure_ascii=False))
raise SystemExit(1 if failures else 0)
