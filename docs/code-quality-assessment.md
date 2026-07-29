# Code quality assessment: the generated URL shortener

An honest review of the product the factory built, judged against a
"productionalizable code" bar. The code under review is the final state after
the greenfield and brownfield runs: 19 main classes (~700 lines) and 8 test
classes (60 test methods, ~1,200 lines), captured at
`scenarios/artifacts/brownfield/product/`.

**Verdict: solidly mid-level production-shaped code — strong architecture,
genuinely correct concurrency handling, and unusually good test discipline,
but with real production gaps in persistence, observability, and horizontal
scale.** For an agent-built product in a 2–3 day assignment window it holds
up well; it belongs in staging, not production, as-is. The gaps are listed
below, unvarnished — several were caught by the factory's own review stage,
which is itself part of the story.

## What's genuinely good

### 1. Clean layered architecture

Proper separation — `web` (controllers + DTOs) → `service` (business logic +
domain exceptions) → `repository` → `domain` (JPA entities). Constructor
injection throughout, no field injection, DTOs kept at the boundary
(`ShortenResponse`, `StatsResponse`, `ErrorResponse`), exceptions translated
centrally in a `@RestControllerAdvice`. Textbook Spring structure with
nothing clever or tangled.

### 2. Race conditions handled at the database level

The failure mode of most quickly-written CRUD code is check-then-act
concurrency. This code gets it right: unique constraints on both `code` and
`original_url` are the arbiters, and the service catches
`DataIntegrityViolationException` and distinguishes "lost the URL dedup race"
(return the winner's code) from "lost the code race" (retry with a new code),
inside a bounded loop:

```java
// UrlShortenerService.createNew — bounded retry, DB constraint as arbiter
for (int attempt = 0; attempt < MAX_CODE_GENERATION_ATTEMPTS; attempt++) {
    String code = codeGenerator.nextCode();
    try {
        UrlMapping saved = repository.saveAndFlush(new UrlMapping(code, url));
        return new ShortenResult(saved.getCode(), saved.getOriginalUrl(), true);
    } catch (DataIntegrityViolationException e) {
        var existing = repository.findByOriginalUrl(url);
        if (existing.isPresent()) { /* dedup onto the winner */ }
        // otherwise retry with a new code
    }
}
```

Named unique constraints, explicit indexes, and column lengths on the entity
round it out.

### 3. The hand-rolled rate limiter is defensible engineering

Fixed 1-second window per IP, `synchronized` window rollover, opportunistic
eviction of stale IP entries so memory stays bounded, only `POST
/api/shorten` matched, chain correctly halted on 429 with a JSON error body.
The tests prove all of it — including the subtle case that an IP which has
saturated its shorten budget can still hit `GET /{code}` and
`/actuator/health`.

### 4. Test discipline is the standout

60 test methods, a ~1.7:1 test-to-code ratio, and they are *behavioral*: the
filter is exercised through real `doFilter` calls with mock servlet objects,
window reset is tested with an actual sleep, the 429 body is parsed as JSON
and asserted non-blank, dedup and click-count semantics are verified through
the Spring context. On top of that, the orchestrator's hand-written black-box
acceptance suite (which the coding agent never saw — see
[testing-approach.md](testing-approach.md)) passed against the live service.
That is independent verification, not self-grading.

### 5. Deliberate, documented judgment calls

Two examples. `ShortenRequest.url` is typed `Object` with a comment
explaining why: a non-string JSON value becomes a contract-compliant 400
instead of a raw Jackson deserialization error. The daily-stats aggregation
is pushed into SQL with explicit UTC handling so grouping is independent of
server timezone. And the brownfield analytics addition modeled clicks as an
**append-only event table** rather than widening the counter — a genuinely
good instinct that makes per-day history recomputable from storage.

## Where it falls short of production

### 1. No real persistence story — the biggest gap

`jdbc:h2:mem` means all data dies with the JVM, despite the requirement
saying "persist URL mappings." And `spring.jpa.hibernate.ddl-auto=update`
with no Flyway/Liquibase means no migration path, ever. A production service
needs file-based or external storage plus versioned schema migrations.
(Notably, a later run's requirements stage explicitly demanded file-based H2
surviving restarts for a different product — the pipeline *can* catch this;
the shortener run didn't.)

### 2. `CodeGenerator.seedCounter()` loads the entire table at startup

```java
@PostConstruct
void seedCounter() {
    long maxId = repository.findAll().stream()   // O(table) memory
            .mapToLong(m -> m.getId() == null ? 0L : m.getId())
            .max().orElse(0L);
    counter.set(maxId);
}
```

Should be `SELECT MAX(id)`. **The factory's own review stage flagged exactly
this** and surfaced it at the merge gate — the concern is preserved in the
run's decision lineage. Governance saw it; the approval merged it anyway.
That is precisely where a human in the loop earns their keep: review
concerns are advisory, and a human reviewer should have sent this one back.

### 3. Click counting can undercount under concurrency

`resolveAndRecordClick` does an entity read-modify-write inside a
transaction — two concurrent redirects at default isolation can lose an
increment. The append-only `ClickEvent` table is accurate (one row per
click), so the daily analytics are right while the total counter can drift.
An atomic `UPDATE url_mapping SET click_count = click_count + 1` would fix
it.

### 4. Single-instance assumptions everywhere

In-memory rate limiter state, in-memory database, code counter seeded from
the local max id. Two instances behind a load balancer would double the rate
cap, split the code space, and serve disjoint data. Also
`getRemoteAddr()` ignores `X-Forwarded-For`, so behind a proxy every client
shares one rate bucket. Acceptable for the assignment's scope ("implement
the limiter by hand, no new dependencies"), but this is the "scalable"
criterion's weak spot.

### 5. Zero logging

Not one logger in the product codebase. Health endpoint aside, there is no
operational visibility — no request logs, no warning on rate-limit
rejections or collision retries. The *orchestrator* has audit-grade
observability (Langfuse traces, decision lineage, commit-per-stage); the
*product* has none. Production code needs structured logs at minimum.

### 6. Small contract edge: oversize URLs surface as 500

Validation checks scheme, host, and blankness but not length. A URL longer
than the 2,048-character column limit fails the database constraint and
surfaces as an HTTP 500 rather than a 400 with a validation message.

## Scorecard against the assignment's criteria

The assignment asks for "modular, testable, reliable, secure, scalable code
with safe change management."

| Criterion | Grade | One-liner |
|---|---|---|
| Modular | Strong | Clean layering, single-purpose classes, DTOs at the boundary |
| Testable | Strong | 60 behavioral tests plus an independent black-box acceptance suite |
| Reliable | Mixed | DB-level race handling excellent; click-count race and restart data loss remain |
| Secure | Adequate | Scheme allowlist blocks `javascript:`/`data:`; no internals leaked in errors; no headers or length caps |
| Scalable | Weak | In-memory everything; single-instance by construction |
| Safe change management | Strong | Every change on a task branch, `--no-ff` merges, review concerns preserved in lineage |

## The meta-point

The most defensible claim this assessment supports is not "the agent writes
perfect code" — it doesn't. It is that the factory's controls *detected* the
kinds of flaws listed above: the review stage flagged the `findAll()`
seeding, the policy stage enforced the dependency allowlist and secret
scanning, the acceptance suite independently verified the HTTP contract, and
every concern is preserved in an auditable decision lineage attached to the
merge gate where a human could act on it. The residual gaps are exactly the
ones that slipped through an auto-approved gate — which is an argument for
the human in the loop, not against the pipeline.

A postscript proves the point. The ambiguous-scenario run (`a02b00b7`)
later attempted to close the persistence gap (task T2: file-based H2), and
the agent's own new `RestartPersistenceIntegrationTest` caught the
counter-seeding flaw described above as a live defect — after a simulated
restart, the generator re-issued an already-allocated code. Verification
refused every attempt, the work was rolled back, and the run safe-stopped
when its replan budget ran out. The flaw this assessment predicted on paper
was demonstrated by a failing test and kept out of `main` by the controls
(see `scenarios/artifacts/README.md`, ambiguous scenario).
