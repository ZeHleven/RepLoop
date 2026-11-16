"""Application version shared by the API, client and release packages."""
from pathlib import Path
import re


VERSION = Path(__file__).resolve().parents[1].joinpath("VERSION").read_text(
    encoding="utf-8"
).strip()
if re.fullmatch(r"\d+\.\d+\.\d+", VERSION) is None:
    raise ValueError("backend/VERSION must contain a semantic version")
