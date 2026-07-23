"""Build version shown in the topbar. Derived from git rather than a hand-
maintained file, so it updates with every commit automatically and can
never drift out of sync with what's actually deployed on the device.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _compute_version() -> str:
    try:
        count = _git("rev-list", "--count", "HEAD")
        sha = _git("rev-parse", "--short", "HEAD")
        return f"v{count} ({sha})"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "dev"


VERSION = _compute_version()
