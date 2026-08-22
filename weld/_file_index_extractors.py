"""Per-file token extraction for the ``wd find`` index.

Carved out of :mod:`weld.file_index`, which sat at the 400-line cap so the
next change to it -- a one-line symlink guard on the admission predicate
(bd a2gr) -- breached it. The split is along the seam the module already
had: :mod:`weld.file_index` decides *which* files are surface and owns the
index artifact, and this module turns one file's text into tokens. It reads
nothing and knows no paths beyond the one it is tokenizing, which is why it
is the half that moves.

Every name here is re-exported from :mod:`weld.file_index`, so existing
``from weld.file_index import _extract_python_tokens`` call sites and the
``_tokenize_path`` release-claim pragma keep resolving. Same carve pattern
as :mod:`weld.file_index_search` (the read-side matcher) and
``weld/runtime_srcs.bzl``.

The bounds on what a single file may contribute -- ``_MAX_GENERIC_TOKENS``,
``_MAX_PYTHON_CONSTANTS``, ``_MAX_PYTHON_CONSTANT_NAME_LEN`` -- move with
the code they bound. They are DoS guards on repositories weld did not
write, and both regexes are linear and anchored so they stay ReDoS-free
regardless of input.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_GENERIC_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:-]{1,80}")
_MAX_GENERIC_TOKENS = 512

# Module-level Python constants (UPPER_CASE or _UPPER_CASE) are part of the
# practical "what does this module own" surface that ``wd find`` and
# ``wd query`` must illuminate. Module-level constants are bounded in two
# axes to keep the index small and DoS-safe on pathological inputs:
#
#   * ``_MAX_PYTHON_CONSTANTS`` -- per-file cap on how many constant names
#     enter the token list.
#   * ``_MAX_PYTHON_CONSTANT_NAME_LEN`` -- per-name length cap; an absurdly
#     long identifier is dropped rather than embedded.
#
# The convention regex is intentionally linear (no nested quantifiers) and
# anchored to the full identifier, so it is ReDoS-free regardless of input.
_PY_CONSTANT_NAME_RE = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
_MAX_PYTHON_CONSTANTS = 64
_MAX_PYTHON_CONSTANT_NAME_LEN = 80

# The *field-name* surface: names that identify a field rather than define a
# callable. ``_extract_python_tokens`` harvested only the definition surface
# (classes, public functions, imports, ``__all__``, module constants), so a
# schema field was invisible to ``wd find`` in Python and *only* in Python --
# ``wd find created_at`` returned three files, none of them ``.py``, while the
# term occurs in nine Python files including ``weld/discovery_state.py``, which
# declares it (bd 2peg). Every ``.sh`` hit came from the generic tokenizer,
# which is exactly the recall the rich extractor was silently losing.
#
# Three forms, because that is how a field name appears in Python and the
# measured misses split across all three: a class-level attribute
# (``created_at: str = ""``), a keyword argument (``created_at=...``) and an
# identifier-shaped string literal (``{"created_at": ...}``,
# ``assertIn("created_at", ...)``) -- the last being the majority.
#
# Bounded on the same two axes as the constants above, and additionally
# required to be identifier-shaped so prose strings, paths, format specifiers
# and ``"utf-8"``-style values stay out. That shape check is what makes this a
# field harvest rather than full-text indexing of every Python string.
_MAX_PYTHON_FIELD_NAMES = 256
_MAX_PYTHON_FIELD_NAME_LEN = 80
#: Minimum length, mirroring ``_GENERIC_TOKEN_RE``'s ``{1,80}`` tail (which
#: makes two characters its floor). Keeps ``"r"`` / ``"w"`` mode strings and
#: single-letter variables out of the index.
_MIN_PYTHON_FIELD_NAME_LEN = 2

def _tokenize_path(rel_path: str) -> list[str]:
    """Tokenize *rel_path* into path segments, filename stem, and (when
    the filename has an extension) the raw basename so a literal-with-dot
    ``wd find`` query like ``install.sh`` matches the file by name."""
    parts = Path(rel_path).parts
    tokens = [Path(part).stem if part == parts[-1] else part for part in parts]
    if parts and parts[-1] != tokens[-1]:
        tokens.append(parts[-1])
    return tokens

def _is_python_constant_name(name: str) -> bool:
    """Return True if *name* matches the Python constant convention.

    Constants are top-level identifiers whose names are ``UPPER_CASE`` or
    a leading-underscore variant (``_UPPER_CASE``). The convention regex
    is anchored and linear (no nested quantifiers) so it remains
    ReDoS-free regardless of input.
    """
    if not name or len(name) > _MAX_PYTHON_CONSTANT_NAME_LEN:
        return False
    return _PY_CONSTANT_NAME_RE.match(name) is not None


def _module_constant_names(tree: ast.Module) -> list[str]:
    """Return the module-level constants declared in *tree*.

    Walks ``tree.body`` only -- class- and function-body assignments are
    intentionally ignored, even if they look like constants. Both
    ``ast.Assign`` (``X = 1``) and ``ast.AnnAssign`` (``X: int = 1``)
    targets are supported. Output is bounded by ``_MAX_PYTHON_CONSTANTS``
    so a generated module cannot blow up the index. ``__all__`` is
    explicitly skipped because it is already harvested as a list of
    string exports.
    """
    found: list[str] = []
    seen: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        for target in targets:
            name = target.id
            if name == "__all__":
                continue
            if name in seen:
                continue
            if not _is_python_constant_name(name):
                continue
            seen.add(name)
            found.append(name)
            if len(found) >= _MAX_PYTHON_CONSTANTS:
                return found
    return found


def _is_field_name(name: str) -> bool:
    """Return True when *name* is admissible as a field-surface token.

    Identifier shape is the whole gate: ``str.isidentifier`` accepts exactly
    the strings that can name a Python attribute, keyword or dict-key-used-as-
    a-field, and rejects the prose, paths, URLs, format strings and
    ``"utf-8"``-style values that share the ``ast.Constant`` node type with
    them. Keywords (``class``, ``return``) are left in rather than filtered:
    a string literal ``"class"`` is a real dict key in HTML/JSON-shaped code.
    """
    if not name or len(name) > _MAX_PYTHON_FIELD_NAME_LEN:
        return False
    if len(name) < _MIN_PYTHON_FIELD_NAME_LEN:
        return False
    return name.isidentifier()


def _class_attribute_names(tree: ast.Module) -> list[str]:
    """Return names assigned at class level anywhere in *tree*.

    Both ``ast.AnnAssign`` (``created_at: str = ""`` -- the dataclass field
    shape) and ``ast.Assign`` (``created_at = ""``) targets are read, from the
    direct body of every ``ast.ClassDef``. Body-only, matching
    :func:`_module_constant_names`: a name bound inside a method is a local,
    not a field, and indexing locals is how this becomes full-text search.
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign):
                targets = [stmt.target] if isinstance(stmt.target, ast.Name) else []
            elif isinstance(stmt, ast.Assign):
                targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
            else:
                continue
            found.extend(t.id for t in targets)
    return found


def _field_surface_names(tree: ast.Module) -> list[str]:
    """Return the bounded field-name surface of *tree*.

    Order is class attributes, then keyword arguments, then identifier-shaped
    string literals -- deduplicated, first-seen wins, and truncated at
    :data:`_MAX_PYTHON_FIELD_NAMES`. The ordering is deliberate rather than
    incidental: it is a declaration-before-use preference, so when a
    pathological file hits the cap the names it *defines* survive and the
    names it merely mentions are what get dropped.

    ``ast.walk`` is breadth-first over a fixed tree, so the result is
    deterministic for given source -- the property
    ``weld/tests/weld_file_index_determinism_test.py`` pins for this index.
    """
    keywords: list[str] = []
    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg:
            keywords.append(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.append(node.value)

    found: list[str] = []
    seen: set[str] = set()
    for name in (*_class_attribute_names(tree), *keywords, *literals):
        if name in seen or not _is_field_name(name):
            continue
        seen.add(name)
        found.append(name)
        if len(found) >= _MAX_PYTHON_FIELD_NAMES:
            break
    return found


def _extract_python_tokens(content: str) -> list[str]:
    """Extract class names, function names, import targets, and
    module-level constants from Python."""
    tokens: list[str] = []
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return tokens

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            tokens.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                tokens.append(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                tokens.append(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tokens.append(node.module.split(".")[-1])
            for alias in node.names:
                tokens.append(alias.name)

    # Also extract __all__ exports
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                tokens.append(elt.value)

    # Module-level constants by Python convention -- the residual surface
    # missed by class/function/import/__all__ extraction. Bounded for
    # safety; see ``_module_constant_names`` for caps and rationale.
    tokens.extend(_module_constant_names(tree))

    # The field-name surface -- the residual this extractor lost to Python's
    # own richness, in the same sense the constants above were. See the caps
    # block for the measurement (bd 2peg).
    tokens.extend(_field_surface_names(tree))

    return tokens

def _extract_markdown_tokens(content: str) -> list[str]:
    """Extract headings from markdown content."""
    tokens: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # Remove leading #s and whitespace
            heading = stripped.lstrip("#").strip()
            if heading:
                # Add the full heading and individual words
                for word in heading.split():
                    # Strip common punctuation
                    clean = word.strip("*`_()[]{}:,;.!?\"'")
                    if clean and len(clean) > 1:
                        tokens.append(clean)
    return tokens

def _extract_yaml_tokens(content: str) -> list[str]:
    """Extract top-level keys from YAML content."""
    tokens: list[str] = []
    for line in content.splitlines():
        # Match top-level keys (no leading whitespace)
        if line and not line[0].isspace() and ":" in line:
            key = line.split(":")[0].strip()
            if key and not key.startswith("#"):
                tokens.append(key)
    return tokens

def _extract_typescript_tokens(content: str) -> list[str]:
    """Extract exported symbol names and import targets from TypeScript."""
    tokens: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        # export function/const/class/interface/type
        m = re.match(
            r"export\s+(?:default\s+)?(?:async\s+)?(?:function|const|let|var|class|interface|type|enum)\s+(\w+)",
            stripped,
        )
        if m:
            tokens.append(m.group(1))
            continue
        # import ... from "module"
        m = re.match(r"import\s+.*from\s+['\"]([^'\"]+)['\"]", stripped)
        if m:
            mod = m.group(1).split("/")[-1]
            tokens.append(mod)
    return tokens

def _extract_generic_tokens(content: str) -> list[str]:
    """Extract a bounded, deterministic token set from general text files."""
    tokens = {
        match.group(0).strip("_:-")
        for match in _GENERIC_TOKEN_RE.finditer(content)
    }
    return [token for token in sorted(tokens) if token][:_MAX_GENERIC_TOKENS]
