"""Acceptance: boot the real service, run the black-box HTTP suite.

The suite lives in the ORCHESTRATOR repo (tests/acceptance), never in the
sandbox — the agent is told the API contract but never sees the exam.
Lifecycle: package, start in its own process group, poll health, run the
suite with the service URL in the environment, kill the process group in
a finally block no matter what happened.

With no suite present (before M11, or for profiles without one) the stage
reports "skipped" rather than silently passing.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from factory.observability import tracing
from factory.profiles import get_profile
from factory.stages.common import run_command
from factory.state import FactoryState, metric_event

ORCHESTRATOR_ROOT = Path(__file__).resolve().parents[3]
HEALTH_DEADLINE_S = 60
SUITE_TIMEOUT_S = 120


def acceptance_suite_dir() -> Path:
    override = os.environ.get("FACTORY_ACCEPTANCE_DIR")
    return Path(override) if override else ORCHESTRATOR_ROOT / "tests" / "acceptance"


def _suite_present(suite: Path) -> bool:
    return suite.is_dir() and any(suite.glob("test_*.py"))


def _wait_healthy(url: str, deadline_s: int) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    return False


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        group = os.getpgid(process.pid)
        os.killpg(group, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def acceptance(state: FactoryState) -> dict:
    started = time.monotonic()
    profile = get_profile(state["profile"])
    sandbox = state["sandbox"]
    suite = acceptance_suite_dir()

    def result(status: str, report: str) -> dict:
        return {
            "stage_results": {
                "acceptance": {"status": status, "report": report}
            },
            "metric_events": [
                metric_event(
                    "verification",
                    "acceptance",
                    ok=status in ("pass", "skipped"),
                    attempt=state.get("attempts", 0),
                    duration_s=round(time.monotonic() - started, 3),
                )
            ],
        }

    with tracing.stage_span("acceptance", suite=str(suite)) as span:
        if not _suite_present(suite):
            span.update(output="skipped: no suite")
            return result("skipped", f"no acceptance suite at {suite}")

        package_ok, package_tail = run_command(
            profile.package_cmd, sandbox, profile.first_build_timeout_s
        )
        if not package_ok:
            span.update(output="package failed")
            return result("fail", f"PACKAGE:\n{package_tail}")

        # Boot log lives NEXT TO the sandbox, not inside it, so it never
        # dirties the product tree. Its tail rides into the failure report
        # (and thus the retrying agent's prompt): a service that dies at
        # startup — port already bound, context failure — is otherwise
        # indistinguishable from one that is merely slow.
        boot_log = Path(f"{sandbox}.boot.log")
        with boot_log.open("w") as log_handle:
            service = subprocess.Popen(
                list(profile.run_cmd),
                cwd=sandbox,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # own process group: killable as a unit
            )
        try:
            if not _wait_healthy(profile.health_url, HEALTH_DEADLINE_S):
                tail = "\n".join(
                    boot_log.read_text(errors="replace").splitlines()[-40:]
                )
                exited = service.poll()
                detail = (
                    f"service exited with code {exited} before becoming healthy"
                    if exited is not None
                    else f"service never became healthy within {HEALTH_DEADLINE_S}s"
                )
                return result(
                    "fail",
                    f"{detail} (health: {profile.health_url})\n"
                    f"BOOT LOG (tail):\n{tail}",
                )

            base_url = f"http://127.0.0.1:{profile.service_port}"
            suite_ok, suite_tail = _run_suite(suite, base_url)
            span.update(output={"suite_ok": suite_ok})
            return result("pass" if suite_ok else "fail", f"SUITE:\n{suite_tail}")
        finally:
            _kill_process_group(service)


def _run_suite(suite: Path, base_url: str) -> tuple[bool, str]:
    """The black-box suite reads the service URL from the environment."""
    env = {**os.environ, "SHORTENER_URL": base_url}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", str(suite), "-q", "--no-header"],
            cwd=ORCHESTRATOR_ROOT,
            capture_output=True,
            text=True,
            timeout=SUITE_TIMEOUT_S,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT: acceptance suite exceeded {SUITE_TIMEOUT_S}s"
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, "\n".join(output.splitlines()[-100:])
