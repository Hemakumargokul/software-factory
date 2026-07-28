"""Automated entry/exit gates: predicates on state, checked mechanically.

These are distinct from human approval checkpoints (M8 interrupts). A gate
is code: an entry condition says "this stage's inputs exist and are
coherent", an exit condition says "this stage produced what it promised".
A violated entry gate means the pipeline sequencing itself broke — that is
a bug or a stale decomposition, and the stage must not run.
"""

from dataclasses import dataclass
from typing import Callable

from factory.state import FactoryState

GateCheck = Callable[[FactoryState], str | None]  # None = ok, str = violation


@dataclass(frozen=True)
class StageGate:
    entry: GateCheck | None = None
    exit: GateCheck | None = None


class GateViolation(RuntimeError):
    """An automated gate failed; carries stage and violation text."""

    def __init__(self, stage: str, kind: str, violation: str):
        super().__init__(f"{stage} {kind} gate: {violation}")
        self.stage = stage
        self.kind = kind
        self.violation = violation


def _requirements_entry(state: FactoryState) -> str | None:
    intake = (state.get("stage_results") or {}).get("intake")
    if not intake:
        return "no intake result"
    return None


def _design_entry(state: FactoryState) -> str | None:
    spec = state.get("spec")
    if not spec:
        return "no spec"
    if not spec.get("acceptance_criteria"):
        return "spec has no acceptance criteria"
    return None


def _decompose_entry(state: FactoryState) -> str | None:
    if not state.get("design"):
        return "no design"
    return None


def _implement_entry(state: FactoryState) -> str | None:
    """Req 2 enforcement: a task may not start until every task it depends
    on has been integrated. Keeps the decomposition's dependency graph a
    real constraint rather than decoration."""
    tasks = state.get("tasks") or []
    idx = state.get("task_idx", 0)
    if idx >= len(tasks):
        return f"task_idx {idx} out of range ({len(tasks)} tasks)"
    task = tasks[idx]
    integrated = {t["id"] for t in tasks if t.get("status") == "integrated"}
    missing = [dep for dep in task.get("depends_on", []) if dep not in integrated]
    if missing:
        return f"task {task['id']} depends on unintegrated tasks: {missing}"
    return None


def _implement_exit(state: FactoryState) -> str | None:
    result = (state.get("stage_results") or {}).get("implement") or {}
    if not result.get("files"):
        return "implementer produced an empty diff"
    return None


GATES: dict[str, StageGate] = {
    "requirements": StageGate(entry=_requirements_entry),
    "design": StageGate(entry=_design_entry),
    "decompose": StageGate(entry=_decompose_entry),
    "implement": StageGate(entry=_implement_entry, exit=_implement_exit),
}


def check_entry(stage: str, state: FactoryState) -> None:
    gate = GATES.get(stage)
    if gate and gate.entry:
        violation = gate.entry(state)
        if violation:
            raise GateViolation(stage, "entry", violation)


def check_exit(stage: str, state: FactoryState) -> None:
    gate = GATES.get(stage)
    if gate and gate.exit:
        violation = gate.exit(state)
        if violation:
            raise GateViolation(stage, "exit", violation)
