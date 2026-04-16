"""Minimal YAML subset parser for discover.yaml.

Handles the structures used in discover.yaml: mappings, sequences of
mappings, scalar values, and inline lists.  Does NOT handle the full
YAML spec -- just enough for our config file.

Extracted from weld/discover.py so both the orchestrator and
strategy modules can use it without a pyyaml dependency.
"""

from __future__ import annotations


def _collect_flow_list(val: str, lines: list[str], start: int) -> tuple[str, int]:
    """If *val* starts with ``[`` but the closing ``]`` is on a later line,
    join continuation lines and return ``(joined_value, next_line_index)``.
    If the list is already closed (single-line) or *val* does not start with
    ``[``, return ``(val, start)`` unchanged.
    """
    if not val.startswith("[") or val.endswith("]"):
        return val, start
    # Count open brackets to handle nested lists (unlikely but safe).
    depth = val.count("[") - val.count("]")
    parts = [val]
    i = start
    while depth > 0 and i < len(lines):
        chunk = lines[i].rstrip()
        # Skip blanks / comments inside the continuation.
        if not chunk or chunk.lstrip().startswith("#"):
            i += 1
            continue
        parts.append(chunk.strip())
        depth += chunk.count("[") - chunk.count("]")
        i += 1
    return " ".join(parts), i


def _split_flow_items(inner: str) -> list[str]:
    """Split comma-separated flow YAML items, respecting quotes and nesting."""
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    for char in inner:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char in "[{(":
            depth += 1
        elif char in "]})" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue
        current.append(char)
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items


def _split_flow_pair(item: str) -> tuple[str, str] | None:
    """Split a ``key: value`` flow-map item outside quotes and nesting."""
    quote: str | None = None
    depth = 0
    for idx, char in enumerate(item):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in "[{(":
            depth += 1
            continue
        if char in "]})" and depth > 0:
            depth -= 1
            continue
        if char == ":" and depth == 0:
            return item[:idx].strip(), item[idx + 1 :].strip()
    return None


def parse_yaml(text: str) -> dict | list:
    """Parse a minimal YAML document into Python dicts/lists."""
    lines = text.split("\n")
    return _parse_block(lines, 0, 0)[0]

def _parse_block(lines: list[str], start: int, indent: int) -> tuple[dict | list, int]:
    """Parse a YAML block at *indent* level starting from line *start*."""
    i = start
    while i < len(lines):
        stripped = lines[i].rstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        line_indent = len(lines[i]) - len(lines[i].lstrip())
        if line_indent < indent:
            break
        if stripped.lstrip().startswith("- "):
            return _parse_sequence(lines, i, indent)
        else:
            return _parse_mapping(lines, i, indent)
    return {}, i

def _parse_mapping(lines: list[str], start: int, indent: int) -> tuple[dict, int]:
    result: dict = {}
    i = start
    while i < len(lines):
        stripped = lines[i].rstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        line_indent = len(lines[i]) - len(lines[i].lstrip())
        if line_indent < indent:
            break
        if line_indent > indent:
            i += 1
            continue
        content = stripped.lstrip()
        if content.startswith("- "):
            break
        if ":" not in content:
            i += 1
            continue
        key, _, val = content.partition(":")
        key = key.strip().strip('"').strip("'")
        val = val.strip()
        if val and not val.startswith("#"):
            val, i = _collect_flow_list(val, lines, i + 1)
            result[key] = _parse_scalar(val)
        else:
            child_indent = indent + 2
            j = i + 1
            while j < len(lines):
                s = lines[j].rstrip()
                if s and not s.lstrip().startswith("#"):
                    child_indent = len(lines[j]) - len(lines[j].lstrip())
                    break
                j += 1
            child, i = _parse_block(lines, j, child_indent)
            result[key] = child
    return result, i

def _parse_sequence(lines: list[str], start: int, indent: int) -> tuple[list, int]:
    result: list = []
    i = start
    while i < len(lines):
        stripped = lines[i].rstrip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        line_indent = len(lines[i]) - len(lines[i].lstrip())
        if line_indent < indent:
            break
        content = stripped.lstrip()
        if not content.startswith("- "):
            if line_indent == indent:
                break
            i += 1
            continue
        item_val = content[2:].strip()
        if ":" in item_val and not item_val.startswith(('"', "{")):
            item: dict = {}
            k, _, v = item_val.partition(":")
            k = k.strip().strip('"').strip("'")
            v = v.strip()
            if v:
                v, i = _collect_flow_list(v, lines, i + 1)
                item[k] = _parse_scalar(v)
            else:
                i += 1
            _child_indent = line_indent + 2
            if i < len(lines):
                ns = lines[i].rstrip()
                if ns and not ns.lstrip().startswith("#"):
                    ci = len(lines[i]) - len(lines[i].lstrip())
                    if ci > line_indent:
                        _child_indent = ci  # noqa: F841
            while i < len(lines):
                s2 = lines[i].rstrip()
                if not s2 or s2.lstrip().startswith("#"):
                    i += 1
                    continue
                li2 = len(lines[i]) - len(lines[i].lstrip())
                if li2 <= line_indent:
                    break
                c2 = s2.lstrip()
                if c2.startswith("- ") and li2 == line_indent:
                    break
                if ":" in c2:
                    k2, _, v2 = c2.partition(":")
                    k2 = k2.strip().strip('"').strip("'")
                    v2 = v2.strip()
                    if v2:
                        v2, i = _collect_flow_list(v2, lines, i + 1)
                        item[k2] = _parse_scalar(v2)
                    else:
                        j = i + 1
                        nci = li2 + 2
                        while j < len(lines):
                            ns2 = lines[j].rstrip()
                            if ns2 and not ns2.lstrip().startswith("#"):
                                nci = len(lines[j]) - len(lines[j].lstrip())
                                break
                            j += 1
                        child, i = _parse_block(lines, j, nci)
                        item[k2] = child
                else:
                    i += 1
            result.append(item)
        else:
            result.append(_parse_scalar(item_val))
            i += 1
    return result, i

def _parse_scalar(val: str) -> str | int | float | bool | list:
    """Parse a YAML scalar value."""
    if not val:
        return ""
    if " #" in val:
        val = val[:val.index(" #")].rstrip()
    if val.startswith("{") and val.endswith("}"):
        inner = val[1:-1]
        if not inner.strip():
            return {}
        result: dict[str, object] = {}
        for item in _split_flow_items(inner):
            pair = _split_flow_pair(item)
            if pair is None:
                continue
            key, value = pair
            result[key.strip('"').strip("'")] = _parse_scalar(value)
        return result
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1]
        if not inner.strip():
            return []
        return [_parse_scalar(x) for x in _split_flow_items(inner)]
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        return val[1:-1]
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val
