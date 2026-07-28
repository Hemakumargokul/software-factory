"""Reliability metrics: persist run events to SQLite and compute reports.

The stages already emit `metric_events` into graph state; this module gives
them a life outside the checkpointer. `persist` snapshots a run's events
(idempotently — the CLI calls it at every pause and at the end), `compute`
turns them into the report the brief asks for: success rate, retries,
rollbacks, MTTR and latency with a per-stage breakdown.

MTTR here is wall-clock from a failing verification event to the next
passing event of the same stage — the time the run spent repairing itself,
which for this factory includes the re-implementation attempt in between.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DB_PATH = Path("runs/metrics.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    run_id   TEXT PRIMARY KEY,
    started  TEXT,
    finished TEXT,
    scenario TEXT,
    outcome  TEXT,
    goal     TEXT
);
CREATE TABLE IF NOT EXISTS events(
    run_id  TEXT,
    at      TEXT,
    kind    TEXT,
    stage   TEXT,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, at);
"""


@dataclass
class MetricsReport:
    run_id: str
    scenario: str | None
    outcome: str | None
    started: str | None
    finished: str | None
    end_to_end_s: float | None
    stage_durations: dict[str, float] = field(default_factory=dict)
    verification_passes: int = 0
    verification_failures: int = 0
    success_rate: float | None = None
    first_attempt_success_rate: float | None = None
    retries: int = 0
    rollbacks: int = 0
    mttr_s: float | None = None
    unresolved_failures: int = 0
    cost_usd: float = 0.0

    def scores(self) -> dict[str, float]:
        """The numeric subset worth attaching to the Langfuse trace."""
        out: dict[str, float] = {
            "retries": float(self.retries),
            "rollbacks": float(self.rollbacks),
        }
        if self.success_rate is not None:
            out["success_rate"] = round(self.success_rate, 4)
        if self.first_attempt_success_rate is not None:
            out["first_attempt_success_rate"] = round(
                self.first_attempt_success_rate, 4
            )
        if self.mttr_s is not None:
            out["mttr_s"] = round(self.mttr_s, 3)
        if self.end_to_end_s is not None:
            out["end_to_end_s"] = round(self.end_to_end_s, 3)
        return out


def _connect(db_path: Path | None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def persist(state: dict, outcome: str, db_path: Path | None = None) -> None:
    """Snapshot a run's metric events and metadata. Idempotent: events for
    the run are replaced wholesale, so calling at every gate pause and again
    at the end never duplicates anything."""
    events = state.get("metric_events") or []
    run_id = state["run_id"]
    timestamps = sorted(e["at"] for e in events)

    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO runs(run_id, started, finished, scenario, outcome, goal) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
            "finished=excluded.finished, scenario=excluded.scenario, "
            "outcome=excluded.outcome",
            (
                run_id,
                timestamps[0] if timestamps else None,
                timestamps[-1] if timestamps else None,
                state.get("scenario"),
                outcome,
                state.get("goal"),
            ),
        )
        conn.execute("DELETE FROM events WHERE run_id=?", (run_id,))
        conn.executemany(
            "INSERT INTO events(run_id, at, kind, stage, payload) VALUES(?,?,?,?,?)",
            [
                (run_id, e["at"], e["kind"], e["stage"], json.dumps(e["payload"]))
                for e in events
            ],
        )


def list_runs(db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT run_id, started, finished, scenario, outcome, goal "
            "FROM runs ORDER BY started"
        ).fetchall()
    keys = ("run_id", "started", "finished", "scenario", "outcome", "goal")
    return [dict(zip(keys, row)) for row in rows]


def compute(run_id: str, db_path: Path | None = None) -> MetricsReport:
    with _connect(db_path) as conn:
        run = conn.execute(
            "SELECT started, finished, scenario, outcome FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise KeyError(f"no metrics recorded for run {run_id}")
        rows = conn.execute(
            "SELECT at, kind, stage, payload FROM events WHERE run_id=? ORDER BY at",
            (run_id,),
        ).fetchall()

    started, finished, scenario, outcome = run
    events = [
        {"at": at, "kind": kind, "stage": stage, "payload": json.loads(payload)}
        for at, kind, stage, payload in rows
    ]
    report = MetricsReport(
        run_id=run_id,
        scenario=scenario,
        outcome=outcome,
        started=started,
        finished=finished,
        end_to_end_s=_seconds_between(started, finished),
    )

    verifications: list[dict] = []
    for event in events:
        payload = event["payload"]
        duration = payload.get("duration_s")
        if duration is not None:
            report.stage_durations[event["stage"]] = round(
                report.stage_durations.get(event["stage"], 0.0) + duration, 3
            )
        report.cost_usd = round(
            report.cost_usd + (payload.get("cost_usd") or 0.0), 4
        )
        if event["kind"] == "rollback":
            report.rollbacks += 1
        if event["kind"] == "stage_end" and event["stage"] == "implement":
            if payload.get("attempt", 1) > 1:
                report.retries += 1
        if event["kind"] == "verification":
            verifications.append(event)
            if payload.get("ok"):
                report.verification_passes += 1
            else:
                report.verification_failures += 1

    total = report.verification_passes + report.verification_failures
    if total:
        report.success_rate = report.verification_passes / total
    first = [v for v in verifications if v["payload"].get("attempt", 1) <= 1]
    if first:
        report.first_attempt_success_rate = sum(
            1 for v in first if v["payload"].get("ok")
        ) / len(first)

    report.mttr_s, report.unresolved_failures = _mttr(verifications)
    return report


def _mttr(verifications: list[dict]) -> tuple[float | None, int]:
    """Mean wall-clock from each failing verification to the next passing
    verification of the same stage. Failures that never recover (the run
    rolled back or safe-stopped) are counted, not averaged."""
    repair_times: list[float] = []
    open_failures: dict[str, str] = {}  # stage -> timestamp of first failure

    for event in verifications:
        stage = event["stage"]
        if event["payload"].get("ok"):
            if stage in open_failures:
                delta = _seconds_between(open_failures.pop(stage), event["at"])
                if delta is not None:
                    repair_times.append(delta)
        else:
            open_failures.setdefault(stage, event["at"])

    mttr = sum(repair_times) / len(repair_times) if repair_times else None
    return mttr, len(open_failures)


def _seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return (
        datetime.fromisoformat(end) - datetime.fromisoformat(start)
    ).total_seconds()
