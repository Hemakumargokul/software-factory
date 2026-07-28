#!/usr/bin/env bash
# Shared plumbing for the scenario scripts: environment, run-id parsing and
# artifact capture. Source this; do not run it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FACTORY="$REPO_ROOT/.venv/bin/factory"

# Langfuse dev keys (docker-compose.langfuse.yml); tracing degrades to
# no-op if the stack is down, so exporting these is always safe.
export LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY:-pk-lf-factory-dev}"
export LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY:-sk-lf-factory-dev}"
export LANGFUSE_HOST="${LANGFUSE_HOST:-http://localhost:3000}"

# Scenario runs use the stronger implementer: the acceptance contract is
# strict and haiku demonstrably burns its attempt budget on it. Override by
# exporting FACTORY_MODEL_IMPLEMENTER before running.
export FACTORY_MODEL_IMPLEMENTER="${FACTORY_MODEL_IMPLEMENTER:-sonnet}"
# Sonnet costs more per task, so raise the default run cap accordingly.
export FACTORY_RUN_BUDGET_USD="${FACTORY_RUN_BUDGET_USD:-10}"

# The product build needs JDK 21+; fall back to a known local install.
if ! command -v java >/dev/null 2>&1 && [ -z "${JAVA_HOME:-}" ]; then
    for candidate in /Library/Java/JavaVirtualMachines/zulu-21.jdk/Contents/Home \
                     /opt/homebrew/opt/openjdk@21; do
        [ -d "$candidate" ] && export JAVA_HOME="$candidate" && break
    done
fi
[ -n "${JAVA_HOME:-}" ] && export PATH="$JAVA_HOME/bin:$PATH"

run_id_from_log() {
    grep -o 'run_id=[a-f0-9]\{8\}' "$1" | head -1 | cut -d= -f2
}

capture_artifacts() {
    local name="$1" run_id="$2"
    local dir="$REPO_ROOT/scenarios/artifacts/$name"
    mkdir -p "$dir"

    echo "--- capturing artifacts for $name (run $run_id) ---"
    git -C "/tmp/factory/$run_id" log --graph --oneline --decorate \
        > "$dir/commit-log.txt" || true
    cp "$REPO_ROOT/runs/$run_id/summary.md" "$dir/summary.md" 2>/dev/null || true
    "$FACTORY" metrics "$run_id" > "$dir/metrics.txt" || true
    "$FACTORY" status "$run_id" > "$dir/status.txt" || true
    cat > "$dir/trace.txt" <<EOF
Langfuse trace for this run:
  $LANGFUSE_HOST -> project software-factory -> sessions -> $run_id
  (login: factory@example.com / factory-dev-password)
EOF

    # Preserve the product the agent built: a browsable source tree and a
    # full-history git bundle (`git clone product.bundle` reproduces the
    # sandbox repo with every task branch, merge and run-id trailer).
    rm -rf "$dir/product"
    rsync -a --exclude .git --exclude target --exclude '*.log' \
        "/tmp/factory/$run_id/" "$dir/product/" || true
    git -C "/tmp/factory/$run_id" bundle create "$dir/product.bundle" --all \
        2>/dev/null || true

    echo "artifacts in scenarios/artifacts/$name/"
}
