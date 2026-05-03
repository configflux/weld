"""Strategy-coverage rule: flag discover.yaml entries with zero matches.

Lives in its own module so ``weld.arch_lint`` stays under the 400-line
cap; the rule is self-contained and only needs the project root to
resolve globs against the file system.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from weld.arch_lint import Violation


def rule_strategy_coverage(data: dict, root: Path) -> Iterable["Violation"]:
    """Flag source entries in discover.yaml whose globs match zero files."""
    from weld.arch_lint import Violation  # late import breaks cycle
    from weld._yaml import parse_yaml

    config_path = root / ".weld" / "discover.yaml"
    if not config_path.is_file():
        return

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return
    config = parse_yaml(text)
    sources = config.get("sources", []) if isinstance(config, dict) else []

    unmatched: list[tuple[str, str]] = []  # (pattern, strategy)

    for source in sources:
        if not isinstance(source, dict):
            continue
        strategy = source.get("strategy", "<unknown>")

        glob_pattern = source.get("glob")
        if glob_pattern:
            if "**" in str(glob_pattern):
                matched = list(root.glob(str(glob_pattern)))
            else:
                parent = (root / str(glob_pattern)).parent
                if parent.is_dir():
                    matched = list(
                        parent.glob(Path(str(glob_pattern)).name)
                    )
                else:
                    matched = []
            if not matched:
                unmatched.append((str(glob_pattern), str(strategy)))
            continue

        file_list = source.get("files", [])
        if file_list:
            missing = [
                f for f in file_list
                if not (root / str(f)).is_file()
            ]
            if len(missing) == len(file_list):
                pattern = f"files:{file_list}"
                unmatched.append((pattern, str(strategy)))

    for pattern, strategy in sorted(unmatched, key=lambda t: t[0]):
        yield Violation(
            rule="strategy-coverage",
            node_id=pattern,
            message=(
                f"source entry {pattern!r} (strategy: {strategy}) "
                f"matched zero files; stale or misconfigured"
            ),
            severity="warning",
        )
