# Scenario artifacts

Each folder is the captured evidence of one assignment scenario: the full
run log (every stage, gate and diff shown to the human), the sandbox's
commit graph, reliability metrics, a Langfuse trace pointer, the product
source tree as the run left it, and a `product.bundle` (`git clone
product.bundle` reproduces the sandbox repository with every task branch
and merge).

The agent-generated code is also browsable as branches of this repository
— the integrated (merged-to-main) product history, one branch per run:

- `product/greenfield-498f963c` — the complete URL shortener
- `product/brownfield-2bfb10e8` — the shortener + per-day click analytics
- `product/ambiguous-a02b00b7` — the shortener + the reliability work that passed its gates (T1); the failing T2 was rolled back, never merged

`git checkout product/greenfield-498f963c` (or browse the branch on GitHub)
to review exactly what the factory built, task merge by task merge.

## greenfield — run `498f963c` (completed, release-ready)

"Build a URL shortener" with a pinned HTTP contract. Shows the full
pipeline: intake, spec (20 acceptance criteria), design, decomposition,
implementation, parallel verification (tests / policy / review), the
black-box acceptance suite against the running service, merge gates,
release readiness and the generated engineering summary (`summary.md`).
This run also exercised bounded retries, a rollback and a re-plan: its
early acceptance failures came from a host process squatting on port 8080,
which is why the product now pins 8188 and acceptance failures carry the
service boot log.

## brownfield — run `2bfb10e8` (completed, release-ready)

"Add per-day click analytics" on top of the greenfield product, seeded via
`FACTORY_SEED_DIR`. Shows brownfield classification, the read-only impact
stage (8 affected files, 6 regression risks identified before design), and
one scripted DESIGN-GATE REVISION — the upstream-change re-plan trigger:
the design re-ran with the human's edit folded in and downstream stages
were invalidated. Both tasks merged; the new `ClickEvent` entity,
repository and `GET /api/stats/{code}/daily` endpoint are in `product/`.

## ambiguous — run `a02b00b7` (safe-stopped by governance, as designed)

"Make it more reliable." Intake scored the request 0.95 ambiguity and
parked at the clarification gate; the answers (structured JSON errors,
restart-surviving persistence, DB-aware health endpoint) were folded back
in and re-intake classified the work brownfield at 0.15.

This is the one scenario whose ending is a control, not a merge — and that
is what it is kept to demonstrate. Over its lifetime the run exercised the
entire failure-governance stack: three bounded attempts on a task whose
integration test had a compile error, rollback to the last good merge, a
re-plan that was told what had already been delivered, an empty-diff
attempt rejected by the implement exit gate, T1 of the re-plan (structured
error handling) verified and merged through the human gate, a mid-flight
kill switch, and a checkpoint resume days later against a sandbox rebuilt
from `product.bundle` after `/tmp` had been wiped — the run picked up,
detected the restored tree carried no T2 changes, failed the attempt, and
spent T2's final attempt re-implementing.

That final attempt (file-based H2 persistence, T2) was rejected by the
run's own verification: the agent's new `RestartPersistenceIntegrationTest`
caught a real defect — after a simulated restart the code generator
re-issued an already-allocated code (the same counter-seeding weakness the
greenfield reviewer had flagged at the merge gate) — and the file-backed
datasource broke five other integration tests with context/file-lock
failures. Tests failed, the attempt rolled back, the replan budget was
exhausted, and the run safe-stopped rather than merge broken work or spend
past its cap: `run stopped: replan budget exhausted after rollback of T2`.

What survives is exactly what should: T1's reliability improvement is
merged on `main` (see `product/` and the bundle), the failing T2 work was
rolled back and never integrated, `run.log` carries the complete audit
trail from first clarification to safe-stop, and
`t2-verification-failure.txt` preserves the surefire reports of the tests
that vetoed the final attempt. $6.40 of the $10.00 budget was spent.
