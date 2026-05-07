"""Bazel label resolution for the bazel discovery strategy (ADR 0044).

Resolves Bazel label strings into canonical weld node IDs. Pure, total,
deterministic; no I/O. Externals (``@workspace//...``) and malformed
labels return ``None`` so callers can drop them silently and bump a
``unresolved_labels_dropped`` counter for visibility.

Two resolvers, one per edge class:

- :func:`resolve_src_label` -- maps a ``srcs`` entry to a ``file:`` ID
  using :func:`weld._node_ids.file_id`. Used to emit
  ``build-target -> contains -> file:<src>`` edges.
- :func:`resolve_dep_label` -- maps a ``deps`` entry to a
  ``build-target:`` ID matching the format the bazel strategy uses for
  the target nodes themselves. Used to emit
  ``build-target -> depends_on -> build-target`` edges.

Out of scope (will resolve to ``None`` and be dropped silently):
``select(...)`` expressions, ``config_setting`` references, ``alias``
targets, and ``@external//...`` labels. These become follow-up work if
the v1 blast-radius ROI demands them.
"""

from __future__ import annotations

from weld._node_ids import file_id

__all__ = [
    "resolve_dep_label",
    "resolve_src_label",
]


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


def resolve_src_label(label: str, pkg_label: str) -> str | None:
    """Resolve a ``srcs`` label to a ``file:`` node ID.

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

    The returned ID is the canonical ``file:`` form from
    :func:`weld._node_ids.file_id` (extension stripped, slugged per
    segment), so it matches whatever the file-extracting strategies emit
    for the same on-disk path.
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
        rel = f"{pkg_part}/{name}" if pkg_part else name
        return file_id(rel)

    if label.startswith(":"):
        # Relative label: :filename. Resolve against pkg_label.
        name = label[1:]
        if not name:
            return None
        pkg_dir = _pkg_dir(pkg_label)
        rel = f"{pkg_dir}/{name}" if pkg_dir else name
        return file_id(rel)

    # Bare filename. Resolve against pkg_label.
    pkg_dir = _pkg_dir(pkg_label)
    rel = f"{pkg_dir}/{label}" if pkg_dir else label
    return file_id(rel)


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
