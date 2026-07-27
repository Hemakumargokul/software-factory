"""M9 tests: metrics persistence and the computed report, including MTTR
over interleaved failures."""

import pytest

from factory import metrics


def ev(at: str, kind: str, stage: str, **payload) -> dict:
    return {"at": f"2026-07-27T10:{at}+00:00", "kind": kind, "stage": stage,
            "payload": payload}


def make_state(run_id: str, events: list[dict]) -> dict:
    return {"run_id": run_id, "goal": "demo goal", "scenario": "greenfield",
            "metric_events": events}


@pytest.fixture
def db(tmp_path):
    return tmp_path / "metrics.db"


def test_persist_is_idempotent_and_listable(db):
    events = [ev("00:00", "stage_end", "intake", ok=True, duration_s=2.0)]
    metrics.persist(make_state("r1", events), "paused", db_path=db)
    # Second snapshot with more events replaces, never duplicates
    events.append(ev("01:00", "stage_end", "design", ok=True, duration_s=3.0))
    metrics.persist(make_state("r1", events), "finished", db_path=db)

    runs = metrics.list_runs(db_path=db)
    assert len(runs) == 1
    assert runs[0]["outcome"] == "finished"
    assert runs[0]["started"].startswith("2026-07-27T10:00:00")
    assert runs[0]["finished"].startswith("2026-07-27T10:01:00")

    report = metrics.compute("r1", db_path=db)
    assert report.stage_durations == {"intake": 2.0, "design": 3.0}
    assert report.end_to_end_s == 60.0


def test_compute_unknown_run_raises(db):
    with pytest.raises(KeyError, match="no metrics"):
        metrics.compute("ghost", db_path=db)


def test_success_rates_retries_and_rollbacks(db):
    events = [
        ev("00:00", "stage_end", "implement", ok=True, attempt=1, cost_usd=0.5,
           duration_s=60.0),
        ev("01:00", "verification", "tests", ok=False, attempt=1, duration_s=30.0),
        ev("01:00", "verification", "policy", ok=True, attempt=1, duration_s=5.0),
        ev("02:00", "stage_end", "implement", ok=True, attempt=2, cost_usd=0.4,
           duration_s=50.0),
        ev("03:00", "verification", "tests", ok=True, attempt=2, duration_s=30.0),
        ev("03:00", "verification", "policy", ok=True, attempt=2, duration_s=5.0),
        ev("04:00", "rollback", "rollback", task="T2", duration_s=1.0),
    ]
    metrics.persist(make_state("r2", events), "finished", db_path=db)
    report = metrics.compute("r2", db_path=db)

    assert report.verification_passes == 3
    assert report.verification_failures == 1
    assert report.success_rate == 0.75
    assert report.first_attempt_success_rate == 0.5  # tests failed, policy passed
    assert report.retries == 1        # the attempt=2 implement
    assert report.rollbacks == 1
    assert report.cost_usd == 0.9
    assert report.stage_durations["implement"] == 110.0


def test_mttr_with_interleaved_failures(db):
    """tests fails at 01:00 and recovers at 05:00 (240s); acceptance fails at
    02:00 and recovers at 04:00 (120s) — interleaved episodes are tracked
    per stage, so MTTR is (240 + 120) / 2."""
    events = [
        ev("01:00", "verification", "tests", ok=False, attempt=1),
        ev("02:00", "verification", "acceptance", ok=False, attempt=1),
        ev("04:00", "verification", "acceptance", ok=True, attempt=2),
        ev("05:00", "verification", "tests", ok=True, attempt=2),
    ]
    metrics.persist(make_state("r3", events), "finished", db_path=db)
    report = metrics.compute("r3", db_path=db)

    assert report.mttr_s == 180.0
    assert report.unresolved_failures == 0


def test_mttr_counts_unrecovered_failures_without_averaging_them(db):
    """A repeated failure keeps the FIRST failure time (that's when repair
    began); a stage that never recovers is reported, not averaged in."""
    events = [
        ev("01:00", "verification", "tests", ok=False, attempt=1),
        ev("02:00", "verification", "tests", ok=False, attempt=2),
        ev("03:00", "verification", "tests", ok=True, attempt=3),
        ev("04:00", "verification", "acceptance", ok=False, attempt=3),
        # run safe-stops; acceptance never passes
    ]
    metrics.persist(make_state("r4", events), "safe_stopped", db_path=db)
    report = metrics.compute("r4", db_path=db)

    assert report.mttr_s == 120.0  # 01:00 -> 03:00, one episode
    assert report.unresolved_failures == 1
    assert report.outcome == "safe_stopped"


def test_scores_expose_the_numeric_subset(db):
    events = [
        ev("00:00", "verification", "tests", ok=True, attempt=1, duration_s=10.0),
    ]
    metrics.persist(make_state("r5", events), "finished", db_path=db)
    scores = metrics.compute("r5", db_path=db).scores()

    assert scores["success_rate"] == 1.0
    assert scores["first_attempt_success_rate"] == 1.0
    assert scores["retries"] == 0.0
    assert scores["rollbacks"] == 0.0
    assert "mttr_s" not in scores  # nothing failed, nothing repaired
