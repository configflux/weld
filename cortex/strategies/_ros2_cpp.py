"""Low-level C++ scanning helpers for the ``ros2_topology`` strategy.

This module is intentionally private (leading underscore) and only
used by ``cortex/strategies/ros2_topology.py``.  It exists so the strategy
module itself stays under the repo-wide 400-line budget.  Tests import
``ros2_topology`` directly; they do not import from here.

The helpers cover:

- comment stripping (``//`` and ``/* */``)
- top-level argument splitting that respects nesting and string literals
- matching ``)``/``}`` finders that treat character and string literals
  as opaque
- string-literal extraction for the first positional argument
- ROS2 interface resolution from a templated type like
  ``std_msgs::msg::String``
- namespace-stack reconstruction for a given source offset
- class-header detection for ``rclcpp::Node`` subclasses
- runtime-name extraction from a ``: Base("name")`` super-ctor call
- base-class classification against the YAML-driven node-base table
- config loading for ``cortex/languages/cpp_ros2.yaml``

 and the module docstring of ``ros2_topology.py``
for the full recognition contract.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Comment stripping
# ---------------------------------------------------------------------------

_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

def strip_comments(src: str) -> str:
    """Remove ``/* ... */`` and ``// ...`` comments from C++ source."""
    src = _BLOCK_COMMENT_RE.sub("", src)
    src = _LINE_COMMENT_RE.sub("", src)
    return src

# ---------------------------------------------------------------------------
# Top-level argument splitter
# ---------------------------------------------------------------------------

def split_top_level_args(arg_block: str) -> list[str]:
    """Split a parenthesised argument block on top-level commas.

    Respects nesting for ``(``, ``[``, ``{``, and ``<`` and treats
    string / char literals as opaque.  Returns trimmed argument strings
    with any trailing empty token dropped.
    """
    args: list[str] = []
    depth = 0
    in_str: str | None = None
    buf: list[str] = []
    i = 0
    while i < len(arg_block):
        ch = arg_block[i]
        if in_str:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(arg_block):
                buf.append(arg_block[i + 1])
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = ch
            buf.append(ch)
            i += 1
            continue
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            if depth > 0:
                depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        args.append(tail)
    return args

def extract_string_literal(arg: str) -> str | None:
    """Return the inner string of ``"..."`` if *arg* is a single literal."""
    s = arg.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return None

# ---------------------------------------------------------------------------
# Matching brace / paren finders (literal-aware)
# ---------------------------------------------------------------------------

def _find_matching(src: str, open_idx: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    i = open_idx
    in_str: str | None = None
    while i < len(src):
        ch = src[i]
        if in_str:
            if ch == "\\" and i + 1 < len(src):
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = ch
            i += 1
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def find_matching_brace(src: str, open_idx: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at *open_idx*.

    Returns ``len(src)`` if no match is found so callers can still slice
    a (possibly truncated) body without special-casing.
    """
    result = _find_matching(src, open_idx, "{", "}")
    return result if result >= 0 else len(src)

def find_matching_paren(src: str, open_idx: int) -> int:
    """Return the index of the ``)`` matching the ``(`` at *open_idx*."""
    return _find_matching(src, open_idx, "(", ")")

# ---------------------------------------------------------------------------
# Interface resolution
# ---------------------------------------------------------------------------

def resolve_interface(templated: str) -> tuple[str, str | None]:
    """Resolve a templated type like ``std_msgs::msg::String``.

    Returns ``(message_type, interface_nid)``.  If *templated* does not
    match the ROS2 ``<pkg>::msg|srv|action::<Name>`` convention the
    message type is returned as ``"<unresolved>"`` and the interface id
    is ``None``.
    """
    if not templated:
        return "<unresolved>", None
    parts = [p.strip() for p in templated.split("::") if p.strip()]
    if len(parts) < 3:
        return "<unresolved>", None
    kind = parts[-2]
    if kind not in {"msg", "srv", "action"}:
        return "<unresolved>", None
    pkg = parts[-3]
    name = parts[-1]
    # Strip trailing template parameters / pointer suffixes from the name.
    name = re.sub(r"[^A-Za-z0-9_].*$", "", name)
    display = f"{pkg}/{kind}/{name}"
    return display, f"ros_interface:{display}"

# ---------------------------------------------------------------------------
# Namespace reconstruction
# ---------------------------------------------------------------------------

_NAMESPACE_RE = re.compile(r"\bnamespace\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{")

def enclosing_namespace(src: str, upto: int) -> str:
    """Return the ``a::b::`` prefix for the namespace stack at *upto*.

    Counts namespace blocks whose opening ``{`` precedes *upto* and
    whose matching ``}`` comes after it.  Robust to malformed sources
    because ``find_matching_brace`` returns ``len(src)`` on failure.
    """
    stack: list[str] = []
    for m in _NAMESPACE_RE.finditer(src):
        if m.start() >= upto:
            break
        ns_name = m.group(1)
        brace_open = src.find("{", m.end() - 1)
        if brace_open < 0:
            continue
        brace_close = find_matching_brace(src, brace_open)
        if brace_open < upto < brace_close:
            stack.append(ns_name)
    if not stack:
        return ""
    return "::".join(stack) + "::"

# ---------------------------------------------------------------------------
# Class header detection
# ---------------------------------------------------------------------------

CLASS_RE = re.compile(
    r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:final\s+)?"
    r":\s*public\s+([A-Za-z_][A-Za-z0-9_:<>, \t]*?)\s*[{]",
)

_SUPER_NAME_RE = re.compile(
    r":\s*(?:[A-Za-z_][A-Za-z0-9_:]*\s*,\s*)*"
    r"(?:[A-Za-z_][A-Za-z0-9_:]*)\s*\(\s*\"([^\"]+)\""
)

def extract_runtime_name(body: str) -> str | None:
    """Find a ``: Base("runtime_name")`` super-constructor call."""
    for m in _SUPER_NAME_RE.finditer(body):
        return m.group(1)
    return None

def base_is_ros_node(
    bases_str: str, node_bases: list,
) -> tuple[bool, bool]:
    """Return ``(is_node, lifecycle)`` for a ``: public Base, ...`` list.

    *node_bases* is the ``node_bases`` list loaded from
    ``cortex/languages/cpp_ros2.yaml``; each entry is a ``{match, lifecycle}``
    mapping.  Matching is exact on the full qualified name or on the
    unqualified tail so both ``rclcpp::Node`` and bare ``Node`` forms
    match.
    """
    bases = [b.strip() for b in re.split(r"[,\s]+", bases_str) if b.strip()]
    for base in bases:
        core = re.sub(r"<.*", "", base).strip()
        for entry in node_bases:
            if not isinstance(entry, dict):
                continue
            match = entry.get("match", "")
            if not match:
                continue
            tail = match.split("::")[-1]
            if core == match or core.endswith("::" + match) or core == tail:
                return True, bool(entry.get("lifecycle", False))
    return False, False

# ---------------------------------------------------------------------------
# Call-site prefix detection
# ---------------------------------------------------------------------------

def qualifier_namespace(src: str, call_start: int) -> str | None:
    """Return the ``ns::`` qualifier that precedes a callee at *call_start*.

    ``rclcpp_action::create_client(...)`` → ``"rclcpp_action"``.
    Returns ``None`` when the callee is not namespace-qualified.
    """
    if call_start < 2 or src[call_start - 2:call_start] != "::":
        return None
    j = call_start - 2
    while j > 0 and (src[j - 1].isalnum() or src[j - 1] == "_"):
        j -= 1
    ns_name = src[j:call_start - 2]
    return ns_name or None

# Matches ``<callee>[<templated>](`` at a call-site; used by the scanner.
CALL_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:<([^>;{}]*)>)?\s*"
    r"\(",
)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_cpp_ros2_config() -> dict:
    """Load ``cortex/languages/cpp_ros2.yaml``; empty dict on any failure."""
    from cortex._yaml import parse_yaml
    path = (
        Path(__file__).resolve().parent.parent
        / "languages" / "cpp_ros2.yaml"
    )
    if not path.is_file():
        return {}
    try:
        data = parse_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
