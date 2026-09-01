#!/usr/bin/env bash
# Regression suite for the nine findings reported against 0.23.1.
# Prints PASS/FAIL per finding; exits non-zero if any regressed.
#
#   ./verify-previous-fixes.sh <workspace-root>
set -u
# NOTE: no pipefail here on purpose -- several wd commands exit non-zero by
# design (that is the fix being verified), and pipefail would propagate that
# status through the grep that inspects their output.

R="${1:?usage: verify-previous-fixes.sh <workspace-root>}"
R="$(cd "$R" && pwd)"
FAILED=0

check() { # check <id> <description> <condition-exit-status>
    if [ "$3" -eq 0 ]; then printf '  PASS  %-4s %s\n' "$1" "$2"
    else printf '  FAIL  %-4s %s\n' "$1" "$2"; FAILED=1; fi
}

printf 'Regression suite: nine findings reported against weld 0.23.1\n'
printf 'weld under test: %s\n\n' "$(wd --version)"

# 01 - brief federates at a polyrepo root
cd "$R"
n=$(wd brief "OrderReplayer" 2>/dev/null | python3 -c "import json,sys;print(len(json.load(sys.stdin)['primary']))" 2>/dev/null || echo 0)
[ "${n:-0}" -gt 0 ]; check 01 "wd brief federates at a polyrepo root (primary=$n)" $?

# 02 - graph-backed reads exit non-zero at a graph-less federation root
git worktree add -q "$R/.worktrees/v02" -b verify/02 2>/dev/null
( cd "$R/.worktrees/v02" && wd query "OrderReplayer" >/dev/null 2>&1 )
[ $? -ne 0 ]; check 02 "wd query exits non-zero at a graph-less federation root" $?
git worktree remove --force "$R/.worktrees/v02" 2>/dev/null; git branch -D verify/02 >/dev/null 2>&1

# 03 - capabilities attributes tree-sitter languages, and only present ones
cs=$(cd "$R/services/order-gateway" && wd capabilities 2>/dev/null | awk '$1=="csharp"{print $5}')
[ "$cs" = "yes" ]; check 03a "capabilities reports csharp symbols=yes in a C# repo (got '$cs')" $?
py=$(cd "$R/services/notify-service" && wd capabilities 2>/dev/null | awk '$1=="csharp"{print $2}')
[ "$py" = "no" ]; check 03b "capabilities reports csharp file=no in a Python-only repo (got '$py')" $?

# 04 - python import paths keep their full dotted path
cd "$R/services/notify-service"
python3 -c "
import json,sys
d=json.load(open('.weld/graph.json'))
n=d['nodes']; keys=n.keys() if isinstance(n,dict) else [x['id'] for x in n]
v1=any('order.schema.v1.event_pb2'in k for k in keys)
v2=any('order.schema.v2.event_pb2'in k for k in keys)
sys.exit(0 if (v1 and v2) else 1)
"; check 04 "python package ids keep the full dotted path (v1 and v2 distinct)" $?

# 05 - unwired language is reported
cd "$R/services/order-gateway"
cp .weld/discover.yaml /tmp/wv-good.yaml
printf 'sources:\n  - glob: "doc/*.md"\n    type: doc\n    strategy: markdown\n' > .weld/discover.yaml
wd discover --safe --output .weld/graph.json >/dev/null 2>&1
wd doctor 2>/dev/null | grep -q "unclaimed-source-csharp"; check 05 "doctor warns about unwired C# source" $?
cp /tmp/wv-good.yaml .weld/discover.yaml
wd discover --safe --output .weld/graph.json >/dev/null 2>&1

# 06 - impact refuses to fabricate a verdict
cd "$R"
out=$(wd impact "repo:libs-order-schema" --allow-stale 2>/dev/null)
printf '%s' "$out" | grep -q "Risk: UNKNOWN"
check 06 "wd impact reports UNKNOWN rather than a fabricated LOW" $?

# 07 - docs repo is discovered
cd "$R/docs-site"
c=$(python3 -c "import json;print(len(json.load(open('.weld/graph.json'))['nodes']))" 2>/dev/null || echo 0)
[ "${c:-0}" -gt 0 ]; check 07 "markdown-only repo produces a non-empty graph ($c nodes)" $?

# 08 - brief ranks the exact identifier first
cd "$R/services/order-gateway"
first=$(wd brief "OrderReplayer" 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin); p=d['primary']
print(p[0].get('relevance','') if p else '')" 2>/dev/null)
[ "$first" = "exact match" ]; check 08 "wd brief ranks the exact identifier first (got '$first')" $?

# 09 - worktree seeding names its missing prerequisite
cd "$R/services/order-gateway"
git rm -q --cached .weld/discover.yaml >/dev/null 2>&1
git -c commit.gpgsign=false commit -q -m "untrack" >/dev/null 2>&1
git worktree add -q "$R/.wt-v09" -b verify/09 2>/dev/null
out=$(cd "$R/.wt-v09" && wd query "OrderReplayer" 2>&1)
printf '%s' "$out" | grep -q "discover.yaml"
check 09 "worktree no-graph message names the missing discover.yaml" $?
git worktree remove --force "$R/.wt-v09" 2>/dev/null; git branch -D verify/09 >/dev/null 2>&1
git add -f .weld/discover.yaml >/dev/null 2>&1
git -c commit.gpgsign=false commit -q -m "retrack" >/dev/null 2>&1

printf '\n'
if [ "$FAILED" -eq 0 ]; then printf 'All nine fixes verified.\n'; else printf 'One or more fixes regressed.\n'; fi
exit "$FAILED"
