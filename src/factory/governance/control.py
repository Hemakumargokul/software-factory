"""Cross-process run control: the kill switch.

`factory kill <run_id>` must work from any terminal while the run is being
driven by another process, so the flag is a sentinel file — no daemon, no
shared connection, atomic enough for a single operator.

Semantics: a killed run stops at the next stage boundary (the driver checks
between graph supersteps, so the checkpoint stays consistent and nothing
verified is lost) and refuses to start, resume or take gate answers until
the flag is cleared. Killing is reversible by design — the run is parked,
not destroyed.
"""

import os
from pathlib import Path


def _control_dir() -> Path:
    return Path(os.environ.get("FACTORY_CONTROL_DIR", "runs/control"))


def _flag(run_id: str) -> Path:
    return _control_dir() / f"{run_id}.kill"


def request_kill(run_id: str) -> None:
    _control_dir().mkdir(parents=True, exist_ok=True)
    _flag(run_id).touch()


def clear_kill(run_id: str) -> None:
    _flag(run_id).unlink(missing_ok=True)


def is_killed(run_id: str) -> bool:
    return _flag(run_id).exists()
