"""Policy branch of the verification fan-out: scan what the agent changed.

Read-only with respect to the working tree (it stages files for diffing,
which is also what commit wants). A violation is terminal for this task:
route_after_sync sends it to rollback WITHOUT consuming retries — retrying
policy violations would just ask the agent to hide them better.
"""

import time
from pathlib import Path

from factory import git_ops
from factory.observability import tracing
from factory.governance.policy_rules import scan_all, scan_external
from factory.profiles import get_profile
from factory.state import FactoryState, audit_event, metric_event


async def policy_stage(state: FactoryState) -> dict:
    started = time.monotonic()
    profile = get_profile(state["profile"])
    sandbox = Path(state["sandbox"])

    with tracing.stage_span("policy") as span:
        # Read-only diff: this runs concurrently with the review branch and
        # a staging diff would race on git's index.lock.
        diff = git_ops.diff_readonly(sandbox, state["base_sha"])
        violations = scan_all(
            diff,
            forbidden_patterns=profile.forbidden_patterns,
            dependency_files=profile.dependency_files,
            dependency_allowlist=profile.dependency_allowlist,
        )
        external, skipped = scan_external(sandbox, profile.external_scanners)
        violations.extend(external)
        span.update(
            output={"violations": len(violations), "skipped_scanners": skipped}
        )

    status = "violation" if violations else "pass"
    audit = [
        audit_event(
            "policy_scan",
            "policy",
            status=status,
            violations=[
                {"rule": v.rule, "detail": v.detail, "file": v.file}
                for v in violations
            ],
            skipped_scanners=skipped,  # a degraded scan is never silent
        )
    ]
    return {
        "stage_results": {
            "policy": {
                "status": status,
                "violations": [
                    {"rule": v.rule, "detail": v.detail, "file": v.file,
                     "line": v.line}
                    for v in violations
                ],
                "skipped_scanners": skipped,
                "report": "\n".join(
                    f"{v.rule}: {v.detail} ({v.file})" for v in violations
                ),
            }
        },
        "audit": audit,
        "metric_events": [
            metric_event(
                "verification",
                "policy",
                ok=status == "pass",
                attempt=state.get("attempts", 0),
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
