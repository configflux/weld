"""Terminal leaf-node builders for the bazel strategy (ADR 0109, ADR 0121).

Two node shapes the bazel strategy mints as *side referents* of a BUILD
target's declarations, never as a ``_BUILD_RULES`` rule call itself:
``.bzl`` files a target's declaration reads (:func:`bzl_node`), and
external-workspace dependencies a target's ``deps`` names
(:func:`external_dep_node`). Both are pure functions of their inputs and
carry no rule/role vocabulary of their own -- factored out of
:mod:`weld.strategies.bazel` to keep that module's per-BUILD-file walk
under the 400-line cap, not because either function is reusable outside
this strategy.
"""

from __future__ import annotations


def bzl_node(rel_path: str) -> dict:
    """Build the ``file:`` node for a ``.bzl`` the bazel strategy read.

    ``.bzl`` gets no ``discover.yaml`` source entry of its own (bd rh3l's open
    question). A ``.bzl`` has no meaning apart from the BUILD file that loads
    it -- an unloaded one declares nothing -- and this strategy is already
    holding the load edge when it reads the file, so a second source glob would
    buy a node with no relationships. ``props.file`` means ADR 0101's
    ``graph_files_with_nodes`` counts it as anchored for free; being outside
    every source glob, it never enters ``current_files``, so
    ``files_missing_from_graph`` cannot fire on it.
    """
    stem = rel_path.rpartition("/")[2].rpartition(".")[0]
    return {
        "type": "file",
        "label": stem,
        "props": {
            "file": rel_path,
            "language": "starlark",
            # ``bzl``/``macro``/``manifest`` are the words a reader reaches
            # for, and none of them is in the path or the label. ADR 0105's
            # keywords channel is the generic bag both read paths index.
            "keywords": ["bzl", "starlark", "bazel"],
            "source_strategy": "bazel",
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["build"],
        },
    }


def external_dep_node(repo: str, name: str) -> dict:
    """Build the ``external-dep:`` node for an external-workspace label (ADR 0121).

    *repo* and *name* are the raw label segments from
    :func:`weld.strategies._bazel_labels.resolve_external_dep_label` (the
    node id, minted by that same call, already folds case; the label and
    props below keep the label's own spelling). ``bazel_label`` mirrors the
    prop of the same name on ``build-target``/``test-target`` nodes -- the
    exact ``@repo//name`` text, for tooling that wants the label verbatim
    rather than reparsing the node id.

    No ``roles``: none of :data:`weld.contract.ROLE_VALUES` honestly
    describes "a dependency this repo declares but never analyzes" --
    ``package`` specifically marks a grouping container with real (or
    potential) ``contains`` edges into it, which this node, by design,
    never gets (see ADR 0121). Per the project-wide "omit instead of
    guess" rule, the prop is left off rather than stamped with the closest
    wrong word.
    """
    bazel_label = f"@{repo}//{name}"
    return {
        "type": "external-dep",
        "label": bazel_label,
        "props": {
            "bazel_label": bazel_label,
            "ecosystem": repo,
            "package": name,
            # The ecosystem is the category word (mirrors ``keywords:
            # [rule]`` for build/test targets in ``bazel.py``); the
            # package's own name is already the node's ``label`` and
            # indexed there.
            "keywords": [repo],
            "source_strategy": "bazel",
            "authority": "canonical",
            "confidence": "definite",
        },
    }
