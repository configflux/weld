"""First-run enrichment policy for ``wd discover`` (ADR 0052).

After ``wd discover`` writes the graph, this module proposes
enrichment through one of three branches:

* **Branch A** -- a configured provider is detected. The user sees a
  cost-honest prompt that names the provider, the node count, and
  the estimated dollar range from :mod:`weld._enrichment_cost`. On
  accept, ``wd enrich`` is invoked. On decline, the answer is
  persisted to ``.weld/.enrichment-prompted`` and the prompt is not
  shown again.
* **Branch B** -- no provider but an agent host is detected (Claude
  Code, Cursor, Codex). The user sees a one-line recommendation to
  run ``/enrich-weld``.
* **Branch C** -- neither. A single-line tip is printed; no prompt.

Suppression chain (top-wins): ``--safe`` (ADR 0024), ``--no-enrich``,
``WELD_NO_ENRICH=1``, then the sentinel file.

The module is import-time credential-free: provider detection only
reads environment markers; the single exception is
:func:`_ollama_reachable`, gated behind the no-other-provider branch.

Edge confidence (ADR 0050) is unchanged here -- this module never
mutates edges. Rendering lives in :mod:`weld._first_run_render`.
"""

from __future__ import annotations

import os
import shutil
import socket
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Final

from weld._enrichment_cost import auto_flow_within_cap
from weld._first_run_render import (
    branch_a_above_cap_message,
    branch_a_prompt,
    branch_b_message,
    branch_c_message,
)
from weld._graph_stats import meaningful_coverage_pct
from weld._notice import emit

# Provider detection ─────────────────────────────────────────────────

# Precedence chain. The order is part of ADR 0052's contract: an
# explicit ``WELD_ENRICH_PROVIDER`` always wins, then API-keyed
# providers in registration order, then local/subscription providers.
# A user with both ``ANTHROPIC_API_KEY`` and ``OPENAI_API_KEY`` set
# gets anthropic; reordering would silently change the default.
_PROVIDER_ENV_CHAIN: Final[tuple[tuple[str, str], ...]] = (
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("OPENAI_API_KEY", "openai"),
)
_OLLAMA_HOST_ENV: Final[str] = "OLLAMA_HOST"
_OLLAMA_DEFAULT_HOST: Final[str] = "127.0.0.1"
_OLLAMA_DEFAULT_PORT: Final[int] = 11434
_OLLAMA_PROBE_TIMEOUT_SEC: Final[float] = 0.05
_COPILOT_BINARY_ENV: Final[str] = "WELD_COPILOT_BINARY"
_COPILOT_BINARY_NAME: Final[str] = "copilot"

# Agent-host detection ───────────────────────────────────────────────

# Agent hosts that wire ``/enrich-weld`` (or an equivalent slash
# command) and so are the right Branch B destination. The value of
# the env var is checked only for "set and non-empty" -- the harness
# owns whatever sentinel it uses.
_AGENT_HOST_ENV_MARKERS: Final[tuple[tuple[str, str], ...]] = (
    ("CLAUDE_CODE_HARNESS", "claude-code"),
    ("CURSOR_AGENT", "cursor"),
    ("CODEX_TASK", "codex"),
    ("AIDER_AGENT", "aider"),
    ("CONTINUE_DEV", "continue"),
)

# Opt-outs and persistence ───────────────────────────────────────────

_NO_ENRICH_ENV: Final[str] = "WELD_NO_ENRICH"
_NO_ENRICH_OFF_VALUES: Final[frozenset[str]] = frozenset(
    {"0", "off", "false", "no", "disabled", ""}
)
_PROMPTED_FILENAME: Final[str] = ".enrichment-prompted"


@dataclass(frozen=True)
class FirstRunDecision:
    """Outcome of one first-run policy evaluation.

    Attributes:
        branch: ``"A"`` (provider), ``"B"`` (agent host), ``"C"``
            (tip), or ``"skip"`` when opt-outs suppressed the flow.
        provider: Detected provider name for Branch A; ``None`` else.
        agent: Detected agent host slug for Branch B; ``None`` else.
        node_count: Total nodes in the graph this flow saw.
        within_cap: ``True`` when the auto-flow's 2k-node cap is met.
        coverage_pct: Meaningful description-coverage percent; drives the
            Branch C tip wording (``0.0`` for the other branches).
        skipped_reason: When ``branch == "skip"``, a short reason
            slug (``"safe"``, ``"flag"``, ``"env"``, ``"prompted"``)
            so callers can log without re-evaluating.
    """

    branch: str
    provider: str | None = None
    agent: str | None = None
    node_count: int = 0
    within_cap: bool = True
    coverage_pct: float = 0.0
    skipped_reason: str | None = None


def _env_no_enrich(env: dict | None = None) -> bool:
    """Return ``True`` when ``WELD_NO_ENRICH`` opts out.

    ``WELD_NO_ENRICH=1`` (or any non-off value) opts out. The
    off-values set mirrors :mod:`weld._auto_refresh` so users can
    switch the suppression off via standard "0/off/false/no"
    conventions and so that ``WELD_NO_ENRICH=`` (set-empty) does
    *not* count as opt-out.
    """
    e = env if env is not None else os.environ
    raw = e.get(_NO_ENRICH_ENV, "")
    return raw.strip().lower() not in _NO_ENRICH_OFF_VALUES


def _ollama_reachable(host: str, port: int) -> bool:
    """Best-effort TCP probe for an ollama server on *host:port*.

    A 50ms connect timeout is short enough that a missing server does
    not delay discover but long enough to detect a local daemon. Any
    error collapses to ``False`` -- the prompt should never lie.
    """
    try:
        with socket.create_connection((host, port), timeout=_OLLAMA_PROBE_TIMEOUT_SEC):
            return True
    except OSError:
        return False


def _parse_ollama_host(raw: str) -> tuple[str, int]:
    """Parse ``OLLAMA_HOST`` into ``(host, port)``.

    Accepts ``hostname[:port]`` and ``[http(s)://]hostname[:port][/path]``;
    malformed input falls back to the documented ollama defaults so a
    user with a typo still gets a sensible probe.
    """
    cleaned = raw.strip()
    if cleaned.startswith("http://"):
        cleaned = cleaned[len("http://") :]
    elif cleaned.startswith("https://"):
        cleaned = cleaned[len("https://") :]
    cleaned = cleaned.split("/", 1)[0]
    if not cleaned:
        return _OLLAMA_DEFAULT_HOST, _OLLAMA_DEFAULT_PORT
    if ":" not in cleaned:
        return cleaned, _OLLAMA_DEFAULT_PORT
    host, _, port_text = cleaned.rpartition(":")
    try:
        return host or _OLLAMA_DEFAULT_HOST, int(port_text)
    except ValueError:
        return _OLLAMA_DEFAULT_HOST, _OLLAMA_DEFAULT_PORT


def detect_provider(
    env: dict | None = None,
    *,
    ollama_probe=_ollama_reachable,
    which=shutil.which,
) -> str | None:
    """Return the highest-priority detected provider, or ``None``.

    Precedence (ADR 0052): ``WELD_ENRICH_PROVIDER`` (lower-cased
    override) -> ``ANTHROPIC_API_KEY`` -> ``OPENAI_API_KEY`` ->
    reachable ollama (``OLLAMA_HOST`` or default localhost) ->
    ``WELD_COPILOT_BINARY`` or ``copilot`` on PATH. *ollama_probe*
    and *which* are injection points for tests.
    """
    e = env if env is not None else os.environ

    override = e.get("WELD_ENRICH_PROVIDER", "").strip().lower()
    if override:
        return override

    for env_key, provider_name in _PROVIDER_ENV_CHAIN:
        if e.get(env_key, "").strip():
            return provider_name

    raw_host = e.get(_OLLAMA_HOST_ENV, "").strip()
    if raw_host:
        host, port = _parse_ollama_host(raw_host)
        if ollama_probe(host, port):
            return "ollama"
    elif ollama_probe(_OLLAMA_DEFAULT_HOST, _OLLAMA_DEFAULT_PORT):
        return "ollama"

    explicit_binary = e.get(_COPILOT_BINARY_ENV, "").strip()
    if explicit_binary or which(_COPILOT_BINARY_NAME):
        return "copilot-cli"

    return None


def detect_agent_host(env: dict | None = None) -> str | None:
    """Return the detected agent-host slug, or ``None``.

    Each marker is "set and non-empty"; harnesses use varied sentinel
    values. The first matching marker in
    :data:`_AGENT_HOST_ENV_MARKERS` wins so a process running inside
    two harnesses at once yields a deterministic pick.
    """
    e = env if env is not None else os.environ
    for env_key, slug in _AGENT_HOST_ENV_MARKERS:
        if e.get(env_key, "").strip():
            return slug
    return None


def _prompted_path(root: Path) -> Path:
    return root / ".weld" / _PROMPTED_FILENAME


def has_been_prompted(root: Path) -> bool:
    """Return ``True`` when the user has already answered the prompt.

    Persistence is a single sentinel file written next to the graph.
    Its content is unstructured today (one line: ``yes`` or ``no``);
    callers must not parse it -- the answer field is reserved for a
    future tip-on-decline feature.
    """
    return _prompted_path(root).is_file()


def mark_prompted(root: Path, *, answer: str) -> None:
    """Write the prompted sentinel with *answer* (``"yes"`` or ``"no"``).

    The .weld directory is created if absent so this helper is safe
    to call after a discover that has just initialised the workspace.
    """
    path = _prompted_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{answer.strip().lower()}\n", encoding="utf-8")


def reset_prompted(root: Path) -> bool:
    """Delete the prompted sentinel. Returns ``True`` if it existed.

    Used by ``wd enrich --reset-prompt`` (ADR 0052) so a user who
    declined enrichment but has since configured a provider can opt
    back into the next discover's prompt without manually editing
    ``.weld/``.
    """
    path = _prompted_path(root)
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def evaluate_first_run(
    *,
    root: Path,
    node_count: int,
    safe: bool,
    no_enrich_flag: bool,
    env: dict | None = None,
    ollama_probe=_ollama_reachable,
    which=shutil.which,
) -> FirstRunDecision:
    """Classify the first-run policy outcome without printing anything.

    Suppression chain (top-wins): ``safe``, ``no_enrich_flag``,
    ``WELD_NO_ENRICH``, then the sentinel file. Otherwise picks
    Branch A (provider), B (agent host), or C (silent tip). The 2k-
    node cap on Branch A is reported via ``within_cap``.

    *env*, *ollama_probe*, and *which* are injection points for tests.
    """
    if safe:
        return FirstRunDecision(branch="skip", node_count=node_count, skipped_reason="safe")
    if no_enrich_flag:
        return FirstRunDecision(branch="skip", node_count=node_count, skipped_reason="flag")
    if _env_no_enrich(env):
        return FirstRunDecision(branch="skip", node_count=node_count, skipped_reason="env")
    if has_been_prompted(root):
        return FirstRunDecision(branch="skip", node_count=node_count, skipped_reason="prompted")

    provider = detect_provider(env, ollama_probe=ollama_probe, which=which)
    if provider is not None:
        within_cap = auto_flow_within_cap(node_count)
        return FirstRunDecision(
            branch="A",
            provider=provider,
            node_count=node_count,
            within_cap=within_cap,
        )

    agent = detect_agent_host(env)
    if agent is not None:
        return FirstRunDecision(branch="B", agent=agent, node_count=node_count)

    return FirstRunDecision(branch="C", node_count=node_count)


def render_decision(decision: FirstRunDecision) -> str:
    """Return the human-readable text for *decision* (no prompt).

    Used by callers that want to log the policy outcome without
    actually blocking on input (CI, ``--json`` consumers). The
    interactive flow uses :func:`run_first_run` instead, which calls
    ``input()`` for Branch A.
    """
    if decision.branch == "A":
        if not decision.within_cap:
            return branch_a_above_cap_message(
                decision.provider or "", decision.node_count
            )
        return branch_a_prompt(decision.provider or "", decision.node_count)
    if decision.branch == "B":
        return branch_b_message(decision.agent or "")
    if decision.branch == "C":
        return branch_c_message(decision.coverage_pct)
    return ""  # skip branches print nothing


def cli_reset_prompt(root: Path) -> int:
    """Implement ``wd enrich --reset-prompt``: clear sentinel, exit 0."""
    msg = "cleared" if reset_prompted(root) else "no sentinel file to clear"
    emit(f"[weld] first-run enrichment prompt: {msg}")
    return 0


def maybe_propose_enrichment(
    root: Path, graph: dict, *, safe: bool, no_enrich_flag: bool,
) -> None:
    """Top-level entry point invoked by ``wd discover`` post-write.

    Zero-node graphs short-circuit. On Branch A accept, runs
    enrichment via :func:`weld._first_run_invoke.run_enrichment_on_accept`
    so the graph is populated immediately (ADR 0052). A broad
    exception handler protects the just-written graph from a faulty
    prompt or provider failure.
    """
    import sys
    node_count = len(graph.get("nodes", {}) or {})
    if node_count == 0:
        return
    try:
        decision = evaluate_first_run(
            root=root, node_count=node_count,
            safe=safe, no_enrich_flag=no_enrich_flag,
        )
        if decision.branch == "C":
            decision = replace(decision, coverage_pct=meaningful_coverage_pct(graph))
        if run_first_run(decision, root=root, stderr=sys.stderr):
            from weld._first_run_invoke import run_enrichment_on_accept
            run_enrichment_on_accept(root, decision.provider)
    except Exception as exc:  # noqa: BLE001 -- discover already succeeded
        emit(f"[weld] first-run enrichment notice failed: {exc}")


def run_first_run(
    decision: FirstRunDecision,
    *,
    root: Path,
    stderr: IO[str],
    input_fn=input,
) -> bool:
    """Render *decision* and, for Branch A within-cap, prompt the user.

    Returns ``True`` when the user opted *in* to running enrichment.
    For Branch A above-cap, Branch B, Branch C, and every skip
    branch, returns ``False`` -- the caller must not invoke
    ``wd enrich``.

    The Branch A prompt accepts ``y`` or ``yes`` (case-insensitive)
    as opt-in; anything else (including ``EOF``) is treated as
    opt-out. Both answers persist to ``.weld/.enrichment-prompted``
    so the prompt is not shown again on the next discover.
    """
    text = render_decision(decision)
    if text:
        stderr.write(text)
    if decision.branch != "A" or not decision.within_cap:
        return False
    try:
        answer = input_fn("")
    except EOFError:
        answer = ""
    normalized = answer.strip().lower()
    opted_in = normalized in {"y", "yes"}
    mark_prompted(root, answer="yes" if opted_in else "no")
    return opted_in
