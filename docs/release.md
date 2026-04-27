# Release Checklist

A runnable, linear checklist for cutting a release of `configflux-weld`.

This doc is for humans. It is **not** an auto-publish script. Each step lists
what to do, the exact command to run, and how to verify success before moving
on. Run the steps in order -- earlier checks catch problems the later ones
cannot.

For the "is this release safe to tag?" audit, use the `release-manager` agent
(`/release-audit`). See `docs/adrs/0015-release-manager-agent.md` for the
full list of pre-release checks and the read-only audit posture. That agent
is complementary to this checklist: the agent is the quick GO/NO-GO probe,
this doc is the end-to-end walkthrough.

For the publish-pipeline mechanics (how `tools/publish.sh` moves files from
the internal repo to the public overlay), see `docs/publish.md`.

Related tooling referenced below:

- `tools/publish.sh` -- one-way sync + commit + tag pair creation
- `tools/audit_publish.sh` -- pre-publish leak audit (writes evidence token)
- `tools/release_smoke.sh` -- overlay install smoke (build wheel, install,
  and run core CLI, Agent Graph, and MCP config commands)
- `tools/setup_public_repo.sh` -- one-time clone of the public repo into
  `./public/`
- `./local-task-gate` -- repo gate (lint + build + test, auto-scoped)
- `VERSION` -- single source of truth for the release version
- `weld/pyproject.toml` -- internal package metadata (must match `VERSION`)
- `MODULE.bazel` -- Bazel module metadata (must match `VERSION`)
- `CHANGELOG.md` -- human-readable release notes, gated by
  `tools/release_notes.py`

> Note: `public/weld/pyproject.toml` is **synced** by `tools/publish.sh` from
> `weld/pyproject.toml`; do not hand-edit it. The version triple that the
> publish pre-flight (`check_version_consistency` in
> `tools/publish_version_check.sh`) compares is `VERSION` +
> `weld/pyproject.toml` + `MODULE.bazel`.

---

## Before you start

- [ ] Clean working tree: `git status` shows nothing to commit.
- [ ] Main is up to date: `git pull --ff-only origin main`.
- [ ] Public checkout exists: `ls public/.git` (if not, run
  `tools/setup_public_repo.sh` once).

---

## 1. Update the version

Pick the new version `X.Y.Z` (semver). Update all three places that carry it
so the version triple stays consistent (ADR 0015 check 1).

```bash
echo "X.Y.Z" > VERSION
# Edit weld/pyproject.toml: version = "X.Y.Z"   (top-level, under [project])
# Edit MODULE.bazel:        version = "X.Y.Z"   (inside module(...))
```

Do **not** hand-edit `public/weld/pyproject.toml`; `tools/publish.sh` syncs
it from `weld/pyproject.toml` during step 5.

**Verify:**

```bash
cat VERSION
grep -E '^version[[:space:]]*=' weld/pyproject.toml
grep -E 'version[[:space:]]*=' MODULE.bazel
# All three must print X.Y.Z.
```

The publish pre-flight runs the same check. `publish_version_check.sh` is a
helper sourced by `publish.sh` (it defines `check_version_consistency`) and
is not runnable standalone; the quickest way to run it end-to-end is the
publish dry-run in step 5, which will abort with exit `65` on drift.

---

## 2. Run the full test suite

Run the repo gate at full scope. This lints, builds, and runs all tests.

```bash
./local-task-gate --scope=full
```

**Verify:** exit 0. Any failure here is a blocker; fix and re-run before
continuing. Do **not** skip to smoke testing while the gate is red.

---

## 3. Run the package install smoke test

Stage the overlay the same way publish would, build an sdist + wheel from
`weld/pyproject.toml`, install into a disposable venv, and run the installed
CLI through a small end-to-end repo workflow.

```bash
tools/release_smoke.sh
```

**Verify:** exit 0 and the final log line reads
`[smoke] Installed wheel workflow smoke passed.`

Notes:

- Prefers `tools/publish.sh --dry-run` staging when `public/.git` exists;
  falls back to a direct `weld/` copy otherwise. The fallback is announced
  on stderr.
- Requires `python3` with `ensurepip` and `python -m build`. On setup errors
  the script exits `65` and prints which tool to install.

---

## 4. Generate / update the changelog

`CHANGELOG.md` must contain a non-empty, non-placeholder section for the new
version before the tag goes out (ADR 0015 check 6 -- curated release notes).

- [ ] Add a new section at the top of `CHANGELOG.md`:

  ```markdown
  ## vX.Y.Z - YYYY-MM-DD

  ### Added
  - ...

  ### Changed
  - ...

  ### Fixed
  - ...
  ```

- [ ] Prune empty subsections. Leave only the subsections that have entries.
- [ ] Commit the changelog update (this will be part of the release commit).

**Verify:** the release notes gate passes:

```bash
python3 tools/release_notes.py check --version-file VERSION --changelog CHANGELOG.md
```

Expected: exit 0. This is the same check `tools/publish.sh` runs during
publish, so fixing it now avoids a late failure.

---

## 5. Tag the release

Tagging is done by `tools/publish.sh`. The script creates **both** tags
atomically after a successful sync + commit to `public/`:

- `publish/vX.Y.Z` on the internal repo (annotated, with source mapping).
- `vX.Y.Z` on the public repo (annotated, triggers PyPI publish).

Before invoking publish, run the audit so it has a fresh evidence token:

```bash
tools/audit_publish.sh
```

**Verify:** exit 0 and `.publish-evidence` is written for the current HEAD.

Then dry-run publish and review the diff:

```bash
tools/publish.sh --dry-run --message "Release vX.Y.Z"
cd public && git status && git diff --stat && cd -
```

**Verify:** the staged changes in `public/` look correct (version bumps,
overlays applied, no unexpected file leaks).

Now do the real publish. This commits to `public/` and creates the tag pair
locally; `--push` is deferred to step 6.

```bash
tools/publish.sh --message "Release vX.Y.Z"
```

**Verify:** log ends with `[publish] Publish complete.` and both tags exist:

```bash
git tag -l "publish/vX.Y.Z"
git -C public tag -l "vX.Y.Z"
# Both must print.
```

If you need to inspect the public commit before pushing:

```bash
cd public && git log --oneline -1 && git diff HEAD~1 --stat && cd -
```

See `docs/publish.md` for the full publish-pipeline mechanics (overlays,
`.publishignore`, marker stripping). See `tools/publish_release.sh` for the
tag + push policy sourced by `publish.sh`.

---

## 6. Push and confirm the PyPI publish

The public `vX.Y.Z` tag triggers the PyPI release workflow
(`.github/workflows/publish-pypi.yml` on the public repo, sourced from the
`tools/publish_overlays/publish-pypi.yml` overlay).

Re-run publish with `--push`, or push manually:

```bash
# Option A: let publish.sh push main + both tags
tools/publish.sh --push --message "Release vX.Y.Z"

# Option B: push manually
cd public && git push origin main && cd -
git push origin "publish/vX.Y.Z"
cd public && git push origin "vX.Y.Z" && cd -
```

**Verify:**

- [ ] Public `main` is up to date:
  ```bash
  cd public && git log --oneline origin/main -1 && cd -
  ```
- [ ] Both tags are pushed:
  ```bash
  git ls-remote --tags origin "publish/vX.Y.Z"
  git -C public ls-remote --tags origin "vX.Y.Z"
  ```
- [ ] The `publish-pypi` workflow on the public repo has completed
  successfully for the `vX.Y.Z` tag. Check the Actions tab, or:
  ```bash
  gh run list --repo configflux/weld --workflow publish-pypi.yml --limit 5
  ```
- [ ] The release appears on PyPI:
  ```bash
  curl -s https://pypi.org/pypi/configflux-weld/X.Y.Z/json | jq .info.version
  # Expected: "X.Y.Z"
  ```

If the PyPI workflow fails, do **not** delete or move the tag. Investigate
(`gh run view <run-id> --log`), fix forward, and cut a patch release. Tag
rewrites are forbidden by the publish policy (see `tools/publish_release.sh`
`ensure_tag_points_at`).

---

## 7. Verify the PyPI release via `uv tool install`

From a clean environment (not this repo), install the published CLI with
`uv`:

```bash
uv tool install configflux-weld==X.Y.Z
```

**Verify:**

```bash
wd --version
# Expected: wd X.Y.Z   (or a "wd <string>" line containing X.Y.Z)

wd --help | head -5
# Expected: subcommand help, no ImportError

wd graph stats
wd graph validate
# Expected: both work after discovery in a smoke repo.
```

Clean up when done:

```bash
uv tool uninstall configflux-weld
```

---

## 8. Verify the PyPI release via `pipx install`

From a clean environment, install the same version with `pipx`:

```bash
pipx install "configflux-weld==X.Y.Z"
```

**Verify:**

```bash
wd --version
# Expected: wd X.Y.Z   (or a "wd <string>" line containing X.Y.Z)

python -c "import weld; print(weld.__version__ if hasattr(weld, '__version__') else 'import ok')"
# Expected: exits 0.
```

Clean up when done:

```bash
pipx uninstall configflux-weld
```

If either install path fails on a version that PyPI says is published,
capture the error, open a release-blocker issue (`bd create ... --priority
0`), and cut a patch release. Do not retag.

---

## 9. Post-release: confirm main has not silently drifted

After the tag is pushed and the PyPI workflow is green, run the
public-main consistency check (ADR 0015 check 11) so a future bump on
`main` is not invisibly mismatched against the latest published wheel.

```bash
python3 tools/check_main_release_consistency.py
# or, with PyPI cross-check (opt-in; network failures soft-warn):
python3 tools/check_main_release_consistency.py --pypi
```

**Verify:** exit 0 with `[main-consistency] PASS: VERSION X.Y.Z matches
latest tag vX.Y.Z.` Any other verdict means main and the latest tag have
drifted apart and the next release will inherit the drift unless the
operator decides between two outcomes:

- **Tag the new version next.** Bump `VERSION`, `weld/pyproject.toml`,
  and `MODULE.bazel` together (step 1) and run this check again before
  step 5.
- **Document an intentional lag.** If main is meant to sit ahead of the
  latest tag (e.g., a staged release dated to a future Tuesday), add a
  marker to `README.md`:

  ```html
  <!-- release-lag: 0.11.0 staged for 2026-05-12 launch window -->
  ```

  The check will then verdict `WARN` and surface the reason instead of
  failing. Remove the marker on the day the matching tag is cut.

`/release-audit` invokes the same script as check 11; running it here
keeps the post-release state clean for the next pre-release audit.

---

## Rollback and recovery

- **Bad version file:** fix `VERSION` and both `pyproject.toml` files, re-run
  step 1's consistency check, and continue.
- **Gate red (step 2):** fix forward on `main` before resuming.
- **Smoke failure (step 3):** read `tools/release_smoke.sh` stderr; the
  exit code distinguishes setup errors (65) from real smoke failures (1).
- **Publish refused (step 5):** `tools/publish.sh` aborts rather than
  force-pushing or retagging. Investigate `public/` state manually. Never
  `--force`, never delete tags.
- **PyPI workflow red (step 6):** fix forward and release `X.Y.(Z+1)`.
- **Install smoke red (steps 7-8):** same as above -- cut a patch, do not
  retag.

---

## See also

- `docs/adrs/0015-release-manager-agent.md` -- read-only pre-release audit
  (eleven checks; GO/NO-GO report).
- `tools/check_main_release_consistency.py` -- post-release consistency
  guard (ADR 0015 check 11).
- `docs/adrs/0014-pypi-auto-publish.md` -- PyPI publish workflow, trusted
  publishing, Actions allowlist.
- `docs/publish.md` -- publish-pipeline mechanics (overlays,
  `.publishignore`, marker stripping, danger-pattern audit).
- `tools/publish.sh`, `tools/publish_release.sh`,
  `tools/publish_version_check.sh` -- the publish + tag machinery.
- `tools/release_smoke.sh` -- overlay install smoke.
- `tools/release_notes.py` -- changelog gate used by `publish.sh`.
