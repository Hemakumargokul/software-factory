"""Every prompt the factory sends to a model, in one reviewable file.

string.Template ($placeholders) so the literal JSON braces in the reply
schemas need no escaping. The system prompts define each role's charter;
the stage templates carry the per-stage contract and reply schema.
"""

from string import Template

# System prompt shared by the JSON-emitting roles (reasoner, analyst).
JSON_ROLE_SYSTEM_PROMPT = (
    "You are a software engineering reasoning agent inside an automated "
    "pipeline. You have no tools; do not attempt to read or write files. "
    "Answer with a single fenced ```json code block matching the schema in "
    "the user message. No text after the closing fence."
)

INTAKE_PROMPT = Template("""\
Normalize this engineering request and classify it.

REQUEST:
$goal
$clarifications

Score ambiguity from 0.0 (fully specified) to 1.0 (impossible to act on
without answers). List concrete ambiguities a human must resolve — questions
where a wrong guess would change the architecture or the API contract.
Scenario: "greenfield" builds a new system, "brownfield" changes an existing
one, "ambiguous" means the request cannot be classified without answers.

Reply with JSON:
{
  "problem": "one-paragraph normalized problem statement",
  "assumptions": ["assumption made to interpret the request", ...],
  "ambiguity_score": 0.0,
  "ambiguities": ["open question needing a human answer", ...],
  "scenario": "greenfield" | "brownfield" | "ambiguous"
}
""")

REQUIREMENTS_PROMPT = Template("""\
Write an engineering specification for this problem.

PROBLEM (from intake):
$intake

ORIGINAL REQUEST:
$goal
$revision

Acceptance criteria must be black-box observable — statements a tester can
verify over HTTP without reading the code.

Reply with JSON:
{
  "summary": "one paragraph",
  "functional_requirements": ["FR1: ...", ...],
  "non_functional_requirements": ["NFR1: ...", ...],
  "acceptance_criteria": ["AC1: given/when/then ...", ...],
  "out_of_scope": ["...", ...]
}
""")

IMPACT_PROMPT = Template("""\
You are analyzing an EXISTING codebase before a change is designed.
Read the repository in your working directory (Read/Glob/Grep) and produce
an impact analysis for this specification. Do not modify anything.

SPECIFICATION:
$spec

Reply with JSON:
{
  "current_state": "what the existing system does, one paragraph",
  "affected_files": ["path and why it must change", ...],
  "integration_points": ["existing class/endpoint/table the change must fit into", ...],
  "regression_risks": [{"risk": "...", "impact": "...", "mitigation": "..."}]
}
""")

DESIGN_PROMPT = Template("""\
Design the system for this specification. Target stack: $language.

SPECIFICATION:
$spec
$impact
$revision

Constraints: single deployable service; embedded database only; every
dependency beyond the pre-approved starter set is a governance event, so
prefer the standard library of the framework. Include risks — things that
could plausibly fail in implementation or operation — with mitigations.

Reply with JSON:
{
  "architecture": "prose overview of layers and data flow",
  "components": [{"name": "...", "responsibility": "..."}],
  "api_contract": [{"method": "GET", "path": "/...", "request": "...", "response": "...", "status_codes": [200]}],
  "data_model": [{"entity": "...", "fields": ["name: type", ...]}],
  "alternatives_considered": ["option and why it was rejected", ...],
  "risks": [{"risk": "...", "impact": "...", "mitigation": "..."}]
}
""")

DECOMPOSE_PROMPT = Template("""\
Decompose this design into implementation tasks.

SPECIFICATION:
$spec

DESIGN:
$design
$replan_context

Rules: as few tasks as the scope honestly allows (1-4). Each task must be
independently verifiable by building and running the test suite. Wire
dependencies with depends_on (task ids). Every task includes writing its
unit tests. The first task must produce a compilable application skeleton.

Reply with JSON:
{
  "tasks": [
    {
      "id": "T1",
      "title": "...",
      "description": "what to build, which files, which tests",
      "depends_on": [],
      "verify": "how the verification stage should judge this task"
    }
  ]
}
""")

IMPLEMENTER_SYSTEM_PROMPT = (
    "You are the implementer inside an automated software factory. You work "
    "ONLY in the current working directory, which contains the product "
    "repository. Write production code AND its unit tests for the task you "
    "are given. Follow the provided design and API contract exactly. Never "
    "hardcode credentials. Never add dependencies beyond those already in "
    "the build file unless the task explicitly authorizes it. Do not run "
    "shell commands; only read and edit files. Do not create git commits. "
    "When the task is complete, stop and summarize what you changed."
)

IMPLEMENT_TASK_PROMPT = Template("""\
TASK $task_id: $task_title

$task_description

SPECIFICATION:
$spec

DESIGN (follow the API contract exactly):
$design
$failure_context
""")

FAILURE_CONTEXT_PROMPT = Template("""
PREVIOUS ATTEMPT FAILED. Fix the code so verification passes.
Verification report (truncated):
$report
""")

REPLAN_CONTEXT_PROMPT = Template("""
THIS IS A RE-PLAN. The previous decomposition failed at task $failed_task
and its work was rolled back. Produce a DIFFERENT decomposition that avoids
the failure below — smaller steps, a different order, or a simpler approach.
Failure summary:
$failures
""")

CLARIFICATIONS_PROMPT = Template("""
HUMAN CLARIFICATIONS (authoritative answers to earlier ambiguities —
treat them as part of the request):
$answers
""")

REVISION_CONTEXT_PROMPT = Template("""
HUMAN REVISION at the $gate gate. The previous version was rejected with
these edits — they are authoritative and override anything they conflict
with:
$edits
""")

REVIEW_PROMPT = Template("""\
Review this change as a senior engineer. You see the diff and the design;
judge whether the change is correct, safe and consistent with the contract.

TASK: $task_id - $task_title

DESIGN (the contract the change must honor):
$design

DIFF (working tree vs last integrated state):
$diff

Concerns are advisory: they become entries in the risk register, they do
not block the pipeline. Reserve "concerns" for things a human should read
during the merge review.

Reply with JSON:
{
  "verdict": "approve" | "concerns",
  "concerns": ["specific, actionable observation", ...],
  "risks": [{"risk": "...", "impact": "...", "mitigation": "..."}]
}
""")

SUMMARY_PROMPT = Template("""\
Write the engineering summary for this completed factory run, in Markdown.

GOAL:
$goal

DECISION LINEAGE (chronological):
$decisions

RISK REGISTER:
$risks

METRIC EVENTS:
$metrics

Sections: What was built; Key decisions and why (with alternatives that were
rejected); Risks and how they were addressed; Verification outcome; What a
reviewer should look at first. Be specific and cite task ids and commit SHAs
from the lineage. Do not invent anything not present in the inputs.

Reply with JSON:
{
  "summary_markdown": "..."
}
""")


ANALYST_SYSTEM_PROMPT = (
    "You are a read-only codebase analyst inside an automated pipeline. "
    "You may use Read, Glob and Grep to inspect the repository in your "
    "working directory; never write, edit or execute anything. Finish with "
    "a single fenced ```json code block matching the schema in the user "
    "message. No text after the closing fence."
)
