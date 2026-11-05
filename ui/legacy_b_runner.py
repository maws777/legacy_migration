"""Launch the Legacy B agent from the UI, as its own process (same command as scripts/legacy_b_asks.ps1).

The agent stays a separate system: it reads its own records and talks to the Orchestrator over HTTP. The
process handle lives at module level, so it survives a page reload (F5) as long as the Streamlit server runs.
Its output goes to ``<var>/legacy_b_asks.log`` and is shown on the chat page while it runs.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Launch:
    proc: subprocess.Popen
    log: Path
    started: float


_current: Launch | None = None
popen: Callable[..., subprocess.Popen] = subprocess.Popen          # replaced in tests


def var_dir() -> Path:
    """Database folder of the Orchestrator (MIGRATION_VAR_DIR, set by start_all.ps1), where the calls are metered."""
    return ROOT / os.environ.get("MIGRATION_VAR_DIR", "var")


def start(orchestrator_url: str) -> Launch:
    global _current
    if running():
        return _current
    folder = var_dir()
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "legacy_b_asks.log"
    out = open(log, "w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = popen([sys.executable, "-u", "-m", "agents.legacy_b_asker", "--url", orchestrator_url, "--var", str(folder)],
                 cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT, env=env)
    out.close()                                    # the child keeps its own handle
    _current = Launch(proc, log, time.time())
    return _current


def running() -> bool:
    return _current is not None and _current.proc.poll() is None


def current() -> Launch | None:
    return _current


def output() -> str:
    if _current is None or not _current.log.exists():
        return ""
    return _current.log.read_text(encoding="utf-8", errors="replace")


def reset() -> None:
    """Forget the last launch (tests)."""
    global _current
    _current = None
