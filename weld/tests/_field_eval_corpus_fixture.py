"""Materialises the field-eval synthetic polyrepo workspace on disk.

Three external evaluations shipped the *same* self-contained synthetic
polyrepo -- an Acme "order platform" of a protobuf schema library, a C# gateway
that consumes the schema via a ``<PackageReference>``, a Python notifier that
consumes it via a ``pyproject`` dependency, and a docs-only repo, federated
under a workspace root -- and each round grew it. The v0.23.1 bundle lives on
under ``docs/field-reports/``; the v0.24.0 and v0.25.0 bundles (bd ...d76r1,
bd ...lcq0c) are **not** committed, so this module and its two payload
siblings are where their ``fixture/make-fixture.sh`` survives.

What this module materialises is that script, faithfully -- including the two
shapes v0.24.0 added (a vendored ``.venv`` inside the notifier, finding N2, and
a first-party Python package imported by its own dotted name, finding N4) and
what v0.25.0 added: a fifth child ``libs/billing-schema``, whose published
package name exists only in an MSBuild ``<PackageId>`` and which the gateway
references exactly as it references the proto library (finding M4), plus four
more Python import shapes inside the notifier (relative, lazy, classmethod,
sibling bare-name -- checks X3 and X4). File bodies live next door in
``_field_eval_corpus_sources`` and, for C#/MSBuild,
``_field_eval_corpus_sources_csharp``; the layout, the git plumbing, and the
opt-in flags are here.

Two consumers, two modes:

* ``weld_field_eval_e2e_test`` / ``weld_field_eval_regression_e2e_test`` take
  it with ``git=True`` and **nothing pre-seeded**, then run the real
  ``wd init`` / ``wd discover`` bootstrap as subprocesses. That is the whole
  point of the E2E corpus: the configs and graphs under test are the ones weld
  itself writes.
* ``weld_field_eval_corpus_test`` runs in-process and takes ``preseed=True``,
  which stands in for that bootstrap with per-child ``discover.yaml`` files and
  minimal child graphs -- enough for the real ``build_root_meta_graph`` /
  ``merge_cross_repo_edges`` path to run without shelling out.

Faithfulness note worth keeping, because round three changed it: v0.24.0's
``make-fixture.sh`` committed the notifier *before* writing
``src/acme_notify/``, the ``.venv`` and its ``.gitignore``, so all three
arrived untracked; v0.25.0's commits that repo last, so everything but the
``.venv`` is tracked and the ``.gitignore`` naming it is tracked too. Both
orders keep the distinction the N2 fix has to get right -- a vendored tree git
is told to ignore is still walked by a resolver that does not ask git -- and
the second is the state the v0.25.0 transcripts were taken from, so it is the
one reproduced here. Do not "fix" the order to match an older transcript;
``//weld/tests:weld_field_eval_bundle_test``'s drift guard compares this
module's tracked sets against the shell script's and will say so.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from weld.tests import _field_eval_corpus_sources as src
from weld.tests import _field_eval_corpus_sources_csharp as cs

# Child names / paths mirror the shell fixture's ``.weld/workspaces.yaml``.
SCHEMA = ("libs-order-schema", "libs/order-schema")
BILLING = ("libs-billing-schema", "libs/billing-schema")
GATEWAY = ("services-order-gateway", "services/order-gateway")
NOTIFY = ("services-notify-service", "services/notify-service")
DOCS = ("docs-site", "docs-site")

#: The corpus roster: v0.24.0's four plus ``libs/billing-schema``, the C#-only
#: producer round three added. Ordered as the workspace config lists them.
#:
#: One roster, not a parameter. It briefly was one -- the bundle-lane drift
#: guard had to pin the four-child shape while the shell scripts in
#: ``weld/tests/field_eval/`` were a round behind the materialiser -- and
#: absorbing the 0.25.0 scripts (bd lcq0c.2) put both halves on this roster,
#: so the variation and its ``children=`` plumbing went with it.
CHILDREN: tuple[tuple[str, str], ...] = (SCHEMA, BILLING, GATEWAY, NOTIFY, DOCS)

#: Hermetic git: own identity, no ambient user config, stable locale. Mirrors
#: ``_seed_fixture.GIT_ENV`` -- see that module for why this is not optional.
GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}

_WORKSPACES_YAML_TEMPLATE = """\
version: 1
scan:
  max_depth: 4
  respect_gitignore: {respect_gitignore}
  exclude_paths: [.worktrees]
children:
{children}cross_repo_strategies: [{cross_repo_strategies}]
"""

_CHILD_ENTRY = "  - name: {name}\n    path: {path}\n"

_ROOT_WELD_GITIGNORE = (
    "# Track the shared workspace config; ignore generated local state.\n"
    "*\n"
    "!.gitignore\n"
    "!workspaces.yaml\n"
)

_ROOT_README = (
    "# Acme Platform (workspace root)\n\n"
    "Federated polyrepo workspace: schema library, two services, and a docs "
    "repo.\n"
)

#: The children are ignored at the root, which is what leaves a fresh root
#: worktree holding nothing but ``.weld/workspaces.yaml`` -- the N9 shape.
_ROOT_GITIGNORE = ".worktrees/\nlibs/\nservices/\ndocs-site/\n"

# ``preseed`` only. The gateway's config is deliberately markdown-only -- the
# 0.23.1 Finding-05 shape, a config generated before the C# strategy shipped,
# so 100% of the .cs source is unclaimed while doctor reports healthy. The E2E
# suites do NOT get this; they let ``wd init`` write the real thing.
_MARKDOWN_ONLY_CONFIG = """\
sources:
  - glob: "doc/*.md"
    type: doc
    strategy: markdown
    id_prefix: doc:doc
"""

_PY_CONFIG = """\
sources:
  - glob: "**/*.py"
    type: symbol
    strategy: python_module
    id_prefix: symbol:py
"""

#: ``preseed`` only. What ``wd init`` writes for a repo whose only source is
#: C# -- the billing child. Unlike the gateway's markdown-only stand-in above,
#: nothing about this one is a defect shape: M4 is about the manifest scan,
#: which reads the ``.csproj`` off disk and never consults this file.
_CSHARP_CONFIG = """\
sources:
  - glob: "**/*.cs"
    type: symbol
    strategy: csharp
    id_prefix: symbol:cs
"""

#: One representative node per child, id-shaped like the real discover output.
#: ``preseed`` writes these so the federated read path has a loadable child
#: graph; the E2E suites discover the real ones instead.
_PRESEED_NODES: dict[str, dict[str, str]] = {
    SCHEMA[0]: {"file:src/main/proto/acme/platform/order/schema/v1/event": "file"},
    BILLING[0]: {"package:csharp:acme.platform.billing.schema": "package"},
    GATEWAY[0]: {"package:csharp:acme.platform.ordergateway.orderreplayer": "package"},
    NOTIFY[0]: {"file:src/main": "file", "file:src/broker": "file"},
    DOCS[0]: {"doc:md/platform-overview": "doc"},
}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_all(child_root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        _write(child_root / rel, content)


def _git(root: Path, *args: str) -> str:
    """Run one git command in *root* with the hermetic environment."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=GIT_ENV,
        check=True,
    )
    return proc.stdout.strip()


def init_repo(root: Path) -> None:
    """``git init`` with the fixture's own identity (no ambient config)."""
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "commit.gpgsign", "false")


def commit_all(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def workspaces_yaml(
    *,
    cross_repo_strategies: tuple[str, ...] | list[str] = (),
    respect_gitignore: bool = False,
) -> str:
    """Render ``workspaces.yaml`` -- the knobs the probes toggle.

    ``run-all-repros.sh`` flips ``cross_repo_strategies`` with ``sed`` between
    probes and flips ``respect_gitignore`` inside the N2 probe. Rendering from
    parameters keeps the tests off ``sed`` while producing the same file.

    The child roster is :data:`CHILDREN` and is not a knob: a rewrite
    mid-suite must not be able to deregister a child the materialiser put on
    disk.
    """
    return _WORKSPACES_YAML_TEMPLATE.format(
        respect_gitignore="true" if respect_gitignore else "false",
        children="".join(
            _CHILD_ENTRY.format(name=name, path=path) for name, path in CHILDREN
        ),
        cross_repo_strategies=", ".join(cross_repo_strategies),
    )


def write_workspaces_yaml(
    root: Path,
    *,
    cross_repo_strategies: tuple[str, ...] | list[str] = (),
    respect_gitignore: bool = False,
) -> Path:
    """Write (or rewrite) the root ``workspaces.yaml``; return its path."""
    path = Path(root) / ".weld" / "workspaces.yaml"
    _write(
        path,
        workspaces_yaml(
            cross_repo_strategies=cross_repo_strategies,
            respect_gitignore=respect_gitignore,
        ),
    )
    return path


def _preseed_child(child_root: Path, name: str, config: str) -> None:
    """Stand in for the ``wd init`` + ``wd discover`` bootstrap, in-process."""
    _write(child_root / ".weld" / "discover.yaml", config)
    payload = {
        "meta": {"version": 1, "schema_version": 1},
        "nodes": {
            node_id: {"type": node_type, "label": node_id.rsplit(":", 1)[-1]}
            for node_id, node_type in _PRESEED_NODES[name].items()
        },
        "edges": [],
    }
    _write(
        child_root / ".weld" / "graph.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def materialize_workspace(
    root: Path,
    *,
    git: bool = False,
    preseed: bool = False,
    cross_repo_strategies: tuple[str, ...] | list[str] = (),
    respect_gitignore: bool = False,
) -> Path:
    """Lay down the synthetic polyrepo workspace under *root*; return *root*.

    *git* runs ``git init`` + one commit per repo in ``make-fixture.sh``'s own
    order (see the module docstring on what that leaves untracked). It is
    required by anything that reads a child's lifecycle state -- the workspace
    ledger calls a child ``missing`` until it has a ``.git``.

    *preseed* additionally writes the per-child ``discover.yaml`` and a minimal
    child ``graph.json``, for in-process consumers that cannot shell out to
    ``wd init`` / ``wd discover``.

    The roster is always :data:`CHILDREN` -- see that constant on why it is no
    longer a parameter.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    # child: schema (producer)
    schema_root = root / SCHEMA[1]
    _write_all(schema_root, src.SCHEMA_FILES)
    if git:
        init_repo(schema_root)
        commit_all(schema_root, "order schema")

    # child: C#-only schema library (producer, MSBuild <PackageId> only)
    billing_root = root / BILLING[1]
    _write_all(billing_root, cs.BILLING_FILES)
    if git:
        init_repo(billing_root)
        commit_all(billing_root, "billing schema")

    # child: C# gateway (consumer via <PackageReference>)
    gateway_root = root / GATEWAY[1]
    _write_all(gateway_root, cs.GATEWAY_FILES)
    _write(gateway_root / cs.GATEWAY_CSPROJ_PATH, cs.gateway_csproj())
    if git:
        init_repo(gateway_root)
        commit_all(gateway_root, "order gateway")

    # child: Python notifier (consumer via pyproject dependency). Every shape
    # lands *before* the commit, exactly as the shell fixture does -- round
    # three moved `git_init`/`git_commit` to the end of this repo's block; see
    # the module docstring on what that changed about git visibility.
    notify_root = root / NOTIFY[1]
    _write_all(notify_root, src.NOTIFY_FILES)
    _write_all(notify_root, src.NOTIFY_FIRST_PARTY_FILES)
    _write_all(notify_root, src.NOTIFY_V0250_FILES)
    _write_all(notify_root, src.NOTIFY_VENDORED_FILES)
    if git:
        init_repo(notify_root)
        commit_all(notify_root, "notify service")

    # child: docs-only repo
    docs_root = root / DOCS[1]
    _write_all(docs_root, src.DOCS_FILES)
    if git:
        init_repo(docs_root)
        commit_all(docs_root, "docs")

    if preseed:
        _preseed_child(schema_root, SCHEMA[0], _PY_CONFIG)
        _preseed_child(billing_root, BILLING[0], _CSHARP_CONFIG)
        _preseed_child(gateway_root, GATEWAY[0], _MARKDOWN_ONLY_CONFIG)
        _preseed_child(notify_root, NOTIFY[0], _PY_CONFIG)
        _preseed_child(docs_root, DOCS[0], _PY_CONFIG)

    # workspace root
    write_workspaces_yaml(
        root,
        cross_repo_strategies=cross_repo_strategies,
        respect_gitignore=respect_gitignore,
    )
    _write(root / ".weld" / ".gitignore", _ROOT_WELD_GITIGNORE)
    _write(root / "README.md", _ROOT_README)
    if git:
        init_repo(root)
        _write(root / ".gitignore", _ROOT_GITIGNORE)
        commit_all(root, "workspace root")
    return root
