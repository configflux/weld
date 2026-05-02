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


def dump_yaml(data: object) -> str:
    """Minimal block-style YAML emitter.

    Produces output readable by ``parse_yaml`` and by full YAML loaders
    (e.g. ``pyyaml.safe_load``). Handles dicts, lists, strings, ints,
    floats, bools, and ``None`` -- the same value space ``parse_yaml``
    consumes. Lists of pure scalars are emitted in flow style;
    everything else uses block style.
    """
    out: list[str] = []
    _emit(data, out, 0)
    return "\n".join(out) + "\n"


def _emit(data: object, out: list[str], depth: int) -> None:
    pad = "  " * depth
    if isinstance(data, dict):
        if not data:
            out.append(f"{pad}{{}}")
            return
        for key, value in data.items():
            _emit_kv(str(key), value, out, depth)
    elif isinstance(data, list):
        if not data:
            out.append(f"{pad}[]")
            return
        for item in data:
            _emit_item(item, out, depth)
    else:
        out.append(f"{pad}{_emit_scalar(data)}")


def _emit_kv(key: str, value: object, out: list[str], depth: int) -> None:
    pad = "  " * depth
    if isinstance(value, dict):
        if not value:
            out.append(f"{pad}{key}: {{}}")
            return
        out.append(f"{pad}{key}:")
        for k2, v2 in value.items():
            _emit_kv(str(k2), v2, out, depth + 1)
    elif isinstance(value, list):
        if not value:
            out.append(f"{pad}{key}: []")
            return
        if all(not isinstance(it, (dict, list)) for it in value):
            inline = ", ".join(_emit_scalar(it) for it in value)
            out.append(f"{pad}{key}: [{inline}]")
            return
        out.append(f"{pad}{key}:")
        for item in value:
            _emit_item(item, out, depth + 1)
    else:
        out.append(f"{pad}{key}: {_emit_scalar(value)}")


def _emit_item(item: object, out: list[str], depth: int) -> None:
    pad = "  " * depth
    if isinstance(item, dict):
        if not item:
            out.append(f"{pad}- {{}}")
            return
        keys = list(item.keys())
        first_k = str(keys[0])
        first_v = item[first_k]
        if isinstance(first_v, dict) and first_v:
            out.append(f"{pad}- {first_k}:")
            for k2, v2 in first_v.items():
                _emit_kv(str(k2), v2, out, depth + 2)
        elif isinstance(first_v, list) and first_v:
            if all(not isinstance(it, (dict, list)) for it in first_v):
                inline = ", ".join(_emit_scalar(it) for it in first_v)
                out.append(f"{pad}- {first_k}: [{inline}]")
            else:
                out.append(f"{pad}- {first_k}:")
                for it in first_v:
                    _emit_item(it, out, depth + 2)
        else:
            out.append(f"{pad}- {first_k}: {_emit_scalar(first_v)}")
        for k in keys[1:]:
            _emit_kv(str(k), item[k], out, depth + 1)
    elif isinstance(item, list):
        out.append(f"{pad}-")
        for it in item:
            _emit_item(it, out, depth + 1)
    else:
        out.append(f"{pad}- {_emit_scalar(item)}")


def _emit_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    needs_quote = (
        s == ""
        or s in ("null", "true", "false", "yes", "no", "True", "False", "None")
        or s[0] in " \t-?:,[]{}#&*!|>'\"%@`"
        or s[-1] in " \t"
        or ":" in s
        or "\n" in s
        or "\t" in s
    )
    if needs_quote:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s
