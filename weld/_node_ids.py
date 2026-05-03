"""Canonical node-ID contract for weld discovery (ADR 0041, Layer 1).

This module is the *single* source of truth for how weld mints node IDs.
Every strategy and every reference-creator imports the helpers here
instead of constructing IDs inline. The functions are pure, total, and
deterministic so the same input always produces the same ID.

The module sits at the bottom of weld's dependency DAG: it imports only
from the standard library and has no dependency on ``weld.graph_closure``
or any strategy. Direct use of ``re.sub`` against ID-shaped strings
elsewhere is banned by ADR 0041; the lint rule that enforces this ships
in PR 3.

See ``docs/adrs/0041-graph-closure-determinism.md`` for the rationale and
the full migration table.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Union

# The permitted character set for canonical slugs. Lowercase ASCII
# alphanumerics, dot, colon, underscore, and dash are kept; everything
# else collapses to a single dash. Colon is permitted because IDs are
# colon-segmented (``type:platform:name``); dot and underscore are
# permitted because they appear in legal identifiers (``pkg.sub``,
# ``__init__``).
_ALLOWED_RE = re.compile(r"[^a-z0-9._:-]+")
#: Coalesce runs of dashes (introduced by the substitution above, or
#: pre-existing in the input) into a single dash. Applied after the
#: substitution so that mixed runs (e.g. ``--*--``) collapse correctly.
_MULTI_DASH_RE = re.compile(r"-{2,}")

#: Sentinel returned by :func:`canonical_slug` when the input collapses
#: to the empty string. Stable so callers can detect "we tried to slug
#: something benign-but-empty" without a special-case test.
_EMPTY_SENTINEL = "unknown"


def canonical_slug(value: str) -> str:
    """Return a deterministic, total slug for *value*.

    The function is the single replacement for the three divergent
    ``_slug`` implementations that previously coexisted in
    ``agent_graph_materialize.py``, ``graph_closure.py``, and
    ``runtime_contract.py``.

    Behaviour:

    - Strip leading and trailing whitespace; lowercase via ASCII.
    - Permit ``[a-z0-9._:-]`` (lowercase ASCII alphanumerics, dot, colon,
      underscore, dash). Every other character collapses to a single
      dash. This includes Unicode, slashes, backslashes, NUL bytes, and
      shell metacharacters.
    - Coalesce multi-dashes into a single dash.
    - Strip leading and trailing dashes.
    - Return ``"unknown"`` if the result is empty.

    The function never raises; it is total over ``str``.

    Examples
    --------
    >>> canonical_slug("Hello World")
    'hello-world'
    >>> canonical_slug("foo/bar/baz")
    'foo-bar-baz'
    >>> canonical_slug("café")
    'caf'
    >>> canonical_slug("---")
    'unknown'
    >>> canonical_slug("a..b")
    'a..b'
    """
    if not isinstance(value, str):  # pragma: no cover - defensive total guard
        value = str(value)
    lowered = value.strip().lower()
    collapsed = _ALLOWED_RE.sub("-", lowered)
    coalesced = _MULTI_DASH_RE.sub("-", collapsed)
    trimmed = coalesced.strip("-")
    return trimmed or _EMPTY_SENTINEL


def file_id(rel_path: Union[str, PurePosixPath]) -> str:
    """Return the canonical ``file:`` ID for a repo-relative path.

    The ID is ``"file:" + rel_posix_path_without_extension``. The path is
    canonicalised to POSIX (forward-slash) form before stripping the
    final extension so the result is order-independent across operating
    systems, and the *full* path (not the bare stem) is used so two
    files with the same stem in different directories cannot collide.

    Examples
    --------
    >>> file_id("weld/strategies/python_module.py")
    'file:weld/strategies/python_module'
    >>> file_id("weld/_node_ids.py")
    'file:weld/_node_ids'
    >>> file_id("weld/strategies/_ros2_py.py")
    'file:weld/strategies/_ros2_py'
    >>> file_id("docs/adrs/0041-graph-closure-determinism.md")
    'file:docs/adrs/0041-graph-closure-determinism'
    >>> file_id("README")
    'file:readme'

    Path-traversal safety: the result is *only* a graph node ID — it
    is never used to open a file on disk. ``..`` segments are
    permitted by :func:`canonical_slug` (dot is a legal slug character
    so dotted package names round-trip), but the ``file:`` prefix
    namespaces the result so it cannot escape into another ID class.

    >>> file_id("../etc/passwd")
    'file:../etc/passwd'
    """
    if isinstance(rel_path, PurePosixPath):
        posix = rel_path.as_posix()
    else:
        # Normalise Windows-style separators while keeping POSIX-style
        # paths untouched. ``PurePosixPath`` does not split on ``\``,
        # which would otherwise leave a literal backslash in the ID.
        posix = str(rel_path).replace("\\", "/")
    if not posix:
        return "file:" + _EMPTY_SENTINEL
    pp = PurePosixPath(posix)
    # Strip the final extension only; multi-dot stems (foo.tar.gz) keep
    # their inner dots so we do not lose disambiguation.
    if pp.suffix:
        without_ext = posix[: -len(pp.suffix)]
    else:
        without_ext = posix
    # Apply the canonical-slug rule per path segment so each segment is
    # individually safe (no slashes-collapse-to-dash for the separator
    # itself; just the chars *within* a segment). Empty segments
    # (consecutive slashes, leading slash) are dropped.
    segments = [seg for seg in without_ext.split("/") if seg]
    if not segments:
        return "file:" + _EMPTY_SENTINEL
    cleaned = "/".join(canonical_slug(seg) for seg in segments)
    return f"file:{cleaned}"


def package_id(language: Union[str, None], name: str) -> str:
    """Return the canonical ``package:`` ID for a language package.

    With *language* supplied, the ID is
    ``"package:" + language + ":" + canonical_slug(name)``. Without a
    language, the bare form ``"package:" + canonical_slug(name)`` is
    used. This single rule replaces ``ros_package:<name>``,
    ``package:python:<name>`` (with bespoke per-strategy slug rules),
    ``package:csharp:<name>``, and the variants in between.

    Examples
    --------
    >>> package_id("python", "mypkg")
    'package:python:mypkg'
    >>> package_id("ros2", "rclpy")
    'package:ros2:rclpy'
    >>> package_id("csharp", "MyProject")
    'package:csharp:myproject'
    >>> package_id(None, "third-party")
    'package:third-party'
    >>> package_id("", "third-party")
    'package:third-party'
    """
    slug = canonical_slug(name)
    if language:
        lang_slug = canonical_slug(language)
        return f"package:{lang_slug}:{slug}"
    return f"package:{slug}"


def entity_id(
    node_type: str,
    *,
    platform: Union[str, None],
    name: str,
) -> str:
    """Return the canonical entity ID for a non-file/non-package node.

    With *platform* supplied, the ID is
    ``"{node_type}:{platform}:{canonical_slug(name)}"``. Without a
    platform, the bare ``"{node_type}:{canonical_slug(name)}"`` form is
    used.

    There is **no path-hashed suffix.** The
    ``f"{base}:{sha1(path)[:8]}"`` disambiguator that previously lived
    in ``agent_graph_materialize._node_id_for_values`` is removed by
    construction: collisions are merges via
    :mod:`weld._graph_node_registry`, not separate IDs.

    Examples
    --------
    >>> entity_id("skill", platform="generic", name="architecture-decision")
    'skill:generic:architecture-decision'
    >>> entity_id("agent", platform="claude", name="reviewer")
    'agent:claude:reviewer'
    >>> entity_id("topic", platform=None, name="/cmd_vel")
    'topic:cmd_vel'
    """
    type_slug = canonical_slug(node_type)
    name_slug = canonical_slug(name)
    if platform:
        platform_slug = canonical_slug(platform)
        return f"{type_slug}:{platform_slug}:{name_slug}"
    return f"{type_slug}:{name_slug}"


__all__ = [
    "canonical_slug",
    "file_id",
    "package_id",
    "entity_id",
]
