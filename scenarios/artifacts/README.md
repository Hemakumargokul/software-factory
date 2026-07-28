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
- `product/ambiguous-a02b00b7` — the shortener + reliability work merged so far

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

## ambiguous — run `a02b00b7` (in progress; full run to be completed soon)

"Make it more reliable." Intake scored the request 0.95 ambiguity and
parked at the clarification gate; the answers (structured JSON errors,
restart-surviving persistence, DB-aware health endpoint) were folded back
in and re-intake classified the work brownfield at 0.15. The run has
already demonstrated the governance machinery under real failure: three
bounded attempts on a task whose integration test had a compile error,
rollback to the last good merge, a re-plan that was told what had already
been delivered, an empty-diff attempt rejected by the implement exit gate
and retried with that context, and T1 of the re-plan (structured error
handling) verified and merged. The run is currently parked mid-flight via
the kill switch — `status.txt` shows the checkpoint — and will be resumed
and completed soon with
`factory kill a02b00b7 --clear && factory resume a02b00b7`. Its captured
state meanwhile demonstrates that a run is a durable, controllable
artifact, not a process.
