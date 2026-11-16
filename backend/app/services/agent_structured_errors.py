from __future__ import annotations

import re
from typing import Any


def _safe_location_segment(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(value))[:40] or "field"


def safe_structured_error_category(error: BaseException) -> str:
    """Return validator types and field paths without raw model values."""
    root_name = type(error).__name__
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        errors_method = getattr(current, "errors", None)
        if callable(errors_method):
            try:
                issues = errors_method()
            except Exception:
                issues = []
            safe_issues: list[str] = []
            for issue in issues[:3] if isinstance(issues, list) else []:
                if not isinstance(issue, dict):
                    continue
                issue_type = re.sub(
                    r"[^a-zA-Z0-9_-]",
                    "_",
                    str(issue.get("type") or "invalid"),
                )[:50]
                location = issue.get("loc") or ()
                if not isinstance(location, (list, tuple)):
                    location = (location,)
                path = ".".join(
                    _safe_location_segment(item) for item in location
                ) or "root"
                safe_issues.append(f"{issue_type}@{path}")
            if safe_issues:
                validator_name = type(current).__name__
                return (
                    f"{root_name}>{validator_name}:"
                    f"{'|'.join(safe_issues)}"
                )[:160]
        current = current.__cause__ or current.__context__
    return root_name[:160]


def safe_error_category(error: BaseException) -> str:
    return type(error).__name__[:160]
