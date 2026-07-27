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

Start with [docs/architecture.md](docs/architecture.md) (the system as
built), then [docs/testing-approach.md](docs/testing-approach.md).
[docs/plan.md](docs/plan.md) and
[docs/implementation-plan.md](docs/implementation-plan.md) are the original
design and build plans.

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
factory status  <run_id>                  # where is the run parked? spend so far?
factory approve <run_id>                  # approve the pending gate
factory approve <run_id> --revise "..."   # request changes / answer clarifications
factory approve <run_id> --reject         # reject at this gate
factory approve <run_id> --auto           # approve this and every later gate
factory run "..." --auto                  # unattended demo mode
factory run "..." --budget 5              # cap total agent spend at $5
factory kill <run_id>                     # kill switch, from any terminal
factory kill <run_id> --clear             # make a killed run resumable again
```

Every run carries an aggregate spend budget (`--budget`, or
`FACTORY_RUN_BUDGET_USD`, default $5; `0` disables). Every role invocation
reports its cost into the run's metric events; before dispatching more agent
work — another implementation attempt, the next task, a re-plan — the graph
checks the total and safe-stops with the spend stated if the cap is reached.
Work that already passed verification is never discarded by a budget stop,
and merging it is never blocked.

`factory kill` stops a run from outside its driving process: a running run
halts at the next stage boundary (everything up to there is checkpointed),
and a parked run refuses `approve`/`resume` until the kill is cleared. It is
reversible by design — kill parks the run, it does not destroy it. For an
immediate stop of the driving process itself, Ctrl-C is always safe.

A revision at the requirement gate re-runs `requirements` with your edits
and invalidates the design and task list; a design revision invalidates the
task list. Rejecting a merge rolls the work back and re-plans with your
stated reason as context.

Ctrl-C is always a safe stop: every superstep is checkpointed, so nothing
is lost but the stage that was mid-flight. `factory resume <run_id>`
continues from the last checkpoint (or re-displays a pending gate).

After a run:

```bash
factory metrics                # index of all runs
factory metrics <run_id>       # success rate, retries, rollbacks, MTTR, latency
```

## Observability (optional)

```bash
docker compose -f docker-compose.langfuse.yml up -d
```

That is the entire setup: the compose file pre-seeds the org, project, API
keys (matching `.env.example`) and a login — factory@example.com /
factory-dev-password at http://localhost:3000. Every run becomes a session
with stage spans, generation spans (model, tokens, cost), a span for every
tool call the implementer attempted (denials flagged), and reliability
scores. Without Docker the factory runs identically; tracing degrades to a
no-op. `scripts/langfuse_smoke.py` produces a full demo trace without
spending Claude quota.

## The three scenarios

```bash
scenarios/prime.sh                                   # warm the Maven cache once
scenarios/greenfield.sh                              # build the URL shortener
scenarios/brownfield.sh /tmp/factory/<greenfield-id> # extend it (incl. a design-gate revision)
scenarios/ambiguous.sh  /tmp/factory/<greenfield-id> # "Make it more reliable."
```

Each captures its artifacts (run log, sandbox commit graph, engineering
summary, metrics, trace pointer) into `scenarios/artifacts/<name>/`.

## Layout

```
src/factory/          orchestrator (never edits product code)
templates/            product scaffolds seeded at bootstrap
tests/factory/        orchestrator unit tests (SDK roles mocked)
tests/acceptance/     black-box HTTP contract tests for the product
scenarios/            the three assignment scenarios plus captured artifacts
docs/                 design plan, implementation plan, deliverable docs
```
