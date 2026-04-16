"""Strategy: Function-level call graph extraction for Python.

Walks every Python module under a glob, records ``symbol`` nodes for each
top-level and nested ``def`` / ``async def`` / ``ClassDef``, and emits a
``calls`` edge for each call site inside a function body.

Resolution is best-effort and explicitly partial -- see ADR
``weld/docs/adr/0004-call-graph-schema-extension.md``:

1. **Same-module name lookup**: ``foo()`` resolves to a sibling
   ``def foo`` defined in the same module.
2. **Import-table lookup**: ``baz()`` resolves to ``symbol:py:foo.bar:baz``
   when the module declares ``from foo.bar import baz``. ``mod.func()``
   resolves to ``symbol:py:foo.bar:func`` when ``import foo.bar as mod``
   (or ``import foo.bar``) is in scope.
3. **Unresolved fallback**: anything else becomes
   ``symbol:unresolved:<name>``. Strategies never silently drop a call.

The strategy uses stdlib ``ast`` only -- no new mandatory dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

#: Sentinel ID prefix for call sites whose target name could not be
#: resolved against the module's imports or local definitions. Kept stable
#: so consumers can filter / rank these distinctly from resolved targets.
UNRESOLVED_PREFIX = "symbol:unresolved:"

def _module_dotted_path(rel_path: str) -> str:
    """Return a python-style dotted module path for *rel_path*.

    ``weld/strategies/python_callgraph.py`` -> ``weld.strategies.python_callgraph``
    ``services/api/app.py`` -> ``services.api.app``
    ``foo/__init__.py`` -> ``foo``
    """
    p = Path(rel_path)
    parts = list(p.parts)
    if not parts:
        return ""
    last = parts[-1]
    if last == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = Path(last).stem
    return ".".join(parts)

def _symbol_id(module_path: str, qualname: str) -> str:
    """Return a stable id for a Python symbol."""
    return f"symbol:py:{module_path}:{qualname}"

def _unresolved_id(name: str) -> str:
    return f"{UNRESOLVED_PREFIX}{name}"

# ---------------------------------------------------------------------------
# Glob resolution (mirrors python_module._resolve_glob to keep strategies
# behaviourally identical at the discovery level)
# ---------------------------------------------------------------------------

def _resolve_glob(root: Path, pattern: str) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    dirs: set[str] = set()
    if "**" in pattern:
        raw = sorted(root.glob(pattern))
        for py in filter_glob_results(root, raw):
            files.append(py)
            dirs.add(str(py.parent.relative_to(root)) + "/")
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return [], []
        name_pat = Path(pattern).name
        raw = sorted(parent.glob(name_pat))
        for py in filter_glob_results(root, raw):
            files.append(py)
        dirs.add(str(parent.relative_to(root)) + "/")
    return files, sorted(dirs)

# ---------------------------------------------------------------------------
# Import-table extraction
# ---------------------------------------------------------------------------

def _build_import_table(tree: ast.Module) -> dict[str, tuple[str, str]]:
    """Return ``{local_name: (module, attr)}`` for every import.

    For ``from foo.bar import baz`` the entry is
    ``"baz": ("foo.bar", "baz")``.
    For ``from foo.bar import baz as qux`` the entry is
    ``"qux": ("foo.bar", "baz")``.
    For ``import foo.bar`` the entry is ``"foo": ("foo.bar", "")`` so
    that ``foo.bar.func()`` can resolve via attribute lookup.
    For ``import foo.bar as mod`` the entry is ``"mod": ("foo.bar", "")``.
    The empty-string ``attr`` slot signals "this is a module alias --
    treat the call's attribute as the symbol name".
    """
    table: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            module = node.module
            for alias in node.names:
                local = alias.asname or alias.name
                table[local] = (module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                table[local] = (alias.name, "")
    return table

# ---------------------------------------------------------------------------
# Per-module symbol + call walker
# ---------------------------------------------------------------------------

class _CallGraphVisitor(ast.NodeVisitor):
    """Collect symbol definitions and call sites within a single module.

    Builds two side-effects on the orchestrator: ``symbols`` (qualname ->
    metadata) and ``calls`` (qualname-of-caller -> list of resolved
    target ids). Nesting is tracked via a qualname stack so methods get
    ``ClassName.method`` and closures get ``outer.inner``.
    """

    def __init__(self, module_path: str, import_table: dict[str, tuple[str, str]]) -> None:
        self.module_path = module_path
        self.import_table = import_table
        # qualname -> {"line": int, "name": str}
        self.symbols: dict[str, dict] = {}
        # caller-qualname -> list of (target_id, resolved: bool)
        self.calls: dict[str, list[tuple[str, bool]]] = {}
        self._qual_stack: list[str] = []

    # -- helpers ---------------------------------------------------------

    def _current_qual(self) -> str:
        return ".".join(self._qual_stack)

    def _record_symbol(self, name: str, lineno: int) -> str:
        self._qual_stack.append(name)
        qual = self._current_qual()
        if qual not in self.symbols:
            self.symbols[qual] = {"name": name, "line": lineno}
        return qual

    def _resolve_call(self, node: ast.Call) -> tuple[str, bool]:
        """Best-effort resolution of a call target to a symbol id.

        Returns ``(target_id, resolved)``. ``resolved`` is True for
        same-module / import-table hits and False for the unresolved
        sentinel form.
        """
        func = node.func
        # Bare name: foo()
        if isinstance(func, ast.Name):
            name = func.id
            # 1. same-module top-level def
            if name in self.symbols:
                return _symbol_id(self.module_path, name), True
            # 2. imported name (from foo.bar import name [as alias])
            if name in self.import_table:
                module, attr = self.import_table[name]
                if attr:
                    return _symbol_id(module, attr), True
                # bare module alias used as a callable -- treat as
                # unresolved (we have no idea what the module's __call__
                # surface is)
                return _unresolved_id(name), False
            return _unresolved_id(name), False

        # Attribute call: a.b() or a.b.c()
        if isinstance(func, ast.Attribute):
            attr = func.attr
            # x.y() where x is an imported module / module alias
            value = func.value
            if isinstance(value, ast.Name) and value.id in self.import_table:
                module, _ = self.import_table[value.id]
                return _symbol_id(module, attr), True
            # self.foo() / cls.foo() / arbitrary chains: not resolved.
            return _unresolved_id(attr), False

        # Subscript / lambda / etc -- nothing useful to record.
        return _unresolved_id("<dynamic>"), False

    # -- visit hooks -----------------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._record_symbol(node.name, node.lineno)
        for child in node.body:
            self.visit(child)
        self._qual_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qual = self._record_symbol(node.name, node.lineno)
        # Walk the body for Call nodes; nested functions / classes are
        # handled by recursive visit_*.
        for child in node.body:
            for sub in ast.walk(child):
                if isinstance(sub, ast.Call):
                    target_id, resolved = self._resolve_call(sub)
                    self.calls.setdefault(qual, []).append((target_id, resolved))
        # Now descend into nested defs/classes (visit them as ordinary
        # children so their qualnames stack on top of this one).
        for child in node.body:
            for sub in ast.iter_child_nodes(child):
                pass
        for child in node.body:
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                self.visit(child)
            else:
                # Recurse into compound statements to find nested defs.
                for sub in ast.walk(child):
                    if isinstance(
                        sub,
                        (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                    ) and sub is not child:
                        # Skip -- we don't track deeply nested closures
                        # inside if/for blocks beyond the top of the body.
                        # The shallow walk above already covered direct
                        # nesting; deeper analysis is out of scope per
                        # ADR 0004.
                        pass
        self._qual_stack.pop()

# ---------------------------------------------------------------------------
# Strategy entry point
# ---------------------------------------------------------------------------

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Walk a glob of Python files and extract symbols + ``calls`` edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)
    excludes = source.get("exclude", [])

    matched, dirs = _resolve_glob(root, pattern)
    discovered_from.extend(dirs)
    if not matched:
        return StrategyResult(nodes, edges, discovered_from)

    for py in matched:
        if should_skip(py, excludes):
            continue
        try:
            source_text = py.read_text(encoding="utf-8")
            tree = ast.parse(source_text, filename=str(py))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        rel_path = str(py.relative_to(root))
        module_path = _module_dotted_path(rel_path)
        if not module_path:
            continue

        import_table = _build_import_table(tree)
        visitor = _CallGraphVisitor(module_path, import_table)
        visitor.visit(tree)

        # Emit one symbol node per defined qualname.
        for qual, meta in visitor.symbols.items():
            sid = _symbol_id(module_path, qual)
            nodes[sid] = {
                "type": "symbol",
                "label": qual,
                "props": {
                    "file": rel_path,
                    "module": module_path,
                    "qualname": qual,
                    "line": meta["line"],
                    "language": "python",
                    "source_strategy": "python_callgraph",
                    "authority": "derived",
                    "confidence": "definite",
                    "roles": ["implementation"],
                },
            }

        # Emit one calls edge per call site (deduplicated within a caller).
        for caller_qual, targets in visitor.calls.items():
            from_id = _symbol_id(module_path, caller_qual)
            seen: set[tuple[str, bool]] = set()
            for target_id, resolved in targets:
                if (target_id, resolved) in seen:
                    continue
                seen.add((target_id, resolved))
                # Materialize unresolved sentinel nodes lazily so the
                # graph stays referentially closed for the orchestrator's
                # final cleanup pass.
                if target_id.startswith(UNRESOLVED_PREFIX):
                    nodes.setdefault(
                        target_id,
                        {
                            "type": "symbol",
                            "label": target_id.split(":", 2)[-1],
                            "props": {
                                "module": "",
                                "qualname": target_id.split(":", 2)[-1],
                                "language": "python",
                                "resolved": False,
                                "source_strategy": "python_callgraph",
                                "authority": "derived",
                                "confidence": "speculative",
                                "roles": ["implementation"],
                            },
                        },
                    )
                edges.append(
                    {
                        "from": from_id,
                        "to": target_id,
                        "type": "calls",
                        "props": {
                            "source_strategy": "python_callgraph",
                            "confidence": "definite" if resolved else "speculative",
                            "resolved": resolved,
                        },
                    }
                )

    return StrategyResult(nodes, edges, discovered_from)
