#!/usr/bin/env bash
# Scenario 2 — brownfield: extend the product built by greenfield.sh.
#
# Usage: scenarios/brownfield.sh /tmp/factory/<greenfield-run-id>
#
# Demonstrates: seeding from an existing codebase (FACTORY_SEED_DIR), the
# read-only impact-analysis stage, and one DESIGN-GATE REVISION — the
# upstream-change re-plan trigger — before approving the rest.

source "$(dirname "$0")/_lib.sh"

SEED="${1:?usage: brownfield.sh <path to existing product, e.g. /tmp/factory/abc12345>}"
[ -d "$SEED" ] || { echo "seed directory not found: $SEED"; exit 1; }
export FACTORY_SEED_DIR="$SEED"

GOAL='In the existing URL shortener codebase, add per-day click analytics: a new endpoint GET /api/stats/{code}/daily returning HTTP 200 and a JSON array [{"date": "YYYY-MM-DD", "clicks": N}, ...] of redirect counts per calendar day (UTC), most recent first; unknown codes return HTTP 404. Do NOT change any existing endpoint, response shape, validation rule or the rate limiter — the existing behavior is contractual.'

LOG="$REPO_ROOT/scenarios/artifacts/brownfield/run.log"
mkdir -p "$(dirname "$LOG")"

# Leg 1: run to the requirement gate.
"$FACTORY" run "$GOAL" 2>&1 | tee "$LOG"
RUN_ID="$(run_id_from_log "$LOG")"

# Leg 2: approve the spec; the run analyzes impact, designs, parks at the
# design gate.
"$FACTORY" approve "$RUN_ID" 2>&1 | tee -a "$LOG"

# Leg 3: REVISE at the design gate (captured upstream-change re-plan).
"$FACTORY" approve "$RUN_ID" --revise \
  'Aggregate daily clicks with a database query over per-click rows (or a per-day counter table) rather than counting in application memory, so analytics survive restarts. Keep the response shape exactly as specified.' \
  2>&1 | tee -a "$LOG"

# Leg 4: approve the revised design and everything after it.
"$FACTORY" approve "$RUN_ID" --auto 2>&1 | tee -a "$LOG"

capture_artifacts brownfield "$RUN_ID"
