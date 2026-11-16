"""Show the active source identity and reject inconsistent release versions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def git(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *arguments], encoding="utf-8"
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sync-version", action="store_true",
                        help="Copy backend/VERSION to miniapp/package.json")
    args = parser.parse_args()
    version = (ROOT / "backend/VERSION").read_text(encoding="utf-8").strip()
    if re.fullmatch(r"\d+\.\d+\.\d+", version) is None:
        parser.error("backend/VERSION must contain a semantic version")
    package_path = ROOT / "miniapp/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if args.sync_version and package["version"] != version:
        package["version"] = version
        package_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    if package["version"] != version:
        parser.error("miniapp/package.json version differs; run with --sync-version")
    print(json.dumps({"workspace": str(ROOT), "version": version,
                      "branch": git("branch", "--show-current"),
                      "commit": git("rev-parse", "HEAD"),
                      "source_dirty": bool(git("status", "--porcelain"))},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
