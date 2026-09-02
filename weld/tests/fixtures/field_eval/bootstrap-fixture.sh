#!/usr/bin/env bash
# Initialises Weld across the fixture workspace: wd init + wd discover per child,
# then a federated discover at the root.
set -euo pipefail
R="${1:?usage: bootstrap-fixture.sh <workspace-root>}"
R="$(cd "$R" && pwd)"

for c in libs/order-schema libs/billing-schema services/order-gateway services/notify-service docs-site; do
    ( cd "$R/$c" && wd init >/dev/null 2>&1 && wd discover --safe --output .weld/graph.json )
done
( cd "$R" && wd discover --safe --output .weld/graph.json )
