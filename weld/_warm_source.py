"""Pluggable artifact sources for ``wd warm`` (ADR 0067).

``wd warm`` fetches a CI-published ``graph.json`` for the nearest-ancestor
commit and incrementally refreshes it to local HEAD. The transport is
decoupled behind a tiny protocol so the warm path can be verified end-to-end
without live CI (a local directory stands in for the artifact store) while the
same path also fetches over HTTPS in production.

Two sources ship:

* :class:`LocalDirSource` -- ``file://PATH`` or a bare directory. Reads
  ``<dir>/<sha>/graph.json`` and the sibling ``graph.json.sha256``. Used by the
  test suite and local QA, and genuinely useful for a shared mount or a
  ``gh run download`` target.
* :class:`HttpsSource` -- ``https://…`` with a ``{sha}`` placeholder. GETs the
  graph and its ``.sha256`` sibling.

Security (ADR 0067 §4): the only artifact-influenced input to URL/path
construction is the commit SHA, which is validated against ``^[0-9a-f]{40}$``
(it originates from local ``git rev-list``, never the network). HTTPS templates
must be ``https://`` (``file://`` is allowed for tests) and must not carry
credential material (``user:pass@``). Sources return raw bytes plus the
published hash; verification happens in :mod:`weld.warm` before any bytes are
allowed to become the local graph. Artifact content is data, never executed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from urllib.request import urlopen

__all__ = [
    "ArtifactSource",
    "LocalDirSource",
    "HttpsSource",
    "is_valid_sha",
    "build_artifact_url",
    "source_from_spec",
    "SHA_RE",
    "SHA_PLACEHOLDER",
    "GRAPH_BASENAME",
    "SHA256_SUFFIX",
    "HTTP_TIMEOUT_SECONDS",
    "MAX_ARTIFACT_BYTES",
]

# A full git object name: exactly 40 lowercase hex chars. Abbreviated or
# uppercase SHAs are rejected so nothing ambiguous or attacker-shaped ever
# reaches path/URL construction.
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Placeholder an HTTPS template must contain so we substitute a validated SHA
# rather than join attacker-controlled path components.
SHA_PLACEHOLDER = "{sha}"

# Canonical artifact basenames (ADR 0067 §1): the graph and its integrity tag.
GRAPH_BASENAME = "graph.json"
SHA256_SUFFIX = ".sha256"

# Network read timeout; warm degrades to full discover on any failure, so a
# tight bound keeps a slow/hung mirror from stalling the command.
HTTP_TIMEOUT_SECONDS = 15

# Hard ceiling on a fetched artifact. The repo graph is a few MB; this guards
# against a pathological/hostile response exhausting memory before the hash
# check can reject it. Generous (256 MiB) but finite.
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024


def is_valid_sha(sha: str) -> bool:
    """Return True iff *sha* is a full 40-char lowercase-hex git object name."""
    return isinstance(sha, str) and bool(SHA_RE.match(sha))


@runtime_checkable
class ArtifactSource(Protocol):
    """Fetch a published graph artifact for a commit SHA.

    Returns ``(graph_bytes, expected_sha256_or_None)`` for a hit, or ``None``
    for a miss. Implementations must **not** raise on a routine miss or a
    transport error: warm treats any failure as "no hit" and degrades to a
    full local discover, so a source that raised would defeat the guaranteed
    fallback. The returned ``expected_sha256`` is the published integrity tag
    (``None`` only when the source genuinely has no tag); :mod:`weld.warm`
    verifies it before landing the bytes.
    """

    def fetch(self, sha: str) -> tuple[bytes, str | None] | None: ...


def _parse_sha256_text(text: str | None) -> str | None:
    """Extract a 64-hex digest from a ``.sha256`` payload, else ``None``.

    Accepts both a bare digest and the ``<digest>  <filename>`` form emitted by
    ``sha256sum``. Returns ``None`` for missing/garbage input so a malformed
    tag becomes "no tag" (which warm then treats as an unverifiable, refused
    artifact) rather than crashing.
    """
    if not text:
        return None
    token = text.strip().split()[0] if text.strip() else ""
    token = token.lower()
    if re.match(r"^[0-9a-f]{64}$", token):
        return token
    return None


class LocalDirSource:
    """Artifact source backed by a local directory (``file://`` or a path).

    Layout: ``<root>/<sha>/graph.json`` and ``<root>/<sha>/graph.json.sha256``.
    A missing graph file is a miss; a missing/garbage ``.sha256`` yields a hit
    with ``expected=None`` (warm then refuses the unverifiable artifact).
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def fetch(self, sha: str) -> tuple[bytes, str | None] | None:
        if not is_valid_sha(sha):
            return None
        graph_path = self._root / sha / GRAPH_BASENAME
        try:
            data = graph_path.read_bytes()
        except OSError:
            return None
        if len(data) > MAX_ARTIFACT_BYTES:
            return None
        sha_path = self._root / sha / (GRAPH_BASENAME + SHA256_SUFFIX)
        try:
            expected = _parse_sha256_text(sha_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            expected = None
        return data, expected


def build_artifact_url(template: str, sha: str, *, suffix: str = "") -> str:
    """Build a fetch URL by substituting a **validated** SHA into *template*.

    Security (ADR 0067 §4): *sha* must pass :func:`is_valid_sha` and *template*
    must contain :data:`SHA_PLACEHOLDER`, be ``https://`` (or ``file://`` for
    tests), and carry no credentials (``user:pass@``). The SHA is the only
    artifact-influenced component and it is constrained to hex, so the artifact
    store cannot redirect the fetch elsewhere. *suffix* (e.g. ``.sha256``) is a
    fixed, caller-supplied tail, never user input.

    Raises :class:`ValueError` for any violation -- the caller treats that as a
    misconfiguration and degrades to full discover.
    """
    if not is_valid_sha(sha):
        raise ValueError(f"refusing to build URL for non-SHA value: {sha!r}")
    if SHA_PLACEHOLDER not in template:
        raise ValueError(
            f"artifact URL template must contain {SHA_PLACEHOLDER!r}: {template!r}"
        )
    parts = urlsplit(template)
    if parts.scheme not in ("https", "file"):
        raise ValueError(
            f"artifact URL must be https:// (or file:// for tests): {template!r}"
        )
    if parts.username is not None or parts.password is not None:
        raise ValueError("artifact URL must not embed credentials")
    return template.replace(SHA_PLACEHOLDER, sha) + suffix


class HttpsSource:
    """Artifact source backed by an HTTPS (or ``file://``) URL template.

    The template must contain ``{sha}``; the graph is fetched from the
    substituted URL and its hash from the sibling ``…graph.json.sha256`` URL.
    ``file://`` templates are accepted so the same code path is exercised in
    tests without a network.
    """

    def __init__(self, url_template: str) -> None:
        self._template = url_template

    @property
    def url_template(self) -> str:
        return self._template

    def _get(self, url: str) -> bytes | None:
        # Scheme is constrained to https/file by build_artifact_url; nothing
        # downstream can broaden it. Bounded read; any failure -> None (miss).
        try:
            with urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310
                return resp.read(MAX_ARTIFACT_BYTES + 1)
        except Exception:
            return None

    def fetch(self, sha: str) -> tuple[bytes, str | None] | None:
        try:
            graph_url = build_artifact_url(self._template, sha)
            sha_url = build_artifact_url(self._template, sha, suffix=SHA256_SUFFIX)
        except ValueError:
            return None
        data = self._get(graph_url)
        if data is None or len(data) > MAX_ARTIFACT_BYTES:
            return None
        raw = self._get(sha_url)
        try:
            expected = _parse_sha256_text(raw.decode("utf-8")) if raw else None
        except UnicodeDecodeError:
            expected = None
        return data, expected


def source_from_spec(spec: str | None) -> ArtifactSource | None:
    """Resolve a ``--source`` / ``WELD_WARM_SOURCE`` value to a source.

    * ``None`` / empty -> ``None`` (no source: warm goes straight to discover).
    * ``https://…`` containing ``{sha}`` -> :class:`HttpsSource`.
    * ``file://…`` containing ``{sha}`` -> :class:`HttpsSource` (template form).
    * ``file://DIR`` without ``{sha}`` -> :class:`LocalDirSource` rooted at DIR.
    * any other string -> :class:`LocalDirSource` rooted at that path.

    Returns ``None`` for an unusable spec so the caller degrades gracefully.
    """
    if not spec:
        return None
    parts = urlsplit(spec)
    if parts.scheme == "https":
        return HttpsSource(spec) if SHA_PLACEHOLDER in spec else None
    if parts.scheme == "file":
        if SHA_PLACEHOLDER in spec:
            return HttpsSource(spec)
        # file://DIR -> a local directory artifact store.
        local = parts.path or ""
        return LocalDirSource(local) if local else None
    if SHA_PLACEHOLDER in spec:
        # A bare template with a placeholder but no scheme is ambiguous;
        # refuse rather than guess a transport.
        return None
    return LocalDirSource(spec)
