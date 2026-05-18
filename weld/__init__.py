# weld — repo-local connected structure toolkit

# Hermetic-test hook: when WELD_HERMETIC_BLOCK_TREE_SITTER=1 is set in the
# environment, install a meta-path finder that raises ImportError for
# ``tree_sitter`` and any ``tree_sitter_*`` grammar package. This lets a
# test harness run ``bazel test //...`` against an env where tree-sitter
# is missing, mirroring GitHub Actions runners that don't ship the
# optional dependency. Tests that genuinely require tree-sitter must
# gate themselves with
# ``@unittest.skipUnless(_is_tree_sitter_available(), ...)`` — the
# hermetic gate fails any test that depends on tree-sitter without this
# protection.
import os as _os

if _os.environ.get("WELD_HERMETIC_BLOCK_TREE_SITTER") == "1":
    import importlib.abc as _importlib_abc
    import sys as _sys

    class _BlockTreeSitter(_importlib_abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):  # noqa: ARG002
            if (
                name == "tree_sitter"
                or name.startswith("tree_sitter.")
                or name.startswith("tree_sitter_")
            ):
                raise ImportError(
                    "tree-sitter blocked by WELD_HERMETIC_BLOCK_TREE_SITTER: "
                    + name
                )
            return None

    _sys.meta_path.insert(0, _BlockTreeSitter())
    for _name in [
        _n for _n in list(_sys.modules)
        if _n == "tree_sitter"
        or _n.startswith("tree_sitter.")
        or _n.startswith("tree_sitter_")
    ]:
        del _sys.modules[_name]
