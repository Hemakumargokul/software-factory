#!/usr/bin/env bash
# Scenario 3 — ambiguous request: "Make it more reliable."
#
# Usage: scenarios/ambiguous.sh /tmp/factory/<greenfield-run-id>
#
# Demonstrates: ambiguity detection in intake, the clarification interrupt,
# and human answers folding back into the run.

source "$(dirname "$0")/_lib.sh"

SEED="${1:?usage: ambiguous.sh <path to existing product, e.g. /tmp/factory/abc12345>}"
[ -d "$SEED" ] || { echo "seed directory not found: $SEED"; exit 1; }
export FACTORY_SEED_DIR="$SEED"

GOAL='Make it more reliable.'

LOG="$REPO_ROOT/scenarios/artifacts/ambiguous/run.log"
mkdir -p "$(dirname "$LOG")"

# Leg 1: intake should score this ambiguous and park at the clarify gate.
"$FACTORY" run "$GOAL" 2>&1 | tee "$LOG"
RUN_ID="$(run_id_from_log "$LOG")"

# Leg 2: answer the clarification questions.
"$FACTORY" approve "$RUN_ID" --revise \
  '"It" is the URL shortener service in this repository. Reliability means: (1) malformed JSON bodies and unexpected errors return structured JSON errors with correct status codes instead of default error pages; (2) redirects and stats keep working after a restart because state is in the embedded H2 database; (3) the actuator health endpoint reports the database state. Do NOT change any existing endpoint path, response shape, validation rule or the rate limiter. No new dependencies beyond the pre-approved starters.' \
  2>&1 | tee -a "$LOG"

# Leg 3: approve the spec, design and merges.
"$FACTORY" approve "$RUN_ID" --auto 2>&1 | tee -a "$LOG"

capture_artifacts ambiguous "$RUN_ID"
