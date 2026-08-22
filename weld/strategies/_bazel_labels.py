"""Bazel label resolution for the bazel discovery strategy (ADR 0044, 0121).

Resolves Bazel label strings into canonical weld node IDs. Pure, total,
deterministic; no I/O. Malformed labels return ``None`` so callers can drop
them silently and bump a ``unresolved_labels_dropped`` counter for
visibility.

Four resolvers, one per edge class:

- :func:`resolve_src_labels` -- maps a ``srcs`` entry to every plausible
  node-ID spelling for the file it names, via the shared referrer rule in
  :mod:`weld.strategies._target_ids`. Used to emit
  ``build-target -> contains -> <src>`` edges, one per candidate, with the
  post-processor's dangling-edge sweep keeping whichever resolved.
- :func:`resolve_dep_label` -- maps an **in-repo** ``deps`` entry to a
  ``build-target:`` ID matching the format the bazel strategy uses for
  the target nodes themselves. Used to emit
  ``build-target -> depends_on -> build-target`` edges.
- :func:`resolve_external_dep_label` -- maps an **external-workspace**
  ``deps`` entry (``@repo//pkg[:name]``) to an ``external-dep:`` node id
  (ADR 0121). Deliberately a separate function from
  :func:`resolve_dep_label` rather than a branch inside it: the two
  destination node types get different edge treatment at the call site
  (only an in-repo dep also earns a ``test-target``'s inferred ``tests``
  edge), so the caller needs to tell them apart, not just get a node id.
- :func:`data_edges` -- the ``data`` attribute's resolver, which composes
  :func:`resolve_dep_label` and :func:`resolve_src_labels` and is
  *deferred*: bazel spells a target and a source file identically, so only
  the finished target set can tell them apart. It takes the minted node map
  and therefore runs as a second pass, which is why it is a function here
  rather than a branch in either resolver above. External labels in
  ``data`` are not resolved (ADR 0121 scopes the external-dep resolver to
  ``deps``; no BUILD file in this repo names one in ``data``).

Out of scope (will resolve to ``None`` and be dropped silently, in every
slot): ``select(...)`` expressions, ``config_setting`` references, ``alias``
targets, and the bzlmod self-reference ``@//...`` (not a dependency on
anything external). ``srcs`` and ``data`` still drop every ``@repo//...``
label; only :func:`resolve_external_dep_label` resolves them, and only
:mod:`weld.strategies.bazel`'s ``deps`` loop calls it. These become
follow-up work if the ROI demands them.

:func:`edge_props` is the one export here that resolves nothing: it is the
single ``props`` constructor every bazel edge goes through, and it lives
beside the resolvers rather than in ``bazel.py`` because :func:`data_edges`
is here and would otherwise import back into its own caller. One constructor
is the point -- a new edge kind cannot reach the graph unstamped by
forgetting a dict literal (bd cpkp).
"""

from __future__ import annotations

from weld._node_ids import entity_id
from weld.strategies._target_ids import target_ids

__all__ = [
    "data_edges",
    "edge_props",
    "resolve_dep_label",
    "resolve_external_dep_label",
    "resolve_src_labels",
    "resolve_src_path",
]


def edge_props(build_file: str, confidence: str = "definite") -> dict:
    """Return the ``props`` every bazel-emitted edge carries.

    ``provenance.file`` is the **BUILD file** this edge was declared in --
    never the file the edge points at. ADR 0074's incremental purge keeps an
    edge across a node purge only when it can attribute it to a file; an
    unattributed one falls back to endpoint membership and is dropped whenever
    *either* endpoint is purged. Every edge here crosses out of the BUILD file
    into something another source entry owns (a ``srcs`` file, a sibling
    target, a loaded ``.bzl``), so without this stamp, editing a declared
    source purged its ``file:`` node and took the inbound ``contains`` edge
    with it -- and the BUILD file being clean, this strategy's glob held no
    dirty file, never re-ran, and never re-minted it. Measured on this repo:
    editing any ``weld/*.py`` lost that file's two ``contains`` edges and no
    later incremental run restored them (bd cpkp). The direction is what makes
    the stamp correct as well as sufficient: an edit to the BUILD file itself
    *is* in the stale set, so the edges it produced are purged and re-minted
    by the re-run, exactly as a full discover would have them.
    """
    return {
        "source_strategy": "bazel",
        "confidence": confidence,
        "provenance": {"file": build_file},
    }


def _pkg_dir(pkg_label: str) -> str:
    """Return the repo-relative directory portion of a ``//pkg`` label.

    ``"//"``      -> ``""`` (root package)
    ``"//weld"``  -> ``"weld"``
    ``"//a/b/c"`` -> ``"a/b/c"``
    """
    if not pkg_label.startswith("//"):
        return ""
    rest = pkg_label[2:]
    return rest


def _is_external(label: str) -> bool:
    """External-workspace labels start with ``@``. Always dropped."""
    return label.startswith("@")


def resolve_src_path(label: str, pkg_label: str) -> str | None:
    """Resolve a ``srcs`` label to the repo-relative path it names.

    Accepted label forms:

    - ``"foo.py"``              (bare filename, relative to ``pkg_label``)
    - ``":foo.py"``             (colon-prefixed, relative to ``pkg_label``)
    - ``"//path/to:foo.py"``   (absolute label with filename)
    - ``"//path/to:foo"``       (absolute label, treated as filename)
    - ``"//:foo.py"``          (root-package absolute)

    Rejected (returns ``None``):

    - ``"@external//..."``      (external workspace)
    - empty string
    - bare path with no filename portion (``"//path/to"`` with no colon)

    A path, not an ID: which ID class the file reached the graph under is
    the *claiming* strategy's decision, and this module is a referrer.
    :func:`resolve_src_labels` applies the shared spelling rule on top.
    """
    if not label or _is_external(label):
        return None

    if label.startswith("//"):
        # Absolute label: //path:filename or //path/to:name
        if ":" not in label[2:]:
            # Bare //path with no filename -- not a valid src form.
            return None
        pkg_part, _, name = label[2:].partition(":")
        if not name:
            return None
        return f"{pkg_part}/{name}" if pkg_part else name

    if label.startswith(":"):
        # Relative label: :filename. Resolve against pkg_label.
        name = label[1:]
        if not name:
            return None
        pkg_dir = _pkg_dir(pkg_label)
        return f"{pkg_dir}/{name}" if pkg_dir else name

    # Bare filename. Resolve against pkg_label.
    pkg_dir = _pkg_dir(pkg_label)
    return f"{pkg_dir}/{label}" if pkg_dir else label


def resolve_src_labels(label: str, pkg_label: str) -> list[str]:
    """Return every plausible node-ID spelling for a ``srcs`` label.

    Empty when :func:`resolve_src_path` rejects the label, so a caller can
    still count it as unresolved.

    This used to mint ``file:`` unconditionally, which made the ``srcs``
    edge wrong in both directions.

    *Missing*, in the common case: a BUILD file names ``publish.sh`` and
    ``tool_script`` had minted ``tool:tools/publish`` for it, so the
    ``file:tools/publish`` edge pointed at nothing and the dangling sweep
    removed it without a word. Across this repo that silently dropped 31
    ``contains`` edges -- every shell script and extensionless entry point
    the build declares -- so ``wd impact tools/publish.sh`` could not
    reach the target that ships it (bd i7ny).

    *Wrong*, latently: :func:`weld._node_ids.file_id` strips the final
    extension, so ``srcs = ["install.sh"]`` spells ``file:install``, which
    an unrelated ``install.py`` would answer to. That is the confidently
    wrong claim :data:`weld.strategies._target_ids.FILE_NODE_EXTENSIONS`
    exists to refuse, and the dangling sweep cannot catch it because the
    node it lands on is real. Deferring to :func:`~weld.strategies.
    _target_ids.target_ids` fixes both at once: it offers ``file:`` only
    for the extensions ``file:`` nodes are minted for.

    A ``srcs`` entry for a file no strategy claimed still resolves to
    nothing, and that is the intended answer rather than a residual gap:
    the node set is ``.weld/discover.yaml``'s decision, and a BUILD glob
    is not a second, implicit discovery scope (ADR 0111).
    """
    rel = resolve_src_path(label, pkg_label)
    if rel is None:
        return []
    return target_ids(rel)


def resolve_dep_label(label: str, pkg_label: str) -> str | None:
    """Resolve a ``deps`` label to a ``build-target:`` node ID.

    Accepted label forms:

    - ``":target"``             (relative, target in the same package)
    - ``"//path/to:target"``  (absolute, fully qualified)
    - ``"//path/to"``           (absolute with implicit target name --
      Bazel resolves to a target named after the last path segment)

    Rejected (returns ``None``):

    - ``"@external//..."``      (external workspace)
    - empty string
    - any string that is not a Bazel label form (no ``//`` prefix and no
      leading ``:``)

    The returned ID is ``build-target://<pkg>:<name>``, matching the
    format the bazel strategy already uses for the target nodes
    themselves so the edge resolves to a real node when one exists.
    Edges to nonexistent targets are still emitted -- they become
    "dangling" but are kept for diagnostic value (a real product fix
    surfaces as either a missing weld discovery node or a broken Bazel
    BUILD file).
    """
    if not label or _is_external(label):
        return None

    if label.startswith("//"):
        rest = label[2:]
        if ":" in rest:
            pkg_part, _, name = rest.partition(":")
            if not name:
                return None
        else:
            # //path/to -> implicit target name = last segment
            if not rest:
                return None
            pkg_part = rest
            name = rest.rsplit("/", 1)[-1]
        return f"build-target://{pkg_part}:{name}"

    if label.startswith(":"):
        name = label[1:]
        if not name:
            return None
        # Reuse pkg_label as-is (it is already in ``//pkg`` form).
        return f"build-target:{pkg_label}:{name}"

    # Anything else (bare names, relative paths, etc.) is not a valid
    # dep label and must be dropped.
    return None


def resolve_external_dep_label(label: str) -> tuple[str, str, str] | None:
    """Resolve a ``deps`` label naming an external-workspace target (ADR 0121).

    Accepted label forms, both spellings of the same dependency resolving to
    the same *node_id*:

    - ``"@pypi//tree_sitter_cpp"``   (implicit target name = last path
      segment, mirroring :func:`resolve_dep_label`'s in-repo ``//path/to``
      rule)
    - ``"@pypi//tree_sitter_cpp:tree_sitter_cpp"`` (explicit colon name)
    - ``"@pypi//:tree_sitter_cpp"``  (root-of-repo colon form)

    Returns ``(node_id, repo, name)``: *node_id* is
    ``external-dep:<repo>:<name>`` via :func:`weld._node_ids.entity_id`
    (case-folded, so ``@PyPI//x`` and ``@pypi//x`` collide on purpose --
    package ecosystems are case-insensitive, the same reasoning
    :func:`weld._node_ids.package_id` already applies to C#/ROS2
    namespaces). *repo* and *name* are the **raw**, un-folded strings as
    written in the label, for callers that want the display spelling.

    *repo* is the external workspace name taken verbatim -- ``pypi`` for
    this repo's ``rules_python`` ``pip.parse(hub_name = "pypi")`` hub, and
    by the identical rule ``npm``/``crates`` for a hub of that name, with no
    per-ecosystem code required. No resolution beyond the label text is
    attempted: which version, or whether the repository is even declared in
    ``MODULE.bazel``, is out of scope -- the declared label is the fact the
    graph was missing, not what it refers to.

    Rejected (returns ``None``):

    - a label with no leading ``@`` (an in-repo label; :func:`resolve_dep_label`'s
      job, not this function's -- the two are mutually exclusive by
      construction so a caller can try one, then the other, and never
      double-count a label)
    - a bare ``@repo`` with no ``//`` at all
    - the bzlmod self-reference ``@//...`` (empty repo name -- not an
      external dependency, it names the root module itself)
    - ``@repo//`` with neither a package path nor a colon name to identify
      which target
    """
    if not label.startswith("@"):
        return None
    rest = label[1:]
    if "//" not in rest:
        return None
    repo, _, path_part = rest.partition("//")
    if not repo:
        return None
    pkg_path, _, colon_name = path_part.partition(":")
    if colon_name:
        name = colon_name
    elif pkg_path:
        name = pkg_path.rsplit("/", 1)[-1]
    else:
        return None
    node_id = entity_id("external-dep", platform=repo, name=name)
    return (node_id, repo, name)


def data_edges(
    pending: list[tuple[str, str, str, str]],
    nodes: dict[str, dict],
) -> list[dict]:
    """Resolve deferred ``data`` labels once every target node is known.

    Each *pending* entry is ``(from_nid, label, pkg_label, build_file)``; the
    trailing BUILD file is carried through the deferral only so the emitted
    edge can be stamped with its producer (see :func:`edge_props`) -- it is
    not otherwise part of the resolution.

    A ``data`` entry names either a target or a source file, and bazel spells
    both the same way. The rule: if the label resolves to a target this run
    actually minted, it is that target; otherwise it is a file. That order
    matters -- preferring the file reading would turn
    ``//examples:example_files`` into ``file:examples/example_files``, a path
    that does not exist, and the filegroup's inbound edges are the whole answer
    to "which tests execute against ``examples/``" (bd oj3m).

    Emitted as ``depends_on`` rather than a new ``runs_against`` edge type: a
    ``data`` entry is a declared build-graph dependency, which is exactly the
    class ADR 0107 says propagates through blast radius, so reusing the label
    arrives with its traversal question already answered.

    The file reading takes the same per-spelling treatment ``srcs`` does
    (ADR 0111): a ``data`` entry naming a shell script reaches the graph as
    ``tool:``, not ``file:``. The target reading is unaffected -- checked
    against ``nodes`` first, and it wins outright when it hits.
    """
    out: list[dict] = []
    for from_nid, label, pkg_label, build_file in pending:
        target_nid = resolve_dep_label(label, pkg_label)
        if target_nid is not None and target_nid in nodes:
            candidates = [target_nid]
        else:
            candidates = resolve_src_labels(label, pkg_label)
        for to_nid in candidates:
            if to_nid == from_nid:
                continue
            out.append({
                "from": from_nid,
                "to": to_nid,
                "type": "depends_on",
                "props": edge_props(build_file),
            })
    return out
