"""Compatibility entry point; implementation lives in scripts/agent_eval."""
from pathlib import Path
import sys

for _parent in Path(__file__).resolve().parents:
    if (_parent / "agent_eval" / "__init__.py").is_file():
        sys.path.insert(0, str(_parent))
        break

from agent_eval.capture import ProviderCapture, RUN_CONTEXT
