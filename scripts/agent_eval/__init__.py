"""Shared evaluation support; independent of production Agent behavior."""
import hashlib
from pathlib import Path


def source_manifest():
    """Include the shared implementation in each new evaluation's code freeze."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }
