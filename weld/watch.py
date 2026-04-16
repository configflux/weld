"""Auto-rediscovery on filesystem change.

Runs discovery incrementally whenever watched source files change.
Backends: ``watchdog`` (optional dep, event-driven) with a polling
fallback that compares ``(mtime_ns, size)`` tuples.  Events are
debounced so a single editor save does not trigger multiple discovery
runs.  After each flush the loop calls the discovery callback and
prints a one-line graph diff summary.

CLI:

    wd watch
    wd watch --debounce 2s
    wd watch /path/to/repo
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


DEFAULT_DEBOUNCE: float = 0.5   # seconds before flushing pending changes
POLL_INTERVAL: float = 0.25     # seconds between loop ticks


_DEBOUNCE_PATTERN = re.compile(
    r"""^\s*
        (?P<value>[0-9]+(?:\.[0-9]+)?)
        \s*
        (?P<unit>ms|s|)
        \s*$""",
    re.VERBOSE,
)


def parse_debounce(value: str) -> float:
    """Parse a debounce spec like ``"2s"``, ``"500ms"`` or ``"1.5"`` (seconds).

    Returns the value in seconds as a float.  Raises ``ValueError`` for
    empty input, negative values, or unrecognised formats.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"empty debounce value: {value!r}")

    m = _DEBOUNCE_PATTERN.match(value)
    if not m:
        raise ValueError(f"invalid debounce value: {value!r}")
    raw = float(m.group("value"))
    unit = m.group("unit") or "s"
    seconds = raw / 1000.0 if unit == "ms" else raw
    if seconds < 0:
        raise ValueError(f"debounce must be non-negative: {value!r}")
    return seconds


class _Backend(Protocol):
    """Minimal backend interface: snapshot + poll-until-next-change."""

    def snapshot(self) -> None: ...
    def poll(self) -> set[str]: ...


EnumerateFn = Callable[[Path], Iterable[str]]
"""Callable returning the repo-relative paths to track under *root*."""


class _PollingBackend:
    """Polling-based backend.  Diffs (mtime_ns, size) tuples per poll."""

    def __init__(self, root: Path, enumerate_fn: EnumerateFn) -> None:
        self._root = root
        self._enumerate = enumerate_fn
        self._prev: dict[str, tuple[int, int]] = {}

    def _current(self) -> dict[str, tuple[int, int]]:
        current: dict[str, tuple[int, int]] = {}
        for rel in self._enumerate(self._root):
            abs_path = self._root / rel
            try:
                st = abs_path.stat()
            except (FileNotFoundError, PermissionError, OSError):
                continue
            current[rel] = (st.st_mtime_ns, st.st_size)
        return current

    def snapshot(self) -> None:
        self._prev = self._current()

    def poll(self) -> set[str]:
        current = self._current()
        prev = self._prev
        changed: set[str] = set()
        for rel, stat in current.items():
            if prev.get(rel) != stat:
                changed.add(rel)
        for rel in prev.keys() - current.keys():
            changed.add(rel)
        self._prev = current
        return changed


def _try_import_watchdog() -> Any | None:
    """Return the ``watchdog.observers`` module or ``None`` if unavailable."""
    try:  # pragma: no cover - exercised indirectly via get_backend
        import watchdog.observers  # type: ignore

        return watchdog.observers
    except Exception:
        return None


class _WatchdogBackend:
    """Event-driven backend using the ``watchdog`` package."""

    def __init__(
        self,
        root: Path,
        enumerate_fn: EnumerateFn,
        observers_mod: Any,
    ) -> None:
        # Import events lazily so the module import does not require
        # watchdog to be installed.
        import threading

        import watchdog.events  # type: ignore

        self._root = root
        self._enumerate = enumerate_fn
        self._lock = threading.Lock()
        self._dirty: set[str] = set()

        tracked = {str(Path(p)) for p in self._enumerate(root)}
        self._tracked = tracked

        backend = self

        class _Handler(watchdog.events.FileSystemEventHandler):  # type: ignore
            def on_any_event(self, event: Any) -> None:  # pragma: no cover - needs watchdog
                try:
                    abs_path = Path(event.src_path)
                    rel = str(abs_path.relative_to(root))
                except (ValueError, OSError):
                    return
                if rel in backend._tracked:
                    with backend._lock:
                        backend._dirty.add(rel)

        self._observer = observers_mod.Observer()
        self._observer.schedule(_Handler(), str(root), recursive=True)
        self._observer.start()

    def snapshot(self) -> None:
        # Refresh the tracked set (file listing may have grown) and clear
        # any events observed during enumeration.
        self._tracked = {str(Path(p)) for p in self._enumerate(self._root)}
        with self._lock:
            self._dirty.clear()

    def poll(self) -> set[str]:
        with self._lock:
            out = set(self._dirty)
            self._dirty.clear()
        return out

    def stop(self) -> None:  # pragma: no cover - requires watchdog
        try:
            self._observer.stop()
            self._observer.join(timeout=1.0)
        except Exception:
            pass


def get_backend(
    root: Path,
    enumerate_fn: EnumerateFn,
    *,
    prefer_watchdog: bool = True,
) -> _Backend:
    """Return a watchdog-backed watcher if available, else polling."""
    if prefer_watchdog:
        observers_mod = _try_import_watchdog()
        if observers_mod is not None:  # pragma: no cover - watchdog optional
            try:
                return _WatchdogBackend(root, enumerate_fn, observers_mod)
            except Exception as exc:
                print(
                    f"[weld] watchdog backend failed ({exc}); "
                    f"falling back to polling",
                    file=sys.stderr,
                )
    return _PollingBackend(root, enumerate_fn)


@dataclass
class WatchEngine:
    """Debounces backend events into ``on_change`` callbacks.

    Accumulates paths from ``poll_fn`` into a pending set.  Flushes via
    ``on_change`` when a tick with no new events arrives at least
    ``debounce_seconds`` after the last change.  Injecting *clock* and
    *sleep* keeps the engine deterministic for tests.
    """

    debounce_seconds: float
    poll_fn: Callable[[], set[str]]
    on_change: Callable[[set[str]], None]
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep

    _pending: set[str] = None  # type: ignore[assignment]
    _last_change_at: float | None = None

    def __post_init__(self) -> None:
        self._pending = set()
        self._last_change_at = None

    def tick(self) -> None:
        """Run one iteration of the watch loop."""
        changed = self.poll_fn()
        now = self.clock()

        if changed:
            self._pending |= changed
            self._last_change_at = now
            return

        if (
            self._pending
            and self._last_change_at is not None
            and (now - self._last_change_at) >= self.debounce_seconds
        ):
            flushed = self._pending
            self._pending = set()
            self._last_change_at = None
            try:
                self.on_change(flushed)
            except Exception as exc:  # pragma: no cover - defensive
                print(f"[weld] watch on_change error: {exc}", file=sys.stderr)

    def run_forever(self, poll_interval: float = POLL_INTERVAL) -> None:
        """Drive ``tick()`` in a loop; callers handle KeyboardInterrupt."""
        while True:
            self.tick()
            self.sleep(poll_interval)


def run_once(
    changed: set[str],
    discover_cb: Callable[[set[str]], str],
    *,
    stream=None,
) -> None:
    """Rediscover for *changed* files and print the returned diff summary."""
    target = stream if stream is not None else sys.stdout
    count = len(changed)
    noun = "file" if count == 1 else "files"
    header = f"[weld watch] {count} {noun} changed; rediscovering"
    print(header, file=target)
    try:
        summary = discover_cb(changed)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[weld watch] discovery failed: {exc}", file=target)
        return
    if summary:
        print(summary, file=target)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wd watch",
        description="Watch source files and re-run discovery on change.",
    )
    parser.add_argument("root", nargs="?", default=".", help="Project root (default: .)")
    parser.add_argument(
        "--debounce", default=str(DEFAULT_DEBOUNCE),
        help=f"Debounce window: '2', '2s', '1.5s', '500ms' (default: {DEFAULT_DEBOUNCE}s).",
    )
    parser.add_argument(
        "--no-watchdog", dest="prefer_watchdog", action="store_false", default=True,
        help="Force polling backend even if watchdog is installed.",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=POLL_INTERVAL,
        help=f"Polling tick interval (default: {POLL_INTERVAL}s).",
    )
    args = parser.parse_args(argv)
    try:
        args.debounce_seconds = parse_debounce(args.debounce)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def _default_enumerate(root: Path) -> list[str]:
    """Union of repo-relative paths matched by every configured source."""
    from weld._yaml import parse_yaml
    from weld.discovery_state import resolve_source_files
    from weld.strategies._helpers import filter_glob_results

    config_path = root / ".weld" / "discover.yaml"
    if not config_path.is_file():
        return []
    config = parse_yaml(config_path.read_text(encoding="utf-8")) or {}
    sources = config.get("sources", []) or []
    files: set[str] = set()
    for s in sources:
        files.update(resolve_source_files(root, s, filter_glob_results))
    return sorted(files)


def _default_discover_cb(root: Path) -> Callable[[set[str]], str]:
    """Build the CLI's discovery callback: rediscover + emit diff summary.

    The watch-triggered write path emits ``graph.json`` through the canonical
    serializer so the determinism contract (ADR 0012 section 3) holds across
    watch-driven rewrites just as it does for ``wd discover``. The on-disk
    replacement goes through :func:`weld.workspace_state.atomic_write_text`
    so a crashed watch callback cannot leave a truncated ``graph.json``
    behind (matches the federation root-write guarantee in ADR 0011 §8).
    """
    from weld import diff as diff_mod
    from weld import discover as discover_mod
    from weld.serializer import dumps_graph as _dumps_graph
    from weld.workspace_state import atomic_write_text

    def _cb(_changed: set[str]) -> str:
        graph = discover_mod.discover(root, incremental=True)
        atomic_write_text(root / ".weld" / "graph.json", _dumps_graph(graph))
        diff_result = diff_mod.load_and_diff(root)
        return diff_mod.format_human(diff_result)

    return _cb


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``wd watch``."""
    args = _parse_args(argv)
    root = Path(args.root).resolve()

    if not root.is_dir():
        print(f"[weld watch] root does not exist: {root}", file=sys.stderr)
        return 2

    backend = get_backend(
        root,
        _default_enumerate,
        prefer_watchdog=args.prefer_watchdog,
    )
    backend.snapshot()

    discover_cb = _default_discover_cb(root)

    def _on_change(changed: set[str]) -> None:
        run_once(changed, discover_cb)

    engine = WatchEngine(
        debounce_seconds=args.debounce_seconds,
        poll_fn=backend.poll,
        on_change=_on_change,
    )

    backend_name = type(backend).__name__.lstrip("_")
    print(
        f"[weld watch] watching {root} "
        f"(backend: {backend_name}, debounce: {args.debounce_seconds}s). "
        f"Press Ctrl+C to stop.",
        file=sys.stderr,
    )
    try:
        engine.run_forever(poll_interval=args.poll_interval)
    except KeyboardInterrupt:
        print("\n[weld watch] stopped.", file=sys.stderr)
        return 0
    finally:
        stop = getattr(backend, "stop", None)
        if callable(stop):  # pragma: no cover - only watchdog path
            stop()

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
