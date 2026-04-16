"""Shared helpers for weld strategy modules.

Contains AST utilities, path-exclusion policy, and the StrategyResult type
used by all strategies.
Strategies import from this module; no strategy may import another strategy.
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path
from typing import NamedTuple

from weld import repo_boundary as _repo_boundary

EXCLUDED_DIR_NAMES = _repo_boundary.EXCLUDED_DIR_NAMES
EXCLUDED_NESTED_REPO_SEGMENTS = _repo_boundary.EXCLUDED_NESTED_REPO_SEGMENTS
is_excluded_dir_name = _repo_boundary.is_excluded_dir_name
is_nested_repo_copy = _repo_boundary.is_nested_repo_copy
filter_repo_paths = _repo_boundary.filter_repo_paths

# ---------------------------------------------------------------------------
# Shared exclusion policy — directories that must never appear in Weld discovery
# results or the file index.  This is the single source of truth; both
# ``weld.discover`` (via strategies) and ``weld.file_index`` import from here.
# ---------------------------------------------------------------------------

def filter_glob_results(root: Path, paths: list[Path]) -> list[Path]:
    """Filter out paths that reside inside excluded or nested-repo-copy dirs.

    Strategies that use ``root.glob()`` should pass results through this
    function so that worktree copies (and other excluded trees) are dropped.
    """
    return filter_repo_paths(root, paths)

class StrategyResult(NamedTuple):
    """Return type for every strategy extract() function."""

    nodes: dict[str, dict]
    edges: list[dict]
    discovered_from: list[str]

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def base_names(cls: ast.ClassDef) -> list[str]:
    """Return the simple names of all direct bases of a class."""
    names: list[str] = []
    for b in cls.bases:
        if isinstance(b, ast.Name):
            names.append(b.id)
        elif isinstance(b, ast.Attribute):
            names.append(b.attr)
    return names

def inherits(cls: ast.ClassDef, name: str) -> bool:
    """Check if a class directly inherits from *name*."""
    return name in base_names(cls)

def tablename(cls: ast.ClassDef) -> str | None:
    """Extract __tablename__ string from a class body."""
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__tablename__":
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        return node.value.value
    return None

def extract_fks(cls: ast.ClassDef) -> list[dict]:
    """Find sa.ForeignKey("table.col") calls inside a class body."""
    fks: list[dict] = []
    for node in ast.walk(cls):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_fk = (isinstance(func, ast.Attribute) and func.attr == "ForeignKey") or (
            isinstance(func, ast.Name) and func.id == "ForeignKey"
        )
        if not is_fk or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        ref = first.value
        parts = ref.split(".")
        table = parts[0] if parts else ref
        ondelete = kwarg_str(node, "ondelete")
        fks.append({"ref": ref, "table": table, "ondelete": ondelete})
    return fks

def kwarg_str(call: ast.Call, name: str) -> str | None:
    """Extract a string keyword argument value from a Call node."""
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None

def extract_columns(cls: ast.ClassDef) -> list[str]:
    """Extract column names from Mapped[...] annotations in class body."""
    cols: list[str] = []
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if not name.startswith("_"):
                cols.append(name)
    return cols

def extract_all(tree: ast.Module) -> list[str]:
    """Extract __all__ list from a module."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List):
                        return [
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant)
                            and isinstance(elt.value, str)
                        ]
    return []

def enum_members(cls: ast.ClassDef) -> list[dict]:
    """Extract enum member name=value pairs."""
    members: list[dict] = []
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(
                    node.value, ast.Constant
                ):
                    members.append({"name": target.id, "value": node.value.value})
    return members

def module_name(py_path: Path, domain_root: Path) -> str:
    """Derive module name from file path relative to domain root."""
    return py_path.stem

def should_skip(path: Path, excludes: list[str]) -> bool:
    """Check if path matches any exclude pattern."""
    for pattern in excludes:
        if fnmatch.fnmatch(path.name, pattern):
            return True
    return False

def extract_contracts(tree: ast.Module) -> list[dict]:
    """Extract BaseModel subclasses with their fields."""
    contracts: list[dict] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not inherits(node, "BaseModel"):
            continue
        fields: list[str] = []
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.append(item.target.id)
        docstring = ast.get_docstring(node) or ""
        contracts.append({"name": node.name, "fields": fields, "docstring": docstring})
    return contracts

def extract_router_info(tree: ast.Module) -> dict | None:
    """Find APIRouter(...) assignment and extract prefix + tags."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        is_router = (isinstance(func, ast.Name) and func.id == "APIRouter") or (
            isinstance(func, ast.Attribute) and func.attr == "APIRouter"
        )
        if not is_router:
            continue
        prefix = kwarg_str(node.value, "prefix") or ""
        tags_node = None
        for kw in node.value.keywords:
            if kw.arg == "tags" and isinstance(kw.value, ast.List):
                tags_node = kw.value
        tags = []
        if tags_node:
            tags = [e.value for e in tags_node.elts if isinstance(e, ast.Constant)]
        return {"prefix": prefix, "tags": tags, "var": node.targets[0].id}
    return None

def _annotation_name(anno: ast.expr | None) -> str | None:
    """Return the trailing identifier of a function parameter annotation.

    Handles bare names (``payload: RegisterRequest``), attribute access
    (``payload: schemas.RegisterRequest``), and subscripted generics whose
    base is one of these forms (``payload: list[RegisterRequest]`` →
    ``RegisterRequest``). Anything more dynamic returns ``None``.
    """
    if anno is None:
        return None
    if isinstance(anno, ast.Name):
        return anno.id
    if isinstance(anno, ast.Attribute):
        return anno.attr
    if isinstance(anno, ast.Subscript):
        # Strip generic wrappers like Optional[Model], list[Model], Annotated[...]
        slice_node = anno.slice
        if isinstance(slice_node, ast.Tuple) and slice_node.elts:
            slice_node = slice_node.elts[0]
        inner = _annotation_name(slice_node)
        if inner is not None:
            return inner
        return _annotation_name(anno.value)
    return None

#: Annotation names treated as plain values (not contract references).
_PRIMITIVE_ANNOTATIONS: frozenset[str] = frozenset(
    {
        "int", "str", "float", "bool", "bytes", "None", "Any",
        "dict", "list", "tuple", "set", "frozenset",
        "Dict", "List", "Tuple", "Set", "FrozenSet", "Sequence", "Iterable",
        "Optional", "Union", "Annotated", "Literal",
        # FastAPI/Starlette carrier types that don't describe a body contract.
        "Request", "Response", "Header", "Cookie", "Query", "Path",
        "Form", "File", "UploadFile", "Body", "Depends", "Security",
        "BackgroundTasks", "WebSocket",
    }
)

def _extract_request_body_contracts(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """Return contract-like identifiers for function parameter annotations.

    This is a deliberately conservative signal: we cannot verify from a
    single file that the annotation refers to a ``BaseModel`` subclass, so
    callers must stamp any resulting edge with ``confidence="inferred"``.
    Primitives, framework carrier types, and the obvious ``self``/``cls``
    slots are filtered out.
    """
    found: list[str] = []
    args = fn.args
    params: list[ast.arg] = []
    params.extend(args.posonlyargs)
    params.extend(args.args)
    params.extend(args.kwonlyargs)
    for param in params:
        if param.arg in ("self", "cls"):
            continue
        name = _annotation_name(param.annotation)
        if not name or name in _PRIMITIVE_ANNOTATIONS:
            continue
        # Ignore lowercase identifiers: they are almost always builtins or
        # local aliases rather than Pydantic model class names.
        if not name[:1].isupper():
            continue
        if name not in found:
            found.append(name)
    return found

def _extract_responses_dict_models(call: ast.Call) -> list[str]:
    """Pull ``{status: {"model": Model}}`` or ``{status: Model}`` targets.

    FastAPI's ``responses=`` kwarg declares extra status-code-specific
    response bodies. We collect the model class names referenced from it
    so they can be linked back to the route as additional ``responds_with``
    targets. Non-literal keys and fully dynamic values are skipped.
    """
    models: list[str] = []
    for kw in call.keywords:
        if kw.arg != "responses" or not isinstance(kw.value, ast.Dict):
            continue
        for value in kw.value.values:
            name: str | None = None
            if isinstance(value, ast.Dict):
                for subkey, subval in zip(value.keys, value.values):
                    if (
                        isinstance(subkey, ast.Constant)
                        and subkey.value == "model"
                    ):
                        name = _annotation_name(subval)
                        break
            else:
                name = _annotation_name(value)
            if name and name not in models:
                models.append(name)
    return models

def extract_routes(tree: ast.Module, router_var: str) -> list[dict]:
    """Extract ``@router.get/post/...`` decorated functions.

    Each returned dict carries:

    - ``method``: upper-cased HTTP verb.
    - ``path``: router-local literal path (empty when not a string literal).
    - ``function``: handler function name.
    - ``response_model``: primary response contract name, or ``None``.
      Handles both bare-name and attribute-style targets
      (``MyModel`` and ``schemas.MyModel``).
    - ``response_models``: extra contract names pulled from the decorator's
      ``responses={...}`` dict, in declaration order.
    - ``request_body_models``: conservative list of contract-like parameter
      annotation names; consumers must treat these as inferred, not
      definite, since BaseModel inheritance cannot be proven from one file.
    """
    routes: list[dict] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not isinstance(func, ast.Attribute):
                continue
            if not (isinstance(func.value, ast.Name) and func.value.id == router_var):
                continue
            method = func.attr
            if method not in ("get", "post", "put", "delete", "patch"):
                continue
            path = ""
            if dec.args and isinstance(dec.args[0], ast.Constant):
                path = dec.args[0].value
            response_model: str | None = None
            for kw in dec.keywords:
                if kw.arg == "response_model":
                    response_model = _annotation_name(kw.value)
                    break
            response_models = _extract_responses_dict_models(dec)
            request_body_models = _extract_request_body_contracts(node)
            routes.append(
                {
                    "method": method.upper(),
                    "path": path,
                    "function": node.name,
                    "response_model": response_model,
                    "response_models": response_models,
                    "request_body_models": request_body_models,
                }
            )
    return routes
