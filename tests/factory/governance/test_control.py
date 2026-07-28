"""Kill-switch primitives: sentinel-file flags shared across processes."""

import pytest

from factory.governance import control


@pytest.fixture(autouse=True)
def control_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_CONTROL_DIR", str(tmp_path / "control"))
    return tmp_path / "control"


def test_kill_flag_lifecycle():
    assert not control.is_killed("r1")
    control.request_kill("r1")
    assert control.is_killed("r1")
    assert not control.is_killed("r2")  # flags are per run
    control.clear_kill("r1")
    assert not control.is_killed("r1")


def test_kill_and_clear_are_idempotent():
    control.request_kill("r1")
    control.request_kill("r1")
    assert control.is_killed("r1")
    control.clear_kill("r1")
    control.clear_kill("r1")  # clearing a non-existent flag must not raise
    assert not control.is_killed("r1")
