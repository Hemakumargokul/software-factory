# Engineering Summary: URL Shortener Web Service

## What Was Built

A Spring Boot 3 (Java 21) web service implementing a URL shortener with a strict, fixed HTTP contract, deployed on the existing Maven-wrapper scaffold (`java-springboot` template, commit `6cf443a7000278a6a6ec7f61f206a41d7b21ae83`), listening on port 8188:

- **POST /api/shorten** — accepts `{"url": "<http/https URL>"}`, returns 201 with `{"code", "url"}`; deduplicates identical URLs to the same code (200/201); rejects other schemes, malformed URLs, or missing fields with 400 JSON error bodies.
- **GET /{code}** — issues a 301/302 redirect with `Location` set to the original URL; 404 for unknown codes.
- **GET /api/stats/{code}** — returns `{"code", "url", "clicks"}`; 404 for unknown codes.
- **Rate limiting** — a hand-rolled per-IP limiter caps POST /api/shorten at 30 req/sec, returning 429 beyond the cap.
- Persistence via embedded H2 (file-based mode), with `/actuator/health` kept functional.

The work was delivered as a single task, **T1**, which ended up covering the full scope after a scope-completion iteration (see below).

## Key Decisions and Why (with Rejected Alternatives)

- **Single Spring Boot deployable, layered design** (Web controllers → Filter → Service → Repository) rather than a more distributed setup, matching the single-instance assumption baked into the requirements.
- **Hand-rolled rate limiter** using an in-process `ConcurrentHashMap` with a fixed 1-second window per IP.
  - *Rejected*: Bucket4j/Resilience4j — spec mandates no new dependencies for rate limiting.
  - *Rejected*: Redis/external cache for counters — conflicts with the single embedded-database constraint.
  - *Rejected*: sliding-log/token-bucket algorithms — fixed 1-second window chosen for simplicity, explicitly allowed by FR10.
- **Base62 counter/random hybrid for short codes**, not UUID substrings.
  - *Rejected*: UUID truncation — not guaranteed collision-free and doesn't cleanly map to the fixed 7-char base62 alphabet requirement (FR4).
- **Case-sensitive, unnormalized exact-string dedup** on the original URL.
  - *Rejected*: normalizing/lowercasing URLs before dedup — FR2 explicitly mandates exact-match semantics.
- **IP extraction via `HttpServletRequest.getRemoteAddr()` only.**
  - *Rejected*: honoring `X-Forwarded-For` — explicitly excluded by FR11.
- **File-based embedded H2** (`jdbc:h2:file:./data/urlshortener`) rather than in-memory H2.
  - *Rejected*: in-memory H2 — chosen against because it wouldn't survive restarts, and FR13's persistence intent is better served by a file-backed store while staying within the "embedded database only" constraint.
- **Manual validation via `java.net.URI`** instead of adding `spring-boot-starter-validation`.
  - *Rejected*: validation starter — avoided to keep the dependency set unchanged from the approved scaffold.

## Risks and How They Were Addressed

- **Dedup race condition** (concurrent identical POSTs creating duplicate mappings): mitigated by a unique DB constraint on the URL column, a single `@Transactional` check-then-insert method, and re-querying on `DataIntegrityViolationException` to return the winning row's code.
- **Code generation collisions** under concurrent creation: mitigated via a unique constraint on the code column plus a bounded retry loop in the code generator, favoring a monotonic/DB-backed counter over pure randomness.
- **Unbounded rate-limiter memory growth / fixed-window edge bursts**: mitigated by periodic sweeping of stale entries (age > 2s) and explicit documentation of fixed-window edge-burst behavior as an accepted tradeoff.
- **Single-instance-only assumption** for rate limiter and H2 store: documented explicitly as a deployment assumption; file-based H2 chosen so restarts on the same host preserve data.
- **Malformed JSON / wrong content-type bypassing custom error shape**: mitigated with a `@ControllerAdvice`/`@ExceptionHandler` mapping `HttpMessageNotReadableException` (and similar) to the standard `{"error": ...}` 400 JSON shape.
- **Lenient/inconsistent URI scheme validation** (`java.net.URI` edge cases): mitigated by explicit scheme checks (`http`/`https` case-insensitive) and `URI.isAbsolute()` after parsing, with targeted tests for rejected schemes (javascript:, data:, ftp:, schemeless).
- **Non-atomic click-count increment** causing lost updates under concurrent redirects: mitigated by a single `@Transactional` `UPDATE ... SET click_count = click_count + 1 WHERE code = ?` rather than load-increment-save.

## Verification Outcome

- **Attempt 1** (11 files, then found insufficient): tests passed, policy passed, review returned "concerns" (6), but **acceptance failed** — the first attempt only delivered the persistence/codegen skeleton (entity, repository, code generator, actuator health) with no HTTP endpoints, so the full contract suite returned 404s across the board.
- **Attempt 2** (26 files changed, adding the web/service/rate-limit layers on top of the unmodified T1 persistence/codegen layer): tests passed, policy passed, review returned "concerns" (7, one more than attempt 1, not itemized in the provided lineage), and **acceptance passed**.
- T1 was committed at `44951866b50b0f4da0fb1be7d7876c34b239dc41` after verification passed, approved at the `gate_merge` checkpoint, and merged to main via a no-fast-forward merge at `e027de5fb5800aec5c086e7a3b928382acd82740`.
- Both human gate checkpoints (`gate_requirements`, `gate_design`) were approved without requested changes, and `gate_merge` for T1 was approved.

## What a Reviewer Should Look at First

1. **The 7 open "concerns" from the attempt-2 code review** — these passed the merge gate but were not resolved to zero; the specific concerns are not detailed in the truncated lineage and should be pulled from the full review artifact.
2. **The gap between attempt 1 and attempt 2** — attempt 1 shipped only the skeleton (entity/repository/codegen/health) and omitted all HTTP endpoints entirely, which acceptance testing caught but code review did not flag as blocking; worth checking whether review criteria adequately cover "endpoint existence" going forward.
3. **Rate limiter correctness under load** — the fixed 1-second window design has documented edge-burst behavior (potential admission of requests slightly over 30/sec at window boundaries); confirm this is acceptable for production use.
4. **File-based H2 persistence path** (`./data/urlshortener`) — verify this is correctly gitignored/excluded from the deployable artifact and doesn't leak stale data across environments.
5. **Dedup and code-generation concurrency handling** — verify the unique-constraint-plus-retry approach was actually implemented as designed in T1's second attempt, since this was called out as a high-impact risk in the design stage.