"""Local telemetry writer for ``wd`` and the weld MCP server.

ADR 0035 (sections 1, 3, 5, 6) defines the on-disk artifact, the
polyrepo-aware path resolution, the three-tier opt-out, the
failure-isolated writer, and the 1 MiB rotation policy. Public surface:
:class:`Recorder`, :func:`is_enabled`, :func:`resolve_path`, plus the
schema constants. The writer never raises -- a bug here must not crash
the host command, change its exit code, or mutate output.
"""

from __future__ import annotations

import enum
import json
import os
import sys
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Final, IO, Iterable

from weld._telemetry_allowlist import coerce_command, coerce_flags
from weld._telemetry_redact import validate_event


# --- Constants -------------------------------------------------------------

TELEMETRY_SCHEMA_VERSION: Final[int] = 1
TELEMETRY_FILENAME: Final[str] = "telemetry.jsonl"
MAX_FILE_BYTES: Final[int] = 1_048_576
MAX_EVENTS_KEPT_AFTER_ROTATE: Final[int] = 500

_SENTINEL_FILENAME: Final[str] = "telemetry.disabled"
_ENV_VAR: Final[str] = "WELD_TELEMETRY"
_ENV_OFF_VALUES: Final[frozenset[str]] = frozenset(
    {"off", "0", "false", "no", "disabled"}
)
_ENV_ON_VALUES: Final[frozenset[str]] = frozenset(
    {"on", "1", "true", "yes", "enabled"}
)

_FIRST_RUN_NOTICE: Final[str] = (
    "weld: local telemetry on (recording success/failure of wd commands "
    "to .weld/telemetry.jsonl). Disable with WELD_TELEMETRY=off, "
    "--no-telemetry, or `wd telemetry disable`. See `wd telemetry --help`.\n"
)


class OptOutSource(enum.Enum):
    """Which tier in the opt-out chain decided ``is_enabled``."""

    CLI_FLAG = "cli-flag"
    ENV_VAR = "env-var"
    CONFIG_FILE = "config-file"
    DEFAULT_ON = "default-on"


# --- Path resolution -------------------------------------------------------


def _walk_up(start: Path) -> Iterable[Path]:
    cur = start.resolve()
    yield cur
    yield from cur.parents


def _find_workspace_root(start: Path) -> Path | None:
    for parent in _walk_up(start):
        if (parent / ".weld" / "workspaces.yaml").is_file():
            return parent
    return None


def _find_single_repo_root(start: Path) -> Path | None:
    for parent in _walk_up(start):
        weld = parent / ".weld"
        if not weld.is_dir():
            continue
        if (weld / "discover.yaml").is_file() or (weld / "graph.json").is_file():
            return parent
    return None


def _xdg_state_path() -> Path | None:
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        base = Path(state_home)
    else:
        try:
            base = Path.home() / ".local" / "state"
        except (RuntimeError, OSError):
            return None
    return base / "weld" / TELEMETRY_FILENAME


def resolve_path(start: Path) -> Path | None:
    """Resolve the telemetry file path per ADR 0035 § "Local-only JSONL writer".

    Order: polyrepo workspace root, single-repo root, XDG state fallback.
    Returns ``None`` only if every tier (including XDG) fails to resolve.
    """
    ws = _find_workspace_root(start)
    if ws is not None:
        return ws / ".weld" / TELEMETRY_FILENAME
    repo = _find_single_repo_root(start)
    if repo is not None:
        return repo / ".weld" / TELEMETRY_FILENAME
    return _xdg_state_path()


# --- Opt-out ---------------------------------------------------------------


def is_enabled(
    *,
    cli_flag: bool | None,
    root: Path | None,
) -> tuple[bool, OptOutSource]:
    """Return ``(enabled, source)`` per ADR 0035 § "Default-on with three-tier opt-out".

    Resolution order, top wins: ``cli_flag`` (True forces on / False forces off),
    ``WELD_TELEMETRY`` env var, ``.weld/telemetry.disabled`` sentinel, default on.
    """
    if cli_flag is True:
        return True, OptOutSource.CLI_FLAG
    if cli_flag is False:
        return False, OptOutSource.CLI_FLAG

    raw = os.environ.get(_ENV_VAR)
    if raw is not None:
        v = raw.strip().lower()
        if v in _ENV_OFF_VALUES:
            return False, OptOutSource.ENV_VAR
        if v in _ENV_ON_VALUES:
            return True, OptOutSource.ENV_VAR

    if root is not None:
        sentinel = root / ".weld" / _SENTINEL_FILENAME
        try:
            if sentinel.is_file():
                return False, OptOutSource.CONFIG_FILE
        except OSError:
            pass

    return True, OptOutSource.DEFAULT_ON


# --- Writer ----------------------------------------------------------------


def _write_locked(path: Path, event: dict) -> None:
    """Append one JSON line under :func:`fcntl.flock` (best-effort on failure)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n"
    ).encode("ascii")

    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        _try_lock_then_write(fd, payload)
    finally:
        os.close(fd)
    _rotate_if_needed(path)


def _try_lock_then_write(fd: int, payload: bytes) -> None:
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:
        os.write(fd, payload)
        return

    deadline = time.monotonic_ns() + 50 * 1_000_000  # 50 ms.
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic_ns() >= deadline:
                # Best-effort fallback: short writes are append-atomic on POSIX.
                os.write(fd, payload)
                return
            time.sleep(0.001)
    try:
        os.write(fd, payload)
    finally:
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)


def _rotate_if_needed(path: Path) -> None:
    """Trim ``path`` to the trailing ``MAX_EVENTS_KEPT_AFTER_ROTATE`` lines.

    No-op when the file is below ``MAX_FILE_BYTES``. Uses
    :func:`weld.workspace_state.atomic_write_text` for crash-safe rewrite,
    falling back to a tmp+replace if the helper is unavailable.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < MAX_FILE_BYTES:
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    text = "".join(lines[-MAX_EVENTS_KEPT_AFTER_ROTATE:])
    try:
        from weld.workspace_state import atomic_write_text

        atomic_write_text(path, text)
        return
    except Exception:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        with suppress(OSError):
            tmp.unlink()


def _print_first_run_notice(stream: IO[str], path: Path) -> None:
    """Emit the one-line opt-out notice when ``path`` does not yet exist.

    Idempotency is anchored on file non-existence at call time. The caller
    invokes this before the first write so subsequent runs stay silent.
    """
    try:
        if path.exists():
            return
    except OSError:
        return
    try:
        stream.write(_FIRST_RUN_NOTICE)
        stream.flush()
    except (OSError, ValueError):
        pass


# --- Recorder --------------------------------------------------------------


def _weld_version_string() -> str:
    try:
        from importlib.metadata import version

        return version("configflux-weld")
    except Exception:
        try:
            vf = Path(__file__).resolve().parent.parent / "VERSION"
            if vf.is_file():
                return vf.read_text(encoding="utf-8").strip() or "0.0.0"
        except OSError:
            pass
        return "0.0.0"


def _python_version_string() -> str:
    info = sys.version_info
    return f"{info.major}.{info.minor}.{info.micro}"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Recorder:
    """Context manager that records one telemetry event per CLI / MCP call.

    The recorder swallows every writer error -- the host command sees
    only its own behavior. ``set_exit_code`` lets the caller override
    the default exit code derived from the exit-time exception type.
    """

    __slots__ = (
        "_surface",
        "_command",
        "_flags",
        "_root",
        "_cli_flag",
        "_clock",
        "_stderr",
        "_t_start_ns",
        "_exit_code_override",
        "outcome",
        "exit_code",
    )

    def __init__(
        self,
        *,
        surface: str,
        command: str,
        flags: Iterable[str],
        root: Path | None,
        cli_flag: bool | None = None,
        clock=time.monotonic_ns,
        stderr: IO[str] | None = None,
    ) -> None:
        self._surface = surface
        self._command = command
        self._flags = list(flags or ())
        self._root = root
        self._cli_flag = cli_flag
        self._clock = clock
        self._stderr = stderr if stderr is not None else sys.stderr
        self._t_start_ns: int = 0
        self._exit_code_override: int | None = None
        self.outcome: str = "ok"
        self.exit_code: int = 0

    def set_exit_code(self, code: int) -> None:
        """Override the default exit code (``0`` ok, ``1`` error)."""
        if isinstance(code, int) and not isinstance(code, bool):
            self._exit_code_override = code

    def __enter__(self) -> "Recorder":
        try:
            self._t_start_ns = int(self._clock())
        except Exception:
            self._t_start_ns = 0
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        outcome, default_rc, error_kind = _classify_outcome(exc_type)
        self.outcome = outcome
        override = self._exit_code_override
        self.exit_code = override if override is not None else default_rc
        try:
            self._record(error_kind)
        except BaseException:  # noqa: BLE001 -- ADR 0035 failure isolation.
            pass
        return False

    def _record(self, error_kind: str | None) -> None:
        enabled, _src = is_enabled(cli_flag=self._cli_flag, root=self._root)
        if not enabled:
            return
        start = self._root if self._root is not None else Path.cwd()
        path = resolve_path(start)
        if path is None:
            return
        event = self._build_event(error_kind)
        validated = validate_event(event)
        if validated is None:
            return
        _print_first_run_notice(self._stderr, path)
        _write_locked(path, validated)

    def _build_event(self, error_kind: str | None) -> dict:
        try:
            now_ns = int(self._clock())
        except Exception:
            now_ns = self._t_start_ns
        duration_ms = max(0, now_ns - self._t_start_ns) // 1_000_000
        return {
            "schema_version": TELEMETRY_SCHEMA_VERSION,
            "ts": _utc_now_iso(),
            "weld_version": _weld_version_string(),
            "surface": self._surface,
            "command": coerce_command(self._surface, self._command),
            "outcome": self.outcome,
            "exit_code": int(self.exit_code),
            "duration_ms": int(duration_ms),
            "error_kind": error_kind,
            "python_version": _python_version_string(),
            "platform": str(sys.platform) if sys.platform else "unknown",
            "flags": coerce_flags(self._flags),
        }


def _classify_outcome(
    exc_type: type[BaseException] | None,
) -> tuple[str, int, str | None]:
    """Map an exit-time exception type to (outcome, exit_code, error_kind).

    ``KeyboardInterrupt`` and ``BrokenPipeError`` map to ``"interrupted"``
    with exit codes 130 and 141 respectively (POSIX convention). Any
    other exception is ``"error"`` with exit code 1. ``None`` is ``"ok"``
    with exit code 0.
    """
    if exc_type is None:
        return "ok", 0, None
    if issubclass(exc_type, KeyboardInterrupt):
        return "interrupted", 130, "KeyboardInterrupt"
    if issubclass(exc_type, BrokenPipeError):
        return "interrupted", 141, "BrokenPipeError"
    return "error", 1, exc_type.__name__
