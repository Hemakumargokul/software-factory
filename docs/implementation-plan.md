# Implementation Plan

Build order for the software factory described in [plan.md](plan.md). Each milestone lists the files it creates, the interfaces that matter, and a concrete "done when" check. Milestones are ordered so that every one leaves the repo in a runnable, testable state, and the factory unit tests land inside each milestone rather than at the end.

Python 3.11+ for the orchestrator; JDK 21+ for the product the factory builds (Maven is not required — the wrapper ships in the scaffold template). The SDK is async, so nodes are `async def` and the graph runs under `asyncio`.

The factory is language-agnostic: everything product-facing (scaffold, build/test/run commands, policy patterns, dependency allowlist) lives in a `ProjectProfile`, and the demo profile is `java-springboot`. The orchestration graph never mentions a language.

## M0 — Scaffold, profiles and repo bootstrap

Files: `pyproject.toml`, `src/factory/__init__.py`, `src/factory/profiles.py`, `templates/java-springboot/`, `.env.example`, `.gitignore`, `README.md` skeleton, `git init` with an initial commit.

Dependencies, pinned: `claude-agent-sdk`, `langgraph`, `langgraph-checkpoint-sqlite`, `langfuse`, `typer`, `rich`, `pytest`, `pytest-asyncio`, `httpx`.

`profiles.py` defines the `ProjectProfile` dataclass (language, scaffold template path, `build_cmd`, `test_cmd`, `package_cmd`, `run_cmd`, `health_url`, dependency files and allowlist, forbidden patterns, protected globs) plus the `java-springboot` instance. `templates/java-springboot/` contains `mvnw`, `.mvn/wrapper/` and a minimal Spring Boot 3 `pom.xml` — the wrapper jar is a binary an LLM cannot fabricate, which is why the template is seeded rather than generated. Generate the template once locally with a throwaway `spring initializr` download or `mvn wrapper:wrapper`, then commit it.

`.env.example` documents: `FACTORY_PROFILE=java-springboot`, `FACTORY_MODEL_REASONER`, `FACTORY_MODEL_IMPLEMENTER`, `FACTORY_MODEL_FALLBACK`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `LANGFUSE_TRACING_ENABLED`, and a warning that a stale `ANTHROPIC_API_KEY` overrides subscription auth.

Done when: `pip install -e .` succeeds, `pytest` runs (zero tests, zero errors), and `./mvnw -q compile` succeeds inside a copy of the template with JDK 21.

## M1 — State and lineage (`state.py`)

The one piece of nonobvious code is the stage-results reducer. It must merge dicts so parallel branches writing disjoint keys coexist, and `None` overwrites mean invalidation:

```python
def merge_stage_results(left: dict, right: dict) -> dict:
    return {**left, **right}   # right wins; writing {"design": None} invalidates

class FactoryState(TypedDict):
    goal: str
    scenario: str
    spec: dict | None
    ambiguities: list[str]
    impact: dict | None
    design: dict | None
    tasks: list[dict]            # each: {id, title, depends_on: [id], status}
    task_idx: int
    base_sha: str
    head_sha: str | None
    attempts: int
    replan_budget: int
    stage_results: Annotated[dict, merge_stage_results]
    risks: Annotated[list[dict], operator.add]
    decisions: Annotated[list[dict], operator.add]
    audit: Annotated[list[dict], operator.add]
    metric_events: Annotated[list[dict], operator.add]
```

Helper: `record_decision(stage, decision, rationale, alternatives, commit_sha=None, trace_id=None) -> dict` so every node emits lineage the same way.

Tests (`tests/factory/test_state.py`): reducer merges disjoint keys, right-hand `None` invalidates, lineage fields append.

Done when: those tests pass.

## M2 — Git operations (`git_ops.py`)

Thin wrappers over `subprocess.run(["git", ...], cwd=repo, check=True, capture_output=True)`. Every mutating call returns the resulting SHA.

```python
def init_repo(path: Path) -> str                    # git init + empty initial commit
def commit_all(path, message, trailers: dict) -> str  # trailers: {"Factory-Trace-Id": ...}
def current_sha(path) -> str
def diff(path, base_sha) -> str
def changed_files(path, base_sha) -> list[str]
def reset_hard(path, sha) -> str
def create_branch(path, name) -> None
def merge_to_main(path, branch) -> str
```

Tests (`test_git_ops.py`): real git against `tmp_path` — init produces a SHA, commit trailer round-trips through `git log --format=%(trailers)`, reset restores a deleted file.

Done when: tests pass against throwaway repos.

## M3 — SDK roles (`claude.py`)

One entry point wraps `claude_agent_sdk.query()`; the three roles are configurations of it.

```python
@dataclass
class RoleConfig:
    name: str                     # "reasoner" | "analyst" | "implementer"
    allowed_tools: list[str]
    model: str
    fallback_model: str | None
    max_turns: int
    max_budget_usd: float
    permission_mode: str | None = None

@dataclass
class RoleResult:
    text: str
    session_id: str | None
    usage: dict          # tokens, from the terminal ResultMessage
    cost_usd: float | None
    num_turns: int

async def run_role(role: RoleConfig, prompt: str, *, cwd: Path | None = None,
                   system_prompt: str | None = None,
                   can_use_tool=None, hooks=None) -> RoleResult
```

`run_role` iterates the async message stream, keeps the final result message for usage/cost, and raises `RoleError` on nonzero-cost failure so callers can trigger fallback. Also here: `extract_json(text) -> dict` for reasoner outputs (find the last fenced JSON block; raise with the raw text attached on parse failure — the caller decides whether to retry).

The reasoner and analyst prompts ask for JSON with an explicit schema in the system prompt. Keep every prompt template in `claude.py` as module constants so they're reviewable in one place.

Tests: `extract_json` against clean, fenced, and prose-wrapped outputs. `run_role` itself is only smoke-tested manually here; graph tests mock it.

Done when: a manual `python -m factory.smoke` (temporary script) gets a reasoner reply through subscription auth.

## M4 — Permissions and policy (`permissions.py`, `policy_rules.py`)

`permissions.py` builds the callback and hook, closed over the sandbox path and an audit sink:

```python
def make_can_use_tool(sandbox: Path, protected: list[str], audit_sink) -> CanUseTool
def make_pretooluse_hook(audit_sink) -> dict[HookEvent, list[HookMatcher]]
```

Deny rules, in order: any path argument resolving outside `sandbox` (use `Path.resolve()` and `is_relative_to`, so `../` tricks fail); any write/edit whose target matches a protected glob (`tests/acceptance/**`). Every allow and deny goes to the audit sink.

`policy_rules.py` is pure functions over a diff, parameterized by the profile so the rules are language-agnostic:

```python
def scan_secrets(diff: str) -> list[Violation]        # regexes: AWS keys, bearer tokens, PEM headers, password= literals
def scan_forbidden(diff: str, patterns: list[str]) -> list[Violation]   # added lines only
def scan_dependencies(diff: str, dependency_files: list[str], allowlist: set[str]) -> list[Violation]
```

The `java-springboot` profile supplies the forbidden patterns (`Runtime.getRuntime().exec`, `ProcessBuilder`, `System.exit`, reflection classloading) and the dependency scan reads added `pom.xml` lines for new `groupId:artifactId` coordinates. Allowlist starts as Spring Boot starters plus `com.h2database:h2` and JUnit. Anything else is a high-impact action, not an automatic denial.

Tests: path escape via `..`, protected glob matching, each scanner against positive and negative diffs.

Done when: tests pass; denials appear in the audit sink.

## M5 — Tracing (`tracing.py`)

A single module-level guard decides everything once:

```python
def tracing_enabled() -> bool   # keys present, LANGFUSE_TRACING_ENABLED != "false", host reachable (1s httpx GET, cached)

@contextmanager
def stage_span(name, **attrs): ...        # yields real span or _NoopSpan
@contextmanager
def generation_span(name, model, prompt): ...  # caller calls span.end_with(result: RoleResult)
def tool_span(name, input, output, denied: bool): ...
def score(name: str, value: float): ...
```

`_NoopSpan` accepts any method call and does nothing, so call sites never branch on whether tracing is on. Truncate all inputs/outputs to 2,000 chars before sending. `propagate_attributes(session_id=thread_id)` wraps graph invocation in the CLI.

Tests: with env vars unset, every helper is a no-op and nothing raises; truncation boundary.

Done when: tests pass with no Langfuse running.

## M6 — Sequential spine (`graph.py`, `stages/`, minimal `cli.py`)

The core milestone. Nodes are `async def node(state: FactoryState) -> dict` returning partial updates only. Conditional routers are pure sync functions of state — that's what makes routing unit-testable.

Build in this order:

1. `stages/bootstrap.py` — create sandbox at `/tmp/factory/<run_id>`, copy the profile's scaffold template in, `init_repo`, record `base_sha`. The acceptance suite stays in the orchestrator repo; it never enters the sandbox.
2. `stages/intake.py` — reasoner call; output `{problem, assumptions, ambiguity_score, ambiguities, scenario}`.
3. `stages/requirements.py` — reasoner; output spec with acceptance criteria.
4. `stages/design.py` — reasoner; output architecture, API contract, schema, and the initial `risks` entries.
5. `stages/decompose.py` — reasoner; tasks with `depends_on`; validate the DAG is acyclic and topologically order it here (fail fast on a bad decomposition rather than mid-run).
6. `stages/implement.py` — implementer role, `cwd=sandbox`, prompt = current task + spec + design + (on retry) truncated failure report.
7. `stages/tests_stage.py` — run the profile's `build_cmd` then `test_cmd` via `subprocess`, capture the last 100 lines. Timeout 600s on the first run (Maven downloads dependencies), 180s after. The first-task bar is a successful build; test failures gate every task after code exists.
8. `stages/acceptance.py` — the service lifecycle: `package_cmd`, start `run_cmd` in its own process group, poll `health_url` with a 60s deadline, run the black-box suite (`pytest tests/acceptance` in the orchestrator repo with the service URL in an env var), and kill the process group in a `finally`.
9. `stages/commit_stage.py`, `stages/integrate.py` — git ops plus decision records.
10. `stages/release.py`, `stages/summary.py` — checklist assembly; reasoner writes the engineering summary from `decisions`, `risks`, `metric_events`.

`gates.py` lands here too, as data plus two generic wrappers:

```python
@dataclass
class StageGate:
    entry: Callable[[FactoryState], str | None]   # None = ok, str = violation
    exit: Callable[[FactoryState], str | None]
```

The `implement` entry gate checks `all(dep integrated for dep in task.depends_on)`.

Minimal CLI: `factory run "<goal>"` streaming node names and decisions with `rich`.

Tests (`test_graph_spine.py`): with `run_role` monkeypatched to canned JSON per role/stage, the full spine runs end-to-end in-memory and produces a commit in a temp sandbox. Router tests for every conditional edge.

Done when: mocked spine test passes, and one real run against Claude produces a committed hello-world-grade change in the sandbox.

## M7 — Parallel verification, retries, rollback, re-plan

Graph surgery on M6:

- Fan out `implement -> tests_stage, policy_stage, review_stage`; join at `sync` with `builder.add_node("sync", sync_node, defer=True)`. The three branches write `stage_results["tests"|"policy"|"review"]` — disjoint keys through the merge reducer.
- `route_after_sync(state)` — pure function returning `"acceptance" | "implement" | "rollback"` based on branch results, `attempts` vs `MAX_ATTEMPTS=2`, and policy violations (violation skips retries entirely). `route_after_acceptance(state)` returns `"commit" | "implement" | "rollback"` with the same retry accounting, so an acceptance failure feeds back exactly like a unit-test failure.
- `stages/rollback.py` — `reset_hard(base_sha)`, decision record, route to `decompose` (with `replan_budget -= 1`) or `safe_stop`.
- Fallback: `run_role` retries once on `RoleError` with `fallback_model` before the failure propagates to gate logic.

Tests: routing matrix (pass, fail-with-retries, fail-exhausted, policy-violation, replan-budget-zero); reducer behavior under simulated concurrent writes; rollback restores the tree in a temp repo.

Done when: a mocked run with a deliberately failing tests branch retries twice, rolls back, re-plans once, then safe-stops.

## M8 — Human gates, revise semantics, checkpointer

- Swap `InMemorySaver` for `SqliteSaver("runs/checkpoints.db")`.
- Gate nodes contain only `interrupt(payload)` and routing. Payload carries the diff (for merge) or the artifact (spec/design) plus the question. Resume values: `{"action": "approve"}`, `{"action": "reject"}`, `{"action": "revise", "edits": "..."}`.
- Revise at the requirement gate writes the revised spec and returns `stage_results` invalidation `{"design": None, "tasks": None}`; at the design gate, `{"tasks": None}` onward. Both record the invalidation as a decision.
- `clarify` node for ambiguous intake: `interrupt({"questions": [...]})`, answers fold into the spec, loop back to intake.
- CLI: `factory run` prints the interrupt payload and exits with the thread id; `factory approve <thread> [--revise "text"] [--reject]` resumes with `Command(resume=...)`; `factory status <thread>` lists pending interrupts. Ctrl-C documented as safe-stop; `factory resume <thread>` continues.

Tests: with mocked roles, run to the requirement gate, resume with revise, assert design and tasks were invalidated and re-executed; assert gate nodes perform no side effects before `interrupt` (resume-twice test: run the node's pre-interrupt path twice and diff the sandbox).

Done when: a real interactive run pauses at each gate and resumes correctly across process restarts.

## M9 — Metrics (`metrics.py`)

Events are already accumulating in state; this milestone computes and persists.

- SQLite `runs/metrics.db`: table `events(run_id, ts, kind, stage, payload)` written at node boundaries; table `runs(run_id, started, finished, scenario, outcome)`.
- `compute(run_id) -> MetricsReport`: success rate (first-attempt exit-gate passes / attempts), retry count per stage, rollback count, MTTR (first failing exit gate to next passing one, wall clock), end-to-end latency with per-stage breakdown.
- Emit each as `tracing.score(...)` at run end; `factory metrics [run_id]` renders the table with `rich`.

Tests: `compute` against handcrafted event sequences, including MTTR with interleaved failures.

Done when: tests pass and a real run's metrics render.

## M10 — Langfuse compose

`docker-compose.langfuse.yml`: `langfuse-web`, `langfuse-worker`, `postgres`, `clickhouse`, `redis`, `minio`; only `3000:3000` published; `LANGFUSE_INIT_*` seeded with fixed dev keys matching `.env.example`; `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY` pre-filled and commented as throwaway.

Done when: `docker compose -f docker-compose.langfuse.yml up -d`, then a mocked-role run (cheap) produces a visible trace with graph structure, generation spans, tool spans and scores at `localhost:3000` with zero UI setup.

## M11 — Acceptance suite and the three scenarios

`tests/acceptance/test_shortener.py` (hand-written, black-box, lives in the orchestrator repo and never enters the sandbox): shorten/redirect/stats contract via `httpx` against a live service whose base URL arrives in the `SHORTENER_URL` env var, set by the acceptance stage; scheme allowlist (reject `javascript:`, `data:`, `ftp:`); duplicate-URL and collision behavior; 429 under burst. The agent is told the API contract from the design stage, never shown the suite. Prime the Maven cache before scenario runs by building the template once, so first-build downloads don't distort latency metrics.

Scenario scripts in `scenarios/` invoke the CLI with fixed goals:

1. `greenfield.sh` — "Build a URL shortener with shorten, redirect and stats endpoints, persisted in an embedded database."
2. `brownfield.sh` — "Add per-day click analytics and rate limiting." (Run against the repo produced by 1.)
3. `ambiguous.sh` — "Make it more reliable." (Expect clarification; answer via `factory approve --revise`.)

During the brownfield run, exercise one design-gate revision and keep it in the captured artifacts. After each run, copy the sandbox commit log, decision lineage, metrics report and trace URL into `scenarios/artifacts/<name>/`.

Done when: all three scenario artifact sets exist and the acceptance suite passes in the final sandbox.

## M12 — Deliverable documents

- `docs/architecture.md` — components, orchestration model, control flow diagram, key decisions with alternatives (source: plan.md plus real run learnings).
- `docs/testing-approach.md` — the two-suite taxonomy, factory unit tests, limitations and trade-offs (read-only parallel branches, single-worktree, model nondeterminism).
- `docs/engineering-summary.md` — generated by the summary stage from the greenfield run, then reviewed by hand.
- `README.md` finalized: prerequisites, `claude /login`, compose up, run commands, gate workflow, metrics, running without Docker.

Done when: a colleague could go from clone to a gated run using only the README.

## Order and pacing

Day 1: M0 through M6 — the mocked spine test is the day's exit criterion, with one real Claude run as a smoke check.
Day 2: M7 through M9 — governance mechanics, all testable with mocks, so Claude quota is spent only on smoke checks.
Day 3: M10 through M12 — infrastructure, the three real runs, and documents. Real runs are the day's cost center in both quota and wall time: each verification loop includes a Maven build, and acceptance adds packaging plus JVM startup, so budget roughly 3–5 minutes per task iteration. Do the runs once and capture everything.

Standing rules while building: commit at every milestone boundary; factory unit tests land inside the milestone that introduces the module; never point the implementer role at this repo — the sandbox is always `/tmp/factory/<run_id>`.
