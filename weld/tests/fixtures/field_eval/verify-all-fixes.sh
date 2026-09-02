#!/usr/bin/env bash
# Regression suite for every finding reported against weld 0.23.1 and 0.24.0,
# plus the behaviours 0.25.0 introduces. Prints PASS/FAIL per check and exits
# non-zero if any regressed.
#
#   ./verify-all-fixes.sh <workspace-root>
#
# NOTE: no `set -o pipefail` on purpose. Several wd commands exit non-zero BY
# DESIGN (that is the fix being verified) and pipefail would propagate that
# status through the grep inspecting their output, producing false FAILs.
set -u

R="${1:?usage: verify-all-fixes.sh <workspace-root>}"
R="$(cd "$R" && pwd)"
FAILED=0

check() { # check <id> <description> <status>
    if [ "$3" -eq 0 ]; then printf '  PASS  %-5s %s\n' "$1" "$2"
    else printf '  FAIL  %-5s %s\n' "$1" "$2"; FAILED=1; fi
}
enable_resolver()  { sed -i 's/^cross_repo_strategies: \[\]$/cross_repo_strategies: [package_graph]/' "$R/.weld/workspaces.yaml"; }
disable_resolver() { sed -i 's/^cross_repo_strategies: \[package_graph\]$/cross_repo_strategies: []/' "$R/.weld/workspaces.yaml"; }
active_strategies() { grep -E '^\s*strategy:' "$1" | grep -v '^\s*#' | sed 's/.*strategy: *//' | sort; }

printf 'weld under test: %s\n\n' "$(wd --version)"
printf -- '--- findings reported against 0.23.1 ---\n'

cd "$R"
n=$(wd brief "OrderReplayer" 2>/dev/null | python3 -c "import json,sys;print(len(json.load(sys.stdin)['primary']))" 2>/dev/null || echo 0)
[ "${n:-0}" -gt 0 ]; check 01 "wd brief federates at a polyrepo root (primary=$n)" $?

git worktree add -q "$R/.worktrees/v02" -b verify/02 2>/dev/null
( cd "$R/.worktrees/v02" && wd query "OrderReplayer" >/dev/null 2>&1 )
[ $? -ne 0 ]; check 02 "wd query exits non-zero at a graph-less federation root" $?
git worktree remove --force "$R/.worktrees/v02" 2>/dev/null; git branch -D verify/02 >/dev/null 2>&1

cs=$(cd "$R/services/order-gateway" && wd capabilities 2>/dev/null | awk '$1=="csharp"{print $5}')
[ "$cs" = "yes" ]; check 03a "capabilities: csharp symbols=yes in a C# repo (got '$cs')" $?
py=$(cd "$R/services/notify-service" && wd capabilities 2>/dev/null | awk '$1=="csharp"{print $2}')
[ "$py" = "no" ]; check 03b "capabilities: csharp file=no in a Python-only repo (got '$py')" $?

( cd "$R/services/notify-service" && python3 -c "
import json,sys
d=json.load(open('.weld/graph.json'))
n=d['nodes']; keys=n.keys() if isinstance(n,dict) else [x['id'] for x in n]
sys.exit(0 if (any('order.schema.v1.event_pb2' in k for k in keys)
               and any('order.schema.v2.event_pb2' in k for k in keys)) else 1)" )
check 04 "python package ids keep the full dotted path (v1 and v2 distinct)" $?

cd "$R/services/order-gateway"; cp .weld/discover.yaml /tmp/wv25-good.yaml
printf 'sources:\n  - glob: "doc/*.md"\n    type: doc\n    strategy: markdown\n' > .weld/discover.yaml
wd discover --safe --output .weld/graph.json >/dev/null 2>&1
out=$(wd doctor 2>/dev/null); printf '%s' "$out" | grep -q "unclaimed-source-csharp"
check 05 "doctor warns about unwired C# source" $?

printf -- '\n--- findings reported against 0.24.0 ---\n'

# N6/N7 use the degraded config left in place above
printf '%s' "$out" | grep -q -- "--refresh"
check N6 "the unclaimed-source warning offers --refresh, not only --force" $?
wd init --refresh >/dev/null 2>&1; active_strategies .weld/discover.yaml > /tmp/wv25-refresh.txt
cp /tmp/wv25-good.yaml .weld/discover.yaml; wd init --force >/dev/null 2>&1
active_strategies .weld/discover.yaml > /tmp/wv25-force.txt
diff -q /tmp/wv25-refresh.txt /tmp/wv25-force.txt >/dev/null 2>&1
check N7 "--refresh wires the same ACTIVE strategy set as --force" $?
cp /tmp/wv25-good.yaml .weld/discover.yaml; wd discover --safe --output .weld/graph.json >/dev/null 2>&1

cd "$R"; enable_resolver; wd discover --safe --output .weld/graph.json >/dev/null 2>&1
python3 -c "
import json,sys
d=json.load(open('$R/.weld/graph.json'))
n=d['nodes']; nodes=n if isinstance(n,dict) else {x['id']:x for x in n}
e=d['edges']; edges=list(e.values()) if isinstance(e,dict) else e
ends={x.get('from') for x in edges}|{x.get('to') for x in edges}
sys.exit(0 if ends and not [t for t in ends if t not in nodes] else 1)"
check N1 "package_graph edges resolve to real repo: nodes (0 dangling)" $?

python3 -c "
import json,sys
d=json.load(open('$R/.weld/graph.json'))
e=d['edges']; edges=list(e.values()) if isinstance(e,dict) else e
bad=[x for x in edges if (x.get('props',{}).get('package') or '').lower() in ('pandas','google.protobuf')]
sys.exit(0 if not bad else 1)"
check N2 "no cross-repo edges fabricated from the vendored .venv" $?

python3 -c "
import json
p='$R/.weld/graph.json'; d=json.load(open(p)); e=d['edges']
b={'from':'repo:services-order-gateway','to':'repo:does-not-exist','type':'cross_repo:depends_on','props':{}}
(e.update({'_inj':b}) if isinstance(e,dict) else e.append(b)); json.dump(d,open(p,'w'))"
wd graph validate >/dev/null 2>&1
[ $? -ne 0 ]; check N3 "wd graph validate FAILS on a dangling cross-repo endpoint" $?
wd discover --safe --output .weld/graph.json >/dev/null 2>&1

( cd "$R/services/notify-service" && python3 -c "
import json,sys
d=json.load(open('.weld/graph.json'))
n=d['nodes']; nodes=n if isinstance(n,dict) else {x['id']:x for x in n}
first=[k for k,v in nodes.items()
       if k.startswith('package:python:') and v.get('props',{}).get('external')
       and k.split(':')[2].split('.')[0] in ('acme_notify','broker','handlers','shared_helper')]
sys.exit(0 if not first else 1)" )
check N4 "first-party python imports mint no external package nodes" $?

ws_present=$(wd workspace status 2>/dev/null | sed -n 's/.*present=\([0-9]*\).*/\1/p')
st_present=$(wd stale 2>/dev/null | sed -n 's/.*registered, \([0-9]*\) present.*/\1/p')
[ -n "$ws_present" ] && [ "$ws_present" = "$st_present" ]
check N5 "wd stale roster agrees with workspace status (present=$ws_present vs $st_present)" $?

( cd "$R/docs-site" && python3 -c "
import json,sys
d=json.load(open('.weld/graph.json'))
n=d['nodes']; nodes=n if isinstance(n,dict) else {x['id']:x for x in n}
sys.exit(0 if any((v.get('props',{}).get('file') or '')=='README.md' for v in nodes.values()) else 1)" )
check N8 "README.md becomes a doc node in a markdown repo" $?

git worktree add -q "$R/.worktrees/v09" -b verify/09 2>/dev/null
( cd "$R/.worktrees/v09" && wd find "OrderReplayer" >/dev/null 2>&1 )
[ $? -ne 0 ]; check N9 "wd find exits non-zero when the file index is absent" $?
git worktree remove --force "$R/.worktrees/v09" 2>/dev/null; git branch -D verify/09 >/dev/null 2>&1

printf -- '\n--- behaviours introduced in 0.25.0 ---\n'

python3 -c "
import json,sys
d=json.load(open('$R/.weld/graph.json'))
e=d['edges']; edges=list(e.values()) if isinstance(e,dict) else e
sys.exit(0 if edges and all(str(x.get('type','')).startswith('cross_repo:') for x in edges) else 1)"
check X1 "every root cross-repo edge carries the cross_repo: prefix" $?

out=$(wd impact "repo:libs-order-schema" --allow-stale 2>/dev/null)
printf '%s' "$out" | grep -q "Measured by:"
check X2 "wd impact reports a measured result once a resolver has run" $?

disable_resolver; wd discover --safe --output .weld/graph.json >/dev/null 2>&1
out=$(wd impact "repo:libs-order-schema" --allow-stale 2>/dev/null)
printf '%s' "$out" | grep -q "Risk: UNKNOWN"
check 06 "wd impact still reports UNKNOWN when no resolver is wired" $?

cd "$R/services/order-gateway"
first=$(wd brief "OrderReplayer" 2>/dev/null | python3 -c "
import json,sys
p=json.load(sys.stdin)['primary']; print(p[0].get('relevance','') if p else '')" 2>/dev/null)
[ "$first" = "exact match" ]; check 08 "wd brief ranks the exact identifier first (got '$first')" $?

cd "$R/services/order-gateway"
git rm -q --cached .weld/discover.yaml >/dev/null 2>&1
git -c commit.gpgsign=false commit -q -m untrack >/dev/null 2>&1
git worktree add -q "$R/.wt-v09" -b verify/09b 2>/dev/null
out=$(cd "$R/.wt-v09" && wd query "OrderReplayer" 2>&1)
printf '%s' "$out" | grep -q "discover.yaml"
check 09 "worktree no-graph message names the missing discover.yaml" $?
git worktree remove --force "$R/.wt-v09" 2>/dev/null; git branch -D verify/09b >/dev/null 2>&1
git add -f .weld/discover.yaml >/dev/null 2>&1
git -c commit.gpgsign=false commit -q -m retrack >/dev/null 2>&1

cd "$R/services/notify-service"
out=$(wd callers "symbol:py:src.acme_notify.helper:work" 2>/dev/null)
printf '%s' "$out" | grep -q "relative_caller"
check X3 "callers resolve through an explicit relative import" $?
out=$(wd callers "symbol:py:scripts.shared_helper:shared_work" 2>/dev/null)
printf '%s' "$out" | grep -q "run_report"
check X4 "callers resolve through a sibling bare-name import" $?

printf '\n'
if [ "$FAILED" -eq 0 ]; then printf 'All checks passed.\n'; else printf 'One or more checks FAILED.\n'; fi
exit "$FAILED"
