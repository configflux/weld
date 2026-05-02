"""Regression: module-level constants must surface as file-index tokens.

Background: ``wd find _NAMED_REF_RE`` returned the test file but not the
source module that owned the constant
(``weld/agent_graph_metadata.py``), because
``_extract_python_tokens`` ignored module-level ``ast.Assign`` /
``ast.AnnAssign`` nodes.

This test pins the contract: module-level *constants* by Python
convention (``UPPER_CASE`` and ``_UPPER_CASE``) must surface as tokens,
while lowercase or mixed-case module-level assignments must not (those
are runtime state, not constants).

Bounded resource use is part of the contract:

* The per-file constant cap keeps a malicious or generated source file
  from blowing up the token list.
* The per-name length cap keeps a single absurdly long identifier from
  doing the same.

Both caps live in ``weld.file_index`` and are exercised by the
synthetic-overflow case below.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.file_index import _extract_python_tokens  # noqa: E402


_FIXTURE = '''\
"""Fixture module with a mix of constants and non-constants."""

import re

# Constants by Python convention.
PUBLIC_CONST = 1
_PRIVATE_CONST = 2
_NAMED_REF_RE = re.compile(r"\\bfoo\\b")
ANNOTATED_CONST: int = 7
SCREAMING_SNAKE_CASE = "x"

# Not constants -- runtime state / aliases.
runtime_state = {}
mixed_Case = []
_internal = "x"
camelCase = 0

class Holder:
    # Class-body assignments must NOT be indexed as module constants.
    CLASS_LEVEL = 9


def helper():
    # Function-body assignments must NOT be indexed as module constants.
    FUNCTION_LEVEL = 10
    return FUNCTION_LEVEL
'''


class FileIndexModuleConstantsTest(unittest.TestCase):
    """``_extract_python_tokens`` must surface module-level constants."""

    def test_uppercase_constants_present(self) -> None:
        """``UPPER_CASE`` and ``_UPPER_CASE`` module-level names must
        appear in the extracted token list, regardless of whether the
        right-hand side is a literal, expression, or function call.
        """
        tokens = set(_extract_python_tokens(_FIXTURE))
        for expected in (
            "PUBLIC_CONST",
            "_PRIVATE_CONST",
            "_NAMED_REF_RE",
            "ANNOTATED_CONST",
            "SCREAMING_SNAKE_CASE",
        ):
            self.assertIn(
                expected, tokens,
                f"expected module-level constant {expected!r} to surface "
                f"in file-index tokens; got {sorted(tokens)!r}",
            )

    def test_non_constant_assignments_excluded(self) -> None:
        """Lowercase, mixed-case, and ``_lowercase`` module-level
        assignments are runtime state, not constants. They must not be
        indexed under the constant rule.
        """
        tokens = set(_extract_python_tokens(_FIXTURE))
        for forbidden in ("runtime_state", "mixed_Case", "_internal", "camelCase"):
            self.assertNotIn(
                forbidden, tokens,
                f"non-constant {forbidden!r} leaked into the constant "
                f"surface; tighten the convention regex",
            )

    def test_class_and_function_locals_excluded(self) -> None:
        """Class-body and function-body assignments must not surface as
        module-level constants -- only ``tree.body`` is in scope.
        """
        tokens = set(_extract_python_tokens(_FIXTURE))
        self.assertNotIn("CLASS_LEVEL", tokens)
        self.assertNotIn("FUNCTION_LEVEL", tokens)

    def test_per_file_cap_enforced(self) -> None:
        """A pathological module with thousands of UPPER_CASE assigns
        must not blow up the token list. The constant slice must be
        bounded to the documented per-file cap.
        """
        from weld.file_index import _MAX_PYTHON_CONSTANTS

        lines = [f"CONST_{i} = {i}" for i in range(_MAX_PYTHON_CONSTANTS * 4)]
        source = "\n".join(lines) + "\n"
        tokens = _extract_python_tokens(source)
        const_tokens = [t for t in tokens if t.startswith("CONST_")]
        self.assertLessEqual(
            len(const_tokens), _MAX_PYTHON_CONSTANTS,
            "constant extraction must be bounded by _MAX_PYTHON_CONSTANTS "
            "to keep the file index small",
        )

    def test_per_name_length_cap_enforced(self) -> None:
        """A single absurdly long UPPER_CASE identifier must not be
        emitted; the per-name length cap protects index size.
        """
        from weld.file_index import _MAX_PYTHON_CONSTANT_NAME_LEN

        long_name = "X" * (_MAX_PYTHON_CONSTANT_NAME_LEN + 8)
        source = f"{long_name} = 1\nNORMAL_CONST = 2\n"
        tokens = set(_extract_python_tokens(source))
        self.assertNotIn(
            long_name, tokens,
            "over-length constant names must be skipped",
        )
        self.assertIn("NORMAL_CONST", tokens)

    def test_syntax_error_does_not_raise(self) -> None:
        """Bogus source must not raise; the extractor returns an empty
        list (existing behaviour) and the constant code path must
        respect that contract.
        """
        self.assertEqual(_extract_python_tokens("def !! :"), [])


if __name__ == "__main__":
    unittest.main()
