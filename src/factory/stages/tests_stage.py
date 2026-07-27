"""Tests: the profile's build and unit-test commands against the sandbox.

The bar moves with maturity: on the first task a successful BUILD passes
the stage (there may be no tests yet), from the second task on, tests gate.
"""

import time

from factory import tracing
from factory.profiles import get_profile
from factory.state import FactoryState, metric_event


async def tests_stage(state: FactoryState) -> dict:
    from factory.stages.common import run_command

    started = time.monotonic()
    profile = get_profile(state["profile"])
    sandbox = state["sandbox"]
    first_task = state.get("task_idx", 0) == 0
    build_timeout = (
        profile.first_build_timeout_s if first_task else profile.build_timeout_s
    )

    with tracing.stage_span("tests", task_idx=state.get("task_idx", 0)) as span:
        build_ok, build_tail = run_command(profile.build_cmd, sandbox, build_timeout)
        tests_ok, tests_tail = (False, "build failed; tests not run")
        if build_ok:
            tests_ok, tests_tail = run_command(
                profile.test_cmd, sandbox, profile.build_timeout_s
            )

        passed = build_ok and (tests_ok or first_task)
        report = f"BUILD:\n{build_tail}\n\nTESTS:\n{tests_tail}"
        span.update(output={"build_ok": build_ok, "tests_ok": tests_ok})

    return {
        "stage_results": {
            "tests": {
                "status": "pass" if passed else "fail",
                "build_ok": build_ok,
                "tests_ok": tests_ok,
                "report": report,
            }
        },
        "metric_events": [
            metric_event(
                "verification",
                "tests",
                ok=passed,
                attempt=state.get("attempts", 0),
                duration_s=round(time.monotonic() - started, 3),
            )
        ],
    }
