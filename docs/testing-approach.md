# Testing approach

Three test suites with different jobs, different authors and different trust
models. The separation is the point: the entity that writes the code must not
be the entity that grades it.

## 1. Orchestrator unit tests (`tests/factory/`)

Written by hand, run with plain `pytest`, no Claude auth, no Docker, no
network, no JDK. The SDK roles are mocked to canned JSON per stage
(`tests/factory/conftest.py`), which makes the entire graph deterministic:
the full spine runs end to end against a temp sandbox with real git in
milliseconds.

Covered: state reducers and invalidation, git operations (against real temp
repositories), role configuration and the fallback ladder, permission
callback (sandbox escapes, protected paths), policy scanners (secrets,
dependency allowlist, forbidden constructs, gitleaks integration), automated
entry/exit gates, every conditional-edge router as a pure function, the
retry → rollback → re-plan → safe-stop loop, human gates (pause, revise with
downstream invalidation, reject, clarify), checkpointer persistence across
simulated process restarts, and metrics computation including MTTR over
interleaved failures.

Deliberately not covered here: model behavior. These tests prove the machine
around the model; they cannot prove the model writes good Java.

## 2. Black-box acceptance suite (`tests/acceptance/`)

Written by hand against the pinned HTTP contract, run by the acceptance
stage against the live service (packaged jar, own process group, health-poll,
teardown in `finally`). The base URL arrives via `SHORTENER_URL`.

The suite gates only the plan's FINAL task. It exams the entire contract,
so an honest mid-plan increment would always flunk it — and every acceptance
failure buys another paid implementation attempt. Intermediate tasks are
judged by compile + unit tests + policy + review; the last task must leave
the whole specification working end to end (the decompose prompt says so
explicitly). If the service dies at boot, the failure report carries the
tail of its boot log, so a bind failure or context error is visible to the
human and to the retrying agent alike.

Trust model: the suite lives in the orchestrator repo and never enters the
sandbox. The agent is told the contract (it is pinned verbatim in the
scenario goal) but never sees the exam. It asserts contract shape (status
codes, JSON fields, redirect Location), idempotent re-shortening, security
behavior (scheme allowlist rejecting `javascript:`/`data:`/`ftp:`, malformed
input), click accounting, and rate limiting under burst.

## 3. Agent-written unit tests (inside the product)

The decompose stage requires every task to include its own unit tests; the
tests stage runs the profile's build and test commands. These tests are
written by the same agent that wrote the code, so they are treated as a
smoke signal, not as verification — the independent layers above and the
policy/review branches are what gate the merge.

## Limitations and trade-offs, stated honestly

- **The suite-contract coupling is manual.** The acceptance suite asserts the
  contract the scenario goal pins. If a human edits one without the other,
  acceptance fails correctly but confusingly. A contract file consumed by
  both would be the next step.
- **Brownfield acceptance only regression-tests the original contract.** New
  endpoints added by a brownfield run are verified by agent-written tests
  and review, not by an independent suite extension.
- **Parallel verification is read-only by design.** The three branches share
  one working tree; this is safe precisely because none of them writes. The
  more ambitious alternative — parallel implementation in git worktrees with
  merge reconciliation — was scoped out deliberately.
- **Model nondeterminism.** Two runs of the same scenario can produce
  different designs, task splits and code. The tests pin the machine, the
  gates and budgets bound the behavior, and the lineage records what
  actually happened — but bit-identical reproducibility is not a goal.
- **MTTR is coarse.** It measures wall-clock from a failing verification
  event to the next passing one per stage, which folds the re-implementation
  attempt into "repair time". That matches how this factory repairs itself,
  but it is not comparable to human-oncall MTTR.
- **The rate-limit acceptance test is timing-sensitive.** A 100-request
  burst against a 30 req/s cap trips reliably on a local machine; on a
  drastically slower machine the burst could in principle stay under the
  cap. The threshold was chosen to keep the margin wide.

## Running everything

```bash
pytest                        # orchestrator suite: fast, no dependencies
pytest tests/acceptance       # needs SHORTENER_URL pointing at a live service
scenarios/greenfield.sh       # full real run, captures artifacts
```
