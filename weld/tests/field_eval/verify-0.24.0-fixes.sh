#!/usr/bin/env bash
# Verification suite for the nine findings reported against weld 0.24.0.
# Prints PASS/FAIL per finding; exits non-zero if any did not land.
#
#   ./verify-0.24.0-fixes.sh <workspace-root>
#
# The sibling verify-previous-fixes.sh is the evaluator's own script, copied
# byte for byte. This one is ours, written in their format from the probes in
# their run-all-repros.sh -- which writes transcripts for a human to read,
# where a gate needs an assertion that fails. Same workspace, same commands,
# same numbering (N1..N9), one check per finding.
#
# Every check asserts what must be TRUE OF THE RESULT, not that a result was
# produced: N1 asserts no endpoint dangles, not that edges exist, because the
# defect shipped edges. Requires: wd on PATH, git, python3.
set -u
# NOTE: no pipefail, matching verify-previous-fixes.sh -- several wd commands
# exit non-zero by design here (N3 and N9 assert exactly that), and pipefail
# would propagate that status through the python that inspects their output.

R="${1:?usage: verify-0.24.0-fixes.sh <workspace-root>}"
R="$(cd "$R" && pwd)"
GW="$R/services/order-gateway"
FAILED=0

check() { # check <id> <description> <condition-exit-status>
    if [ "$3" -eq 0 ]; then printf '  PASS  %-4s %s\n' "$1" "$2"
    else printf '  FAIL  %-4s %s\n' "$1" "$2"; FAILED=1; fi
}

# Shared prelude for every python probe below: graph loading that accepts
# both on-wire shapes (nodes/edges as dict or as list -- both are legal in
# .weld/graph.json), endpoint spelling, and a fail() that prints why.
PY='
import json, os, re, sys
SEP = chr(31)
CHILDREN = (
    ("libs-order-schema", "libs/order-schema"),
    ("services-order-gateway", "services/order-gateway"),
    ("services-notify-service", "services/notify-service"),
    ("docs-site", "docs-site"),
)
def graph(rel="."):
    with open(os.path.join(rel, ".weld", "graph.json"), encoding="utf-8") as fh:
        return json.load(fh)
def nodes(payload):
    raw = payload.get("nodes")
    if isinstance(raw, dict):
        return {str(k): (v if isinstance(v, dict) else {}) for k, v in raw.items()}
    return {str(x.get("id")): x for x in (raw or []) if isinstance(x, dict)}
def edges(payload):
    raw = payload.get("edges")
    if isinstance(raw, dict):
        raw = list(raw.values())
    return [x for x in (raw or []) if isinstance(x, dict)]
def child_of(endpoint):
    # Both legal endpoint spellings: <child>\x1f<child-local-id> for a node
    # that lives in a child graph, repo:<child> for the root-minted repo node.
    text = str(endpoint)
    if SEP in text:
        return text.split(SEP, 1)[0]
    if text.startswith("repo:"):
        return text[len("repo:"):]
    return None
def fail(message):
    print("        " + message, file=sys.stderr)
    sys.exit(1)
'

resolver() { # resolver <package_graph|""> [respect_gitignore]
    local strategies="$1" gitignore="${2:-false}"
    python3 - "$R" "$strategies" "$gitignore" <<'PYCONF'
import re, sys
root, strategies, gitignore = sys.argv[1:4]
path = root + "/.weld/workspaces.yaml"
with open(path, encoding="utf-8") as fh:
    text = fh.read()
text = re.sub(r"^cross_repo_strategies: .*$",
              "cross_repo_strategies: [%s]" % strategies if strategies
              else "cross_repo_strategies: []", text, flags=re.M)
text = re.sub(r"^(\s*)respect_gitignore: .*$",
              r"\1respect_gitignore: " + gitignore, text, flags=re.M)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text)
PYCONF
    ( cd "$R" && wd discover --safe --output .weld/graph.json >/dev/null 2>&1 ) \
        || printf '        wd discover failed at the workspace root\n' >&2
}

printf 'Verification suite: nine findings reported against weld 0.24.0\n'
printf 'weld under test: %s\n\n' "$(wd --version)"

# ---------------------------------------------------------------- N1
# The resolver's edges must point at nodes a reader can look up, and the
# consequence the evaluator reported -- impact blaming the configuration for
# a resolver that IS wired -- must be gone with both consumers named.
resolver package_graph
( cd "$R" && python3 -c "$PY"'
root = graph()
root_ids = set(nodes(root))
child_ids = {name: set(nodes(graph(rel))) for name, rel in CHILDREN}
ends = set()
for edge in edges(root):
    ends.add(str(edge.get("from")))
    ends.add(str(edge.get("to")))
if not ends:
    fail("package_graph wrote no cross-repo edges at all")
dangling = []
for end in sorted(ends):
    if end in root_ids:
        continue
    child, sep, local = end.partition(SEP)
    if sep and local in child_ids.get(child, set()):
        continue
    dangling.append(end)
if dangling:
    fail("%d/%d dangling endpoints, e.g. %r" % (len(dangling), len(ends), dangling[0]))
' ) && ( cd "$R" && wd impact "repo:libs-order-schema" --json --allow-stale 2>/dev/null ) \
    | python3 -c "$PY"'
payload = json.load(sys.stdin)
if payload.get("risk_level") == "UNKNOWN":
    fail("impact still reports Risk: UNKNOWN with package_graph wired")
if "cannot_answer" in payload:
    fail("impact cannot answer: %r" % (payload.get("cannot_answer"),))
if payload.get("measured_by") != ["package_graph"]:
    fail("impact does not credit the resolver: measured_by=%r" % (payload.get("measured_by"),))
direct = {str(n.get("id")) for n in payload.get("direct_dependents") or []}
for consumer in ("repo:services-order-gateway", "repo:services-notify-service"):
    if consumer not in direct:
        fail("%s missing from direct dependents %r" % (consumer, sorted(direct)))
'
check N1 "resolver edges all resolve, and impact measures both consumers" $?

# ---------------------------------------------------------------- N2
# Exactly the two real joins, under both respect_gitignore settings: the
# report's sharpest line was that the flag made no difference, because the
# resolver credited a repo with every package in its vendored .venv.
N2=0
for RG in false true; do
    resolver package_graph "$RG"
    ( cd "$R" && python3 -c "$PY"'
where = "respect_gitignore=" + sys.argv[1]
expected = {
    ("services-order-gateway", "libs-order-schema", "Acme.Platform.Order.Schema"),
    ("services-notify-service", "libs-order-schema", "order-schema"),
}
joins = set()
for edge in edges(graph()):
    props = edge.get("props") or {}
    joins.add((child_of(edge.get("from")), child_of(edge.get("to")),
               str(props.get("package"))))
vendored = {package for _f, _t, package in joins} & {"pandas", "Google.Protobuf"}
if vendored:
    fail("%s: vendored packages produced joins: %r" % (where, sorted(vendored)))
if joins != expected:
    fail("%s: joins are %r, expected %r" % (where, sorted(joins), sorted(expected)))
' "$RG" ) || N2=1
done
resolver package_graph false
check N2 "exactly the two real cross-repo joins, gitignored or not" "$N2"

# ---------------------------------------------------------------- N3
# validate must reject a dangling federated endpoint. Hand-injected rather
# than the resolver's own output, so this keeps meaning the same thing now
# that N1 has landed: the claim is that validate CAN see one.
N3=0
( cd "$R" && wd graph validate >/dev/null 2>&1 ) || N3=1
( cd "$R" && python3 -c "$PY"'
payload = graph()
edge = {
    "from": "repo:docs-site",
    "to": "services-notify-service" + SEP + "symbol:py:does.not:exist",
    "type": "cross_repo:depends_on",
    "props": {"package": "fabricated-for-this-probe"},
}
existing = payload.get("edges")
if isinstance(existing, dict):
    existing["fabricated-for-this-probe"] = edge
    payload["edges"] = existing
else:
    payload["edges"] = list(existing or []) + [edge]
with open(os.path.join(".weld", "graph.json"), "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
' ) || N3=1
OUT="$( cd "$R" && wd graph validate 2>/dev/null )"; RC=$?
[ "$RC" -ne 0 ] || N3=1
printf '%s' "$OUT" | python3 -c "$PY"'
payload = json.load(sys.stdin)
if payload.get("valid") is not False:
    fail("validate reports valid=%r for a graph with a dangling federated edge"
         % (payload.get("valid"),))
' || N3=1
resolver "" false
check N3 "graph validate rejects a dangling federated edge, passes a sound graph" "$N3"

# ---------------------------------------------------------------- N4
# A module this repository defines must not also be minted as an external
# package. acme_notify.* is first-party here; broker is a plain local import.
( cd "$R/services/notify-service" && python3 -c "$PY"'
minted = sorted(
    node_id for node_id in nodes(graph())
    if node_id.startswith(("package:python:acme_notify", "package:python:broker"))
)
if minted:
    fail("first-party modules minted external package nodes: %r" % (minted,))
' )
check N4 "first-party python imports mint no external package node" $?

# ---------------------------------------------------------------- N5
# The roster must count the states the freshness oracle actually emits, and
# must not disagree with workspace status. Checked twice: all four fresh, and
# with one child edited -- the second is where the shipped roster reported
# the STALE count as the present count.
roster_agrees() { # roster_agrees <label>
    local text json status
    text="$( cd "$R" && WELD_AUTO_REFRESH=0 wd stale --no-refresh 2>/dev/null )"
    json="$( cd "$R" && WELD_AUTO_REFRESH=0 wd stale --no-refresh --json 2>/dev/null )"
    status="$( cd "$R" && wd workspace status 2>/dev/null )"
    LABEL="$1" TEXT="$text" JSON="$json" STATUS="$status" python3 -c "$PY"'
label = os.environ["LABEL"]
text, status = os.environ["TEXT"], os.environ["STATUS"]
payload = json.loads(os.environ["JSON"] or "{}")
match = re.search(r"children:\s*(\d+) registered,\s*(\d+) present,\s*(\d+) stale", text)
if match is None:
    fail("%s: no child roster line in wd stale:\n%s" % (label, text))
states = [str(c.get("state")) for c in payload.get("children") or []]
if not states:
    fail("%s: wd stale --json reported no children" % label)
expected = (
    len(states),
    sum(1 for s in states if s in ("fresh", "stale", "present")),
    sum(1 for s in states if s == "stale"),
)
actual = tuple(int(match.group(i)) for i in (1, 2, 3))
if actual != expected:
    fail("%s: roster (registered, present, stale)=%r but --json says %r (states %r)"
         % (label, actual, expected, sorted(states)))
counts = re.search(r"present=(\d+)", status)
if counts is None:
    fail("%s: no present count in wd workspace status:\n%s" % (label, status))
if int(counts.group(1)) != actual[1]:
    fail("%s: wd stale says %d present, wd workspace status says %s"
         % (label, actual[1], counts.group(1)))
'
}
N5=0
roster_agrees "all four children fresh" || N5=1
printf '// touch\n' >> "$GW/src/OrderReplayer/ReplayUtilities.cs"
roster_agrees "one child edited" || N5=1
git -C "$GW" checkout -- src/OrderReplayer/ReplayUtilities.cs 2>/dev/null
check N5 "stale roster agrees with its own --json and with workspace status" "$N5"

# ---------------------------------------------------------------- N6 / N7
# Both need the team's narrowed, hand-maintained config in place -- that is
# what raises the unclaimed-source warning whose remedy N6 reads and whose
# preservation N7 measures.
GW_BACKUP="$(mktemp)"
trap 'rm -f "$GW_BACKUP"' EXIT
cp "$GW/.weld/discover.yaml" "$GW_BACKUP"
hand_edited_config() {
    cat > "$GW/.weld/discover.yaml" <<'YAML'
# Hand-maintained config. Do not clobber.
sources:
  # Custom: deliberately narrowed by the team.
  - glob: "doc/*.md"
    type: doc
    strategy: markdown
    id_prefix: doc:doc

  # Custom entry nothing auto-detects:
  - files: ["OrderGateway.sln.DotSettings"]
    type: config
    strategy: config_file
YAML
    ( cd "$GW" && wd discover --safe --output .weld/graph.json >/dev/null 2>&1 )
}

hand_edited_config
( cd "$GW" && wd doctor 2>&1 ) | python3 -c "$PY"'
warning = next((l for l in sys.stdin.read().splitlines() if "unclaimed-source" in l), "")
if not warning:
    fail("doctor printed no unclaimed-source warning")
if "--refresh" not in warning:
    fail("the unclaimed-source remedy never names --refresh: %s" % warning)
if "--force" in warning and warning.index("--refresh") > warning.index("--force"):
    fail("the destructive remedy is offered first: %s" % warning)
' && ( cd "$GW" && wd prime 2>&1 ) | python3 -c "$PY"'
_before, marker, steps = sys.stdin.read().partition("Next steps:")
if not marker:
    fail("wd prime printed no Next steps block")
if "--refresh" not in steps:
    fail("prime Next steps never offers --refresh:%s" % steps)
if "--force" in steps and steps.index("--refresh") > steps.index("--force"):
    fail("prime lists --force before --refresh:%s" % steps)
'
check N6 "the unclaimed-source remedy offers --refresh before --force" $?

strategies_of() { # strategies_of <config path> -- one strategy name per line
    sed -n 's/^[[:space:]]*-\{0,1\}[[:space:]]*strategy:[[:space:]]*\([^[:space:]]*\).*/\1/p' \
        "$1" | sort -u
}
hand_edited_config
( cd "$GW" && wd init --refresh >/dev/null 2>&1 )
REFRESHED="$(strategies_of "$GW/.weld/discover.yaml")"
hand_edited_config
( cd "$GW" && wd init --force >/dev/null 2>&1 )
FORCED="$(strategies_of "$GW/.weld/discover.yaml")"
MISSING="$(comm -13 <(printf '%s\n' "$REFRESHED") <(printf '%s\n' "$FORCED"))"
N7=0
[ -n "$FORCED" ] || { printf '        --force wired no strategies at all\n' >&2; N7=1; }
[ -z "$MISSING" ] || {
    printf '        --refresh silences the warning at less coverage than --force; missing: %s\n' \
        "$(printf '%s' "$MISSING" | tr '\n' ' ')" >&2
    N7=1
}
cp "$GW_BACKUP" "$GW/.weld/discover.yaml"
( cd "$GW" && wd discover --safe --output .weld/graph.json >/dev/null 2>&1 )
check N7 "wd init --refresh wires everything --force wires" "$N7"

# ---------------------------------------------------------------- N8
# The docs repo's index file is a node, labelled by the title it declares,
# and the search that names that title finds it first.
( cd "$R/docs-site" && python3 -c "$PY"'
payload = graph()
docs = {i: n for i, n in nodes(payload).items() if n.get("type") == "doc"}
if "doc:md/README" not in docs:
    fail("no node for README.md; doc nodes are %r" % (sorted(docs),))
if "README.md" not in ((payload.get("meta") or {}).get("discovered_from") or []):
    fail("README.md is not in meta.discovered_from")
label = (docs["doc:md/README"] or {}).get("label")
if label != "Platform Documentation":
    fail("the README node is labelled %r, not the title it declares" % (label,))
' ) && ( cd "$R/docs-site" && wd query "Platform Documentation" --json 2>/dev/null ) \
    | python3 -c "$PY"'
matches = json.load(sys.stdin).get("matches") or []
if not matches:
    fail("query for the README title found nothing")
if str(matches[0].get("id")) != "doc:md/README":
    fail("query ranks %r first, not the README node" % (matches[0].get("id"),))
'
check N8 "the docs repo README is a node, and its title ranks it first" $?

# ---------------------------------------------------------------- N9
# A read from an index that was never written is a cannot-answer, not an
# empty answer: non-zero, the file_index_missing code, and a remedy.
N9=0
git -C "$R" worktree add -q "$R/.worktrees/n9" -b verify/n9 >/dev/null 2>&1 || N9=1
OUT="$( cd "$R/.worktrees/n9" && wd find "OrderReplayer" 2>&1 )"; RC=$?
[ "$RC" -ne 0 ] || { printf '        wd find exited 0 from a missing file index\n' >&2; N9=1; }
printf '%s' "$OUT" | grep -q 'error\[file_index_missing\]' || {
    printf '        no file_index_missing code in: %s\n' "$OUT" >&2; N9=1
}
printf '%s' "$OUT" | grep -q 'hint:' || {
    printf '        the refusal states no remediation: %s\n' "$OUT" >&2; N9=1
}
git -C "$R" worktree remove --force "$R/.worktrees/n9" >/dev/null 2>&1
git -C "$R" branch -D verify/n9 >/dev/null 2>&1
check N9 "wd find refuses an absent file index instead of answering empty" "$N9"

printf '\n'
if [ "$FAILED" -eq 0 ]; then printf 'All nine fixes verified.\n'
else printf 'One or more fixes did not land.\n'; fi
exit "$FAILED"
