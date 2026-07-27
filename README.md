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

To be filled in as milestones land: `factory run`, `factory approve`,
`factory status`, `factory metrics`.

## Layout

```
src/factory/          orchestrator (never edits product code)
templates/            product scaffolds seeded at bootstrap
tests/factory/        orchestrator unit tests (SDK roles mocked)
tests/acceptance/     black-box HTTP contract tests for the product
scenarios/            the three assignment scenarios plus captured artifacts
docs/                 design plan, implementation plan, deliverable docs
```
