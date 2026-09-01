"""Materialises the field-eval synthetic 4-repo workspace on disk.

Two external evaluations shipped the *same* self-contained synthetic polyrepo
-- an Acme "order platform" of a protobuf schema library, a C# gateway that
consumes the schema via a ``<PackageReference>``, a Python notifier that
consumes it via a ``pyproject`` dependency, and a docs-only repo, federated
under a workspace root. The v0.23.1 bundle lives on under
``docs/field-reports/``; the v0.24.0 bundle (bd ...d76r1) is **not** committed,
so this module and :mod:`weld.tests._field_eval_corpus_sources` are where its
``fixture/make-fixture.sh`` survives.

What this module materialises is that script, faithfully -- including the two
shapes v0.24.0 added: a vendored ``.venv`` inside the notifier (finding N2) and
a first-party Python package imported by its own dotted name (finding N4). File
bodies live next door in ``_field_eval_corpus_sources``; the layout, the git
plumbing, and the two opt-in flags are here.

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

Faithfulness note worth keeping: ``make-fixture.sh`` commits the notifier
*before* writing ``src/acme_notify/``, the ``.venv`` and its ``.gitignore``, so
those arrive **untracked**. That is reproduced here rather than tidied up --
"untracked but not ignored" (acme_notify) versus "ignored" (.venv) is exactly
the git-visibility distinction the N2 fix has to get right, and the evaluator's
transcripts were taken from a tree in that state.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from weld.tests import _field_eval_corpus_sources as src

# Child names / paths mirror the shell fixture's ``.weld/workspaces.yaml``.
SCHEMA = ("libs-order-schema", "libs/order-schema")
GATEWAY = ("services-order-gateway", "services/order-gateway")
NOTIFY = ("services-notify-service", "services/notify-service")
DOCS = ("docs-site", "docs-site")

CHILDREN: tuple[tuple[str, str], ...] = (SCHEMA, GATEWAY, NOTIFY, DOCS)

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
  - name: libs-order-schema
    path: libs/order-schema
  - name: services-order-gateway
    path: services/order-gateway
  - name: services-notify-service
    path: services/notify-service
  - name: docs-site
    path: docs-site
cross_repo_strategies: [{cross_repo_strategies}]
"""

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

#: One representative node per child, id-shaped like the real discover output.
#: ``preseed`` writes these so the federated read path has a loadable child
#: graph; the E2E suites discover the real ones instead.
_PRESEED_NODES: dict[str, dict[str, str]] = {
    SCHEMA[0]: {"file:src/main/proto/acme/platform/order/schema/v1/event": "file"},
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
    """Render ``workspaces.yaml`` -- the two knobs the probes toggle.

    ``run-all-repros.sh`` flips ``cross_repo_strategies`` with ``sed`` between
    probes and flips ``respect_gitignore`` inside the N2 probe. Rendering from
    parameters keeps the tests off ``sed`` while producing the same file.
    """
    return _WORKSPACES_YAML_TEMPLATE.format(
        respect_gitignore="true" if respect_gitignore else "false",
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
    """Lay down the synthetic 4-repo workspace under *root*; return *root*.

    *git* runs ``git init`` + one commit per repo in ``make-fixture.sh``'s own
    order (see the module docstring on what that leaves untracked). It is
    required by anything that reads a child's lifecycle state -- the workspace
    ledger calls a child ``missing`` until it has a ``.git``.

    *preseed* additionally writes the per-child ``discover.yaml`` and a minimal
    child ``graph.json``, for in-process consumers that cannot shell out to
    ``wd init`` / ``wd discover``.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    # child: schema (producer)
    schema_root = root / SCHEMA[1]
    _write_all(schema_root, src.SCHEMA_FILES)
    if git:
        init_repo(schema_root)
        commit_all(schema_root, "order schema")

    # child: C# gateway (consumer via <PackageReference>)
    gateway_root = root / GATEWAY[1]
    _write_all(gateway_root, src.GATEWAY_FILES)
    if git:
        init_repo(gateway_root)
        commit_all(gateway_root, "order gateway")

    # child: Python notifier (consumer via pyproject dependency). The N4 and
    # N2 shapes land *after* the commit, exactly as the shell fixture does.
    notify_root = root / NOTIFY[1]
    _write_all(notify_root, src.NOTIFY_FILES)
    if git:
        init_repo(notify_root)
        commit_all(notify_root, "notify service")
    _write_all(notify_root, src.NOTIFY_FIRST_PARTY_FILES)
    _write_all(notify_root, src.NOTIFY_VENDORED_FILES)

    # child: docs-only repo
    docs_root = root / DOCS[1]
    _write_all(docs_root, src.DOCS_FILES)
    if git:
        init_repo(docs_root)
        commit_all(docs_root, "docs")

    if preseed:
        _preseed_child(schema_root, SCHEMA[0], _PY_CONFIG)
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
