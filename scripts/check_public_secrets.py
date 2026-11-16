from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024

SENSITIVE_ASSIGNMENT = re.compile(
    r"""(?im)^[ \t]*
    (?P<key>
        DEEPSEEK_API_KEY|
        EMBEDDING_API_KEY|
        WECHAT_APP_SECRET|
        SECRET_KEY|
        POSTGRES_PASSWORD|
        DATABASE_URL|
        TARO_APP_CLOUD_ENV
    )
    [ \t]*=[ \t]*
    (?P<value>[^#\r\n]*)
    """,
    re.VERBOSE,
)

TOKEN_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "provider API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}

SAFE_MARKERS = (
    "change",
    "example",
    "fitness_pass",
    "localhost",
    "127.0.0.1",
    "postgres:5432",
    "replace",
    "test-only",
    "your-",
    "你的",
    "${{",
)


def candidate_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    candidates: list[Path] = []
    for item in result.stdout.split(b"\0"):
        if not item:
            continue
        relative_path = item.decode("utf-8", errors="strict")
        ignored = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", relative_path],
            cwd=ROOT,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        if not ignored:
            candidates.append(ROOT / relative_path)
    return candidates


def is_safe_example(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    return not normalized or any(marker in normalized for marker in SAFE_MARKERS)


def scan_file(path: Path) -> list[str]:
    if not path.is_file() or path.stat().st_size > MAX_TEXT_FILE_BYTES:
        return []

    content = path.read_bytes()
    if b"\0" in content:
        return []
    text = content.decode("utf-8", errors="ignore")
    findings: list[str] = []

    for match in SENSITIVE_ASSIGNMENT.finditer(text):
        if not is_safe_example(match.group("value")):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{match.group('key')} assignment at line {line}")

    for label, pattern in TOKEN_PATTERNS.items():
        match = pattern.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{label} pattern at line {line}")

    return findings


def main() -> int:
    findings: list[tuple[Path, str]] = []
    for path in candidate_files():
        for detail in scan_file(path):
            findings.append((path.relative_to(ROOT), detail))

    if findings:
        print("Potential public-secret findings:")
        for path, detail in findings:
            print(f"- {path}: {detail}")
        return 1

    print("Public candidate scan passed: no credential-like values detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
