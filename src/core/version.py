"""Running code version: the git commit (Render exposes RENDER_GIT_COMMIT; locally `git rev-parse HEAD`)."""
from __future__ import annotations

import functools
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=1)
def git_commit() -> str:
    for k in ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def short(commit: str) -> str:
    return commit[:7] if commit and commit != "unknown" else commit
