"""Human approval checkpoints: interrupt-only nodes.

THE RULE: nothing happens before interrupt() except reading state. When a
graph resumes, the interrupted node re-executes from its top, so any side
effect placed before the interrupt would run twice. These nodes read
state, ask, record the answer — routing is the router's job.

Resume values (from `factory approve`):
  {"action": "approve"}
  {"action": "reject"}
  {"action": "revise", "edits": "..."}   (clarify treats edits as answers)

Revise semantics (the "upstream changed" half of re-planning): a revised
spec invalidates design and tasks; a revised design invalidates tasks.
Invalidation is a None write through the stage_results merge reducer, and
is itself recorded as a decision — history is never erased.
"""

from pathlib import Path

from langgraph.types import interrupt

from factory import git_ops
from factory.state import FactoryState, record_decision

DIFF_LIMIT = 12000


def _answer_of(raw: object) -> tuple[str, str]:
    if not isinstance(raw, dict):
        return "reject", ""
    action = raw.get("action", "reject")
    if action not in ("approve", "reject", "revise"):
        action = "reject"  # unknown answers fail safe
    return action, str(raw.get("edits", ""))


async def gate_requirements(state: FactoryState) -> dict:
    answer = interrupt(
        {
            "gate": "requirements",
            "question": "Approve the specification?",
            "spec": state.get("spec"),
            "open_ambiguities": state.get("ambiguities", []),
        }
    )
    action, edits = _answer_of(answer)

    update: dict = {
        "stage_results": {"gate_requirements": {"action": action, "edits": edits}},
        "decisions": [
            record_decision(
                stage="gate_requirements",
                decision=f"human: {action}",
                rationale=edits or "requirement sign-off checkpoint",
            )
        ],
    }
    if action == "revise":
        # Upstream truth changed: everything derived from the spec is stale.
        update["stage_results"].update({"design": None, "tasks": None})
        update["decisions"].append(
            record_decision(
                stage="gate_requirements",
                decision="invalidated downstream stages: design, tasks",
                rationale="spec revision makes derived artifacts stale; "
                "they will re-run against the revised spec",
            )
        )
    return update


async def gate_design(state: FactoryState) -> dict:
    answer = interrupt(
        {
            "gate": "design",
            "question": "Approve architecture, API contract and data model?",
            "design": state.get("design"),
            "risks": state.get("risks", []),
        }
    )
    action, edits = _answer_of(answer)

    update: dict = {
        "stage_results": {"gate_design": {"action": action, "edits": edits}},
        "decisions": [
            record_decision(
                stage="gate_design",
                decision=f"human: {action}",
                rationale=edits or "design sign-off checkpoint",
            )
        ],
    }
    if action == "revise":
        update["stage_results"].update({"tasks": None})
        update["decisions"].append(
            record_decision(
                stage="gate_design",
                decision="invalidated downstream stage: tasks",
                rationale="design revision makes the decomposition stale",
            )
        )
    return update


async def gate_merge(state: FactoryState) -> dict:
    """Merge to main is the highest-impact action in the run: the human
    sees the diff, the verification verdicts and the reviewer's concerns."""
    task = state["tasks"][state["task_idx"]]
    results = state.get("stage_results") or {}
    diff = git_ops.diff_readonly(Path(state["sandbox"]), state["base_sha"])
    if len(diff) > DIFF_LIMIT:
        diff = diff[:DIFF_LIMIT] + "\n...[diff truncated]"

    answer = interrupt(
        {
            "gate": "merge",
            "question": f"Merge {task['id']} ({task['title']}) to main?",
            "diff": diff,
            "tests": (results.get("tests") or {}).get("status"),
            "policy": (results.get("policy") or {}).get("status"),
            "review_concerns": (results.get("review") or {}).get("concerns", []),
            "commit_sha": state.get("head_sha"),
        }
    )
    action, edits = _answer_of(answer)
    if action == "revise":
        action = "reject"  # merge is binary; edits belong at earlier gates

    return {
        "stage_results": {"gate_merge": {"action": action, "edits": edits}},
        "decisions": [
            record_decision(
                stage="gate_merge",
                decision=f"human: {action} merge of {task['id']}",
                rationale=edits or "merge-to-main checkpoint",
                commit_sha=state.get("head_sha"),
            )
        ],
    }


async def clarify(state: FactoryState) -> dict:
    """Ambiguous intake: ask the human the open questions, fold the answers
    back, and loop to intake. One round — a request that stays ambiguous
    after human answers proceeds on recorded assumptions."""
    answer = interrupt(
        {
            "gate": "clarify",
            "question": "The request is ambiguous; please answer:",
            "questions": state.get("ambiguities", []),
        }
    )
    raw = answer if isinstance(answer, dict) else {}
    answers = str(raw.get("answers") or raw.get("edits") or "")

    return {
        "stage_results": {"clarify": {"answers": answers}},
        "decisions": [
            record_decision(
                stage="clarify",
                decision="human answered clarification questions",
                rationale=answers[:500],
            )
        ],
    }
