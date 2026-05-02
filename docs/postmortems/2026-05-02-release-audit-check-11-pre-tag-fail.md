# Post-mortem: ADR 0015 check [11] FAILs every release between bump and tag

- **Date:** 2026-05-02
- **Release affected:** v0.14.0 (audit run before publish)
- **Severity:** Low impact, high noise. The release shipped successfully; the audit produced a false-FAIL that the operator had to manually rationalise.
- **Owner:** release-manager surface
- **Related:** [ADR 0015](../adrs/0015-release-manager-agent.md) check 11; [tools/check_main_release_consistency.py](../../tools/check_main_release_consistency.py)
- **Tracking:** internal release follow-up

## What happened

During the v0.14.0 pre-release audit on 2026-05-02, eleven of twelve checks
returned PASS. Check [11] "Public-main consistency" returned FAIL with:

```
[main-consistency] FAIL: main is ahead of latest tag (v0.13.2 -> 0.14.0) with no documented lag.
  VERSION              = 0.14.0
  weld/pyproject.toml  = 0.14.0
  latest tag           = v0.13.2
```

The audit aggregate verdict was therefore **NO-GO**, even though the release
artifact was sound: the version triple agreed, the publish dry-run passed,
the full Bazel gate passed (299/299), the overlay smoke passed, the curated
release notes passed, the release-claim verifier passed, the public-surface
registries passed, and there were no open `release-blocker` issues.

The operator overrode the NO-GO and proceeded with the publish, which
completed without incident. The release shipped.

## Why the check FAILed

[`check_main_release_consistency.py`](../../tools/check_main_release_consistency.py)
exists to catch the v0.9.0 / v0.10.1 drift shape: `main`'s `VERSION` silently
gets ahead of (or behind) the most recent `publish/vX.Y.Z` tag, and a release
ships from a commit that disagrees with what was supposed to ship. ADR 0015
check [11] is the audit-time enforcement.

The check classifies the working tree into three shapes:

1. **Match** — `VERSION` == latest tag. PASS.
2. **Ahead** — `VERSION` > latest tag. PASS only if `README.md` carries a
   `<!-- release-lag: <reason> -->` marker, otherwise FAIL.
3. **Behind** — `VERSION` < latest tag. FAIL (the v0.9.0 incident shape).

A legitimate release walks through shape (2) for the duration of the window
between the `release: bump to vX.Y.Z` commit landing on `main` and the
`publish/vX.Y.Z` tag being pushed. **The check has no signal to distinguish
"silent drift" from "active release in progress."** Both look identical to
this script: VERSION is ahead of the latest tag, no lag marker.

## Why this matters

The check is supposed to be load-bearing. When operators get used to seeing a
FAIL on every release audit, they learn to treat NO-GO as "look at which
checks failed and decide if any are real." That is the exact opposite of what
ADR 0015 is for. The aggregate verdict loses its meaning the moment one check
in twelve is structurally noisy.

It also creates pressure to add a `<!-- release-lag: pending vX.Y.Z publish -->`
marker to `README.md` just to clear the FAIL — bookkeeping noise that has to
be added before the audit and removed after the tag, on every release. The
ADR comment markers in `README.md` are meant to document genuine intentional
lag (e.g. waiting for a launch window), not in-flight release state.

The two failure modes already on the table:

1. **False trust:** operator stops reading individual check verdicts and
   rubber-stamps NO-GO results because "[11] always fails."
2. **Bookkeeping churn:** operator adds a release-lag marker, audit returns
   GO, operator must remember to remove the marker after tagging. One extra
   step per release; one new way to ship a misleading marker.

Both modes are real. This post-mortem was triggered by mode (2) almost
happening on this release.

## Root cause

`check_main_release_consistency.py` evaluates the working tree against the
latest tag in isolation. It has no input about *intent* — it does not know
whether the operator is mid-release or whether a long-standing drift has
appeared. Its heuristic is "lag marker means intentional, no marker means
drift." That heuristic is adequate for steady-state main but fails the moment
a release is in progress, because the only way to signal intent in steady
state (the lag marker) is also the only way to signal intent during a
release window.

## Deterministic fix

Add a second intent signal that is naturally present during a real release
and naturally absent during silent drift: **the head commit on `main` is the
release bump.** Specifically:

- The check already knows `VERSION` and the latest tag.
- It can additionally inspect `git log -1 --format=%s HEAD` and look for the
  literal subject `release: bump to v<VERSION>`.
- If that subject is present **and** the version it names matches the
  working-tree `VERSION` **and** `CHANGELOG.md` carries a
  `## v<VERSION> - <date>` section (already required by check [6]), the
  "main ahead of tag" condition is rebranded from FAIL to PASS with a
  `release-in-progress` note. No lag marker required.
- Anything else — main ahead with no bump commit at HEAD, or with a bump
  commit naming a different version, or with no CHANGELOG section — keeps
  the existing FAIL behaviour. The original drift class (v0.9.0 / v0.10.1)
  is still caught because those drifts did not have a release bump commit at
  HEAD.

This is deterministic because:

1. **Convention is already in force.** Every release in repo history follows
   the `release: bump to vX.Y.Z` subject (`b0fc0b2 release: bump to v0.13.1`,
   `683ac5a release: bump to v0.13.0`, `137036a release: bump to v0.12.1`,
   `94b4e41 release: bump to v0.12.0`, etc., back to `aa6e5b7` for v0.5.0).
   We do not need to mandate anything new.
2. **Two independent signals.** The check requires both the bump subject
   and the CHANGELOG section to agree on the version. A typo or a
   half-finished release (bump committed, CHANGELOG not yet drafted) does
   not silently pass.
3. **No operator action.** No flags to remember, no markers to add and
   remove. The fix self-applies for the entire bump-to-tag window.
4. **Original drift class still caught.** A drift commit (e.g. somebody
   manually editing `VERSION` to `0.13.3` outside a release flow) does not
   have a `release: bump to vX.Y.Z` HEAD subject, so the check still FAILs.

### Out of scope (alternatives considered and rejected)

- **Add `--target=v<X>` to the check.** Threads the target version from
  `/release-audit` down into the check. Rejected: requires a parameter on
  every invocation, and the check would still need to find some signal to
  validate the target — at which point we are back to inspecting the bump
  commit. Adding both a parameter and the bump-commit recognition is
  overkill; just the bump-commit recognition is enough.
- **Time-bounded grace window.** Allow main to be ahead for N minutes after
  the bump commit. Rejected: brittle (depends on commit timestamps), does
  not survive operator pause-for-review, introduces a clock dependency.
- **Skip check [11] when `version` argument is passed to `/release-audit`.**
  Rejected: this is the "hide the warning" pattern. We want the original
  drift class to remain caught even during a release audit; what we want to
  remove is the false-FAIL, not the whole check.

## Action items

- File a tracked follow-up (P2) for the deterministic fix to
  `tools/check_main_release_consistency.py` and the corresponding
  `check_main_release_consistency_test` updates.
- The fix needs three test cases:
  1. **In-flight release** — HEAD subject `release: bump to v0.15.0`,
     `VERSION` 0.15.0, CHANGELOG has `## v0.15.0`. Expected: PASS with
     `release-in-progress` note.
  2. **Drift without bump commit** — HEAD subject `feat(x): something`,
     `VERSION` 0.15.0, latest tag v0.14.0. Expected: FAIL (today's
     behaviour, preserved).
  3. **Mismatched bump commit** — HEAD subject `release: bump to v0.16.0`,
     `VERSION` 0.15.0. Expected: FAIL (the bump and the version disagree —
     a real bug shape).
- ADR 0015 should be amended (one line) noting that check [11] now
  recognises the `release: bump to vX.Y.Z` HEAD subject as a release-in-
  progress signal.

## Lessons

- **Audit checks need an "intent" axis when they protect a transition
  state.** A check that fires only on steady-state drift is useful; a check
  that fires equally during legitimate transitions is noise.
- **Convention is a free signal.** When the repo already follows a strict
  convention (every release uses the same commit subject), the audit can
  read that convention to disambiguate intent without adding new mandatory
  artefacts.
- **NO-GO must mean NO-GO.** The moment an audit produces routine false-FAILs
  that operators learn to override, the audit is no longer a gate. Fix
  noise sources before they erode trust in the verdict.

## Related incident in the same release: stale public/ clone bypassed `tools/publish.sh`

Triggered during the same v0.14.0 release as a separate failure. Capturing
here because the recovery interleaved with the audit story above; tracked as
its own bd follow-up since the fix is in a different file.

### What happened

After the audit produced its NO-GO verdict (above) and the operator
authorised the publish, `tools/publish.sh --push` ran, synced files to the
local `public/` clone, applied overlays, committed `8913e68 Release v0.14.0`
on top of local `public/` HEAD, tagged both repos, and tried to push public
`main`. The push was rejected:

```
! [rejected]        main -> main (fetch first)
error: failed to push some refs to 'github.com:configflux/weld.git'
```

Local `public/` was at `7d73abc Release v0.13.1`. Remote `origin/main` was
at `a104026 Release v0.13.2`. The local clone had not fetched since v0.13.1
was published, so it never knew about the v0.13.2 release that the operator
had cut on a different machine. `tools/publish.sh` used the stale local
HEAD as the base for the v0.14.0 sync commit, producing a fork:
`8913e68 Release v0.14.0` parented on v0.13.1 instead of v0.13.2.

The remote refused the push (good — the protective failsafe worked).
Recovery required:

1. Reset local `public/` to `origin/main` (`git reset --hard`).
2. Delete the local tags `publish/v0.14.0` and `v0.14.0` (none had been
   pushed, so nothing to revoke remotely).
3. Re-run `tools/publish.sh --message "Release v0.14.0" --push` from the
   fresh base.

The second run produced a clean v0.14.0 commit on top of v0.13.2.

### Root cause

`tools/publish.sh` does not run `git fetch` on the local `public/` clone
before computing the sync. It treats local `public/` HEAD as authoritative.
This is wrong whenever a previous release was cut from a different machine
or by a different operator: the local clone is not the source of truth,
the remote is.

### Why the failsafe held

The remote-side non-fast-forward rejection on push prevented the wrong
history from reaching `origin/main`. Without that protection, the publish
would have succeeded and overwritten v0.13.2's release commit with a v0.14.0
commit parented on v0.13.1, losing v0.13.2's metadata in the public history
and potentially confusing GitHub Releases / PyPI ordering.

### Deterministic fix (operator decision, 2026-05-02)

Treat `public/` as a **transient release-time artifact**, not a long-lived
working clone. Lifecycle:

1. **Pre-release:** `tools/setup_public_repo.sh` clones `public/` fresh from
   origin. There is no `public/` directory between releases.
2. **Release:** `tools/publish.sh --push` runs against the freshly-cloned
   `public/`.
3. **Post-release green CI:** `rm -rf public/`.
4. Next release starts at step 1.

This is strictly stronger than the obvious fix (fetch + assert local HEAD
== origin/main): there is no local state for staleness to attach to. A
fresh clone IS by definition at origin/main. The cross-machine release
scenario (which is what bit v0.14.0 — a previous release cut from a
different machine) survives without any special-case logic, and there are
no edge cases like uncommitted changes in `public/`, divergent local
branches, or in-flight rebases.

Implementation in `tools/publish.sh`:

```bash
if [ ! -d "${PUBLIC_DIR}" ]; then
  log_error "public/ does not exist. Run tools/setup_public_repo.sh and retry."
  exit 1
fi

git -C "${PUBLIC_DIR}" fetch origin main
local_head=$(git -C "${PUBLIC_DIR}" rev-parse HEAD)
remote_head=$(git -C "${PUBLIC_DIR}" rev-parse origin/main)
if [ "$local_head" != "$remote_head" ]; then
  log_error "public/ HEAD ($local_head) is not at origin/main ($remote_head). rm -rf public/ && tools/setup_public_repo.sh, then retry."
  exit 1
fi
```

The fix belongs early in `publish.sh` — before `sync_files`, before
`apply_overlays`, before any mutation of `public/`. The error message
tells the operator the exact recovery: nuke and re-clone.

### Action item

Tracked follow-up (P1):

1. Land the pre-condition check in `tools/publish.sh`.
2. Add a regression test: `public/` missing → exit non-zero before any
   work. `public/` at older commit than `origin/main` → exit non-zero
   before any work.
3. Document the lifecycle in `docs/release.md` (or wherever release prep
   is documented).
4. Establish the post-CI cleanup as the standing operator practice
   (delete `public/` after every release). Optionally codify in
   `tools/cleanup_release_public.sh`.
