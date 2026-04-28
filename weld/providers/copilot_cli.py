"""GitHub Copilot CLI-backed semantic enrichment provider."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

from weld.providers import build_prompt, parse_json_result

_INSTALL_HINT = (
    "copilot-cli enrichment requires the GitHub Copilot CLI binary on PATH. "
    "Install it from https://docs.github.com/en/copilot/how-tos/use-copilot-cli "
    "or set WELD_COPILOT_BINARY to its absolute path."
)
_DEFAULT_BINARY = "copilot"
_BINARY_ENV = "WELD_COPILOT_BINARY"
_DEFAULT_TIMEOUT_SEC = 120


class CopilotCliProvider:
    """Provider that shells out to the standalone GitHub Copilot CLI.

    Auth lives in the binary itself (GitHub login). Model selection is
    server-side: the ``model`` argument is accepted to satisfy the
    :class:`EnrichmentProvider` Protocol and recorded in provenance, but
    is not forwarded to the binary in this revision.
    """

    DEFAULT_MODEL = "default"

    def __init__(
        self,
        runner: Callable | None = None,
        binary: str | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self._runner = runner if runner is not None else subprocess.run
        self._binary = binary or os.getenv(_BINARY_ENV) or _DEFAULT_BINARY
        self._timeout_sec = (
            timeout_sec if timeout_sec is not None else _DEFAULT_TIMEOUT_SEC
        )

    def enrich(self, node: dict, neighbors: list[dict], *, model: str):
        prompt = build_prompt(node, neighbors)
        try:
            result = self._runner(
                [self._binary, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self._timeout_sec,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(_INSTALL_HINT) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"copilot CLI timed out after {self._timeout_sec}s while enriching "
                f"node {node.get('id')!r}"
            ) from exc

        returncode = int(getattr(result, "returncode", 0) or 0)
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""

        if returncode != 0:
            tail = stderr.strip()[-512:] or stdout.strip()[-512:]
            raise RuntimeError(
                f"copilot CLI exited with status {returncode}: {tail}"
            )

        return parse_json_result(_extract_json(stdout))


def _extract_json(stdout: str) -> str:
    """Return the JSON object slice from *stdout*.

    The copilot CLI may surround the JSON payload with status lines or
    markdown fences. ``parse_json_result`` already strips fenced code
    blocks; this helper additionally trims any leading or trailing
    free-form text by locating the outermost ``{...}`` slice.
    """
    cleaned = stdout.strip()
    if not cleaned:
        return cleaned
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return cleaned
    return cleaned[start : end + 1]
