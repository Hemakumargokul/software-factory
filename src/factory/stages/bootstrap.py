"""Create the product sandbox: copy the profile's scaffold, init git.

The sandbox lives outside the orchestrator repo (default /tmp/factory/<run_id>,
override with FACTORY_SANDBOX_ROOT). The acceptance suite never enters it —
the agent works here, the exam stays in the orchestrator repo.
"""

import os
import shutil
import time
from pathlib import Path

from factory import git_ops
from factory.observability import tracing
from factory.profiles import get_profile
from factory.state import FactoryState, metric_event, record_decision


def sandbox_root() -> Path:
    return Path(os.environ.get("FACTORY_SANDBOX_ROOT", "/tmp/factory"))


def seed_dir() -> Path | None:
    """Brownfield runs seed from an existing product tree instead of the
    profile scaffold (FACTORY_SEED_DIR, e.g. a previous run's sandbox)."""
    override = os.environ.get("FACTORY_SEED_DIR", "").strip()
    return Path(override) if override else None


async def bootstrap(state: FactoryState) -> dict:
    started = time.monotonic()
    profile = get_profile(state["profile"])
    sandbox = sandbox_root() / state["run_id"]
    seed = seed_dir()
    source = seed or profile.scaffold_template

    with tracing.stage_span("bootstrap", sandbox=str(sandbox)):
        # Build outputs and the seed's own git history stay behind: each
        # run's audit trail starts at its own initial commit.
        shutil.copytree(
            source, sandbox,
            ignore=shutil.ignore_patterns(".git", "target", "*.log"),
        )
        git_ops.init_repo(sandbox)
        base_sha = git_ops.commit_all(
            sandbox,
            f"factory: {'seed from ' + str(seed) if seed else 'scaffold from template ' + profile.scaffold_template.name}",
            trailers={"Factory-Run-Id": state["run_id"]},
        )

    return {
        "sandbox": str(sandbox),
        "base_sha": base_sha,
        "head_sha": base_sha,
        "task_idx": 0,
        "attempts": 0,
        "stage_results": {"bootstrap": {"status": "ok", "sandbox": str(sandbox)}},
        "decisions": [
            record_decision(
                stage="bootstrap",
                decision=(
                    f"sandbox seeded from existing product at {seed}"
                    if seed
                    else f"sandbox created from {profile.scaffold_template.name}"
                ),
                rationale="seeded scaffold: the Maven wrapper jar is a binary "
                "an LLM cannot fabricate, so the template is copied, not generated",
                commit_sha=base_sha,
            )
        ],
        "metric_events": [
            metric_event(
                "stage_end",
                "bootstrap",
                ok=True,
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
