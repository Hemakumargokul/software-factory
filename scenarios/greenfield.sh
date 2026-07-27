#!/usr/bin/env bash
# Scenario 1 — greenfield: build the URL shortener from the bare scaffold.
#
# The goal pins the HTTP contract VERBATIM: the hand-written black-box
# acceptance suite (tests/acceptance/test_shortener.py) asserts exactly this
# contract, and the agent is told the contract but never shown the suite.
#
# Gates are auto-approved (--auto); the brownfield scenario exercises the
# interactive revise path.

source "$(dirname "$0")/_lib.sh"

GOAL='Build a URL shortener web service with EXACTLY this HTTP contract:
- POST /api/shorten with JSON body {"url": "<absolute http or https URL>"} returns HTTP 201 and JSON {"code": "<short code>", "url": "<original url>"}. Shortening the same URL again returns the SAME code (HTTP 200 or 201). Any other scheme (javascript:, data:, ftp:), a malformed URL, or a missing url field returns HTTP 400 with a JSON error body.
- GET /{code} returns an HTTP redirect (301 or 302) with the Location header set to the original URL. Unknown codes return HTTP 404.
- GET /api/stats/{code} returns HTTP 200 and JSON {"code": ..., "url": ..., "clicks": ...} where clicks is the number of redirects served for that code. Unknown codes return HTTP 404.
- Rate limiting: POST /api/shorten is capped at 30 requests per second per client IP; requests beyond the cap return HTTP 429. Implement the limiter by hand (no new dependencies).
Persist URL mappings and click counts in the embedded H2 database. Keep the actuator health endpoint working at /actuator/health.'

LOG="$REPO_ROOT/scenarios/artifacts/greenfield/run.log"
mkdir -p "$(dirname "$LOG")"

"$FACTORY" run "$GOAL" --auto 2>&1 | tee "$LOG"

RUN_ID="$(run_id_from_log "$LOG")"
capture_artifacts greenfield "$RUN_ID"
echo "greenfield sandbox: /tmp/factory/$RUN_ID (seed for brownfield.sh)"
