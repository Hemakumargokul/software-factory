# Software Factory

An agentic SDLC orchestrator. LangGraph coordinates a seven-stage lifecycle
(requirements, design, decomposition, implementation, verification,
documentation, release readiness) with entry/exit gates, parallel
verification, bounded retries, rollback, human approval checkpoints and
audit-grade traceability. All file edits are delegated to the Claude Agent
SDK under mechanically enforced permissions. Git is the safety net; Langfuse
is the flight recorder.

The factory is language-agnostic through project profiles. The demo profile
builds a Java Spring Boot URL shortener.

See [docs/plan.md](docs/plan.md) for the design and
[docs/implementation-plan.md](docs/implementation-plan.md) for the build plan.

## Prerequisites

- Python 3.11+
- JDK 21+ (Maven is not required; the wrapper ships in the scaffold template)
- Claude Code auth: `claude /login` with a Pro/Max subscription, or `ANTHROPIC_API_KEY`
- Docker (optional, only for Langfuse tracing)
- gitleaks (optional, `brew install gitleaks`) — extra secret-scanning layer
  in the policy stage; without it the built-in regex scanners still run

> Auth gotcha: a set `ANTHROPIC_API_KEY` (even a stale one) silently
> overrides subscription auth in non-interactive mode. If you hit
> authentication errors, `unset ANTHROPIC_API_KEY`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Run the orchestrator's own tests (no Claude auth, Docker or network needed):

```bash
pytest
```

## Usage

```bash
factory run "Build a URL shortener: POST /shorten, GET /{code} redirects"
```

Every run is a checkpointed thread in `runs/checkpoints.db`. The run pauses
at each human gate (requirement sign-off, design sign-off, merge to main —
plus a clarification gate when the request is ambiguous), prints the
artifact under review, and exits with a run id. From any process, any time
later:

```bash
factory status  <run_id>                  # where is the run parked?
factory approve <run_id>                  # approve the pending gate
factory approve <run_id> --revise "..."   # request changes / answer clarifications
factory approve <run_id> --reject         # reject at this gate
factory approve <run_id> --auto           # approve this and every later gate
factory run "..." --auto                  # unattended demo mode
```

A revision at the requirement gate re-runs `requirements` with your edits
and invalidates the design and task list; a design revision invalidates the
task list. Rejecting a merge rolls the work back and re-plans with your
stated reason as context.

Ctrl-C is always a safe stop: every superstep is checkpointed, so nothing
is lost but the stage that was mid-flight. `factory resume <run_id>`
continues from the last checkpoint (or re-displays a pending gate).

## Layout

```
src/factory/          orchestrator (never edits product code)
templates/            product scaffolds seeded at bootstrap
tests/factory/        orchestrator unit tests (SDK roles mocked)
tests/acceptance/     black-box HTTP contract tests for the product
scenarios/            the three assignment scenarios plus captured artifacts
docs/                 design plan, implementation plan, deliverable docs
```
