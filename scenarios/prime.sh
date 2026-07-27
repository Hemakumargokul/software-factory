#!/usr/bin/env bash
# Prime the Maven cache: build the scaffold template once so first-build
# dependency downloads don't distort the scenario runs' latency metrics.

source "$(dirname "$0")/_lib.sh"

WORK="$(mktemp -d /tmp/factory-prime-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

cp -R "$REPO_ROOT/templates/java-springboot/." "$WORK/"
echo "priming Maven cache (first run downloads dependencies; be patient)..."
# `test`, not `package`: the bare template has no main class to repackage.
(cd "$WORK" && ./mvnw -q -B test)
echo "Maven cache primed."
