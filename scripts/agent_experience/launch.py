"""Windows launcher: only forward the model key; never forward production DB settings."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "51feceb570b8d0d374948c5bf0558201958d1d4f"
SOURCE = ROOT / ".tmp/agent-experience-20261108-src"
ARTIFACTS = ROOT / "artifacts/agent-dev-notes/current-experience-metrics-20261108"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=("offline", "live"))
    p.add_argument("--batch", required=True)
    p.add_argument("--key-source", choices=(".env", ".env.production"), default=".env.production")
    p.add_argument("--database-tag", default="")
    p.add_argument("--max-cny", type=float, default=5)
    p.add_argument("--max-requests", type=int, default=160)
    args = p.parse_args()
    assert args.batch.replace("-", "").isalnum()
    assert not args.database_tag or args.database_tag.isalnum()
    assert 0 < args.max_cny <= 5 and 0 < args.max_requests <= 160
    assert subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip() == COMMIT
    assert not subprocess.check_output(["git", "-C", str(SOURCE), "status", "--porcelain"], text=True).strip()
    out = ARTIFACTS / args.batch
    out.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    suffix = "_" + args.database_tag if args.database_tag else ""
    env["DATABASE_URL"] = f"postgresql+asyncpg://fitness:local-experience-only@fitness-experience-20261108-pg:5432/experience_{args.mode}{suffix}_test"
    env["SECRET_KEY"] = "isolated-experience-test-secret-20261108-never-for-production"
    env["DEEPSEEK_API_KEY"] = ""
    if args.mode == "live":
        env["DEEPSEEK_API_KEY"] = dotenv_values(ROOT / args.key_source).get("DEEPSEEK_API_KEY") or ""
        assert env["DEEPSEEK_API_KEY"], "model_key_missing"
    command = ["docker", "run", "--rm", "--name", f"fitness-experience-20261108-{args.batch}",
               "--network", "fitness-experience-20261108", "--cpus", "2", "--memory", "2g",
               "-v", f"{SOURCE}:/source:ro", "-v", f"{Path(__file__).parent}:/observer:ro", "-v", f"{out}:/out",
               '-v', f'{ROOT / "scripts/agent_eval"}:/eval_support/agent_eval:ro',
               "-w", "/source/backend", "-e", "DATABASE_URL", "-e", "SECRET_KEY", "-e", "DEEPSEEK_API_KEY",
               "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "PYTHONUNBUFFERED=1", "-e", "PYTHONPATH=/source/backend:/eval_support",
               "--entrypoint", "python", "fitness-agent-backend-ci:0.5.36-local", "/observer/run_baseline.py",
               "--source", "/source", "--commit", COMMIT, "--out", "/out", "--mode", args.mode,
               "--max-cny", str(args.max_cny), "--max-requests", str(args.max_requests)]
    # Retain all failures. Logs contain synthetic fixture data, never credentials.
    with (out / "console.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in process.stdout:
            log.write(line)
            log.flush()
            # Compact progress; detailed exceptions stay in the local log.
            if line.startswith(("serial-", "concurrent_2-", '{"tasks"')):
                print(line.rstrip(), flush=True)
        code = process.wait()
    (out / "exit.json").write_text(json.dumps({"exit_code": code, "mode": args.mode, "source_commit": COMMIT}), encoding="utf-8")
    print(json.dumps({"exit_code": code, "out": str(out)}), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
