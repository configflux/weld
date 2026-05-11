"""Corpus manifest loader for ``wd bench --public`` (ADR 0059).

Split out of :mod:`weld.bench._public_runner` so the runner stays focused
on retrieval and scoring while this module owns YAML parsing and schema
validation. Both modules share the same dataclasses, defined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from weld._yaml import parse_yaml


# Allowed family tags. The set is closed: a typo in the manifest must
# fail loudly (per ADR 0059, we do not want silent drops in coverage).
_ALLOWED_FAMILIES = frozenset(
    {"navigation", "dependency", "callgraph", "impact", "cross_repo"}
)


@dataclass(frozen=True)
class CorpusSource:
    """How to acquire a repo (``git`` for production, ``local`` for fixtures).

    The ``placeholder`` flag is the explicit opt-in for "this SHA is a
    stand-in until the real one is pinned" -- the runner skips such
    repos without attempting a clone, and the report renders SKIPPED
    rows for their tasks. This complements
    :func:`weld.bench._public_setup.is_placeholder_sha` which detects
    only the obvious junk patterns.
    """

    kind: str
    url: str = ""
    sha: str = ""
    path: str = ""
    placeholder: bool = False


@dataclass(frozen=True)
class SetupStep:
    """Post-clone preparation step for a repo (e.g. cmake configure).

    Used by language variants that need an extra artefact on disk before
    a competing adapter can run. The canonical case is the libclang C++
    variant: ``cmake -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON`` produces
    ``build/compile_commands.json`` which the libclang strategy then
    consumes.

    Attributes:
        requires_binary: A binary name (passed to ``shutil.which``) that
            gates the step. When the binary is absent the step is
            recorded as ``setup_unavailable`` rather than as a failure
            -- the corresponding adapter then surfaces SKIPPED in the
            report.
        cmd: Argv vector executed in the cloned repo's root. Must not
            be a shell string; subprocess.run is invoked without
            ``shell=True`` so injection cannot land arbitrary code.
        produces: Repo-relative file that must exist after the command
            succeeds. When the file is missing the step is recorded
            as ``setup_failed``.
        timeout_s: Bounded wall-clock timeout for the subprocess.
    """

    requires_binary: str
    cmd: tuple[str, ...]
    produces: str
    timeout_s: int = 120


@dataclass(frozen=True)
class PublicTask:
    """One scored task in the public benchmark.

    Attributes:
        repo_id:        Owning repository's id (mirrors the manifest entry).
        id:             Stable per-repo task id.
        family:         One of ``navigation``, ``dependency``, ``callgraph``,
                        ``impact``, ``cross_repo``.
        prompt:         Natural-language question.
        term:           Search token used by both grep + weld surfaces.
        symbol:         Bare symbol for ``callgraph`` tasks (None otherwise).
        answer_files:   Repo-relative paths considered the ground-truth hits.
    """

    repo_id: str
    id: str
    family: str
    prompt: str
    term: str
    symbol: str | None
    answer_files: tuple[str, ...]


@dataclass(frozen=True)
class PublicRepo:
    """One repository in the corpus.

    ``setup`` is optional: when present, the materializer runs the step
    after a successful clone (gated on the declared binary) and records
    a per-repo setup status so adapters can surface SKIPPED rather than
    crash when the artefact never appears.
    """

    id: str
    language: str
    source: CorpusSource
    tasks: tuple[PublicTask, ...]
    setup: SetupStep | None = None


@dataclass(frozen=True)
class PublicCorpus:
    """Loaded manifest."""

    schema_version: int
    corpus_id: str
    description: str
    repos: tuple[PublicRepo, ...]


def load_public_corpus(path: Path) -> PublicCorpus:
    """Parse + validate a public-corpus YAML manifest.

    Raises :class:`ValueError` if the manifest is missing required fields,
    declares unknown task families, or violates the production SHA invariant
    (when ``source.kind`` is ``git``, ``sha`` must be a 40-character hex
    string; for ``local`` sources we only require ``path``).
    """
    data = parse_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            f"{path}: top-level YAML must be a mapping, got "
            f"{type(data).__name__}"
        )

    schema_version = int(data.get("schema_version") or 0)
    if schema_version != 1:
        raise ValueError(
            f"{path}: unsupported schema_version {schema_version!r} "
            f"(supported: 1)"
        )

    corpus_id = str(data.get("corpus_id") or "")
    if not corpus_id:
        raise ValueError(f"{path}: corpus_id is required")
    description = str(data.get("description") or "")
    repos_raw = data.get("repos")
    if not isinstance(repos_raw, list) or not repos_raw:
        raise ValueError(f"{path}: repos must be a non-empty list")

    repos: list[PublicRepo] = []
    for repo_idx, repo in enumerate(repos_raw):
        if not isinstance(repo, dict):
            raise ValueError(
                f"{path}: repos[{repo_idx}] must be a mapping"
            )
        repo_id = str(repo.get("id") or "")
        if not repo_id:
            raise ValueError(
                f"{path}: repos[{repo_idx}].id is required"
            )
        language = str(repo.get("language") or "")
        source = _parse_source(path, repo_idx, repo.get("source"))
        tasks_raw = repo.get("tasks") or []
        if not isinstance(tasks_raw, list):
            raise ValueError(
                f"{path}: repos[{repo_idx}].tasks must be a list"
            )
        tasks = _parse_tasks(path, repo_id, tasks_raw)
        setup = _parse_setup(path, repo_id, repo.get("setup"))
        repos.append(
            PublicRepo(
                id=repo_id,
                language=language,
                source=source,
                tasks=tuple(tasks),
                setup=setup,
            )
        )

    return PublicCorpus(
        schema_version=schema_version,
        corpus_id=corpus_id,
        description=description,
        repos=tuple(repos),
    )


def _parse_source(
    path: Path, repo_idx: int, raw: object,
) -> CorpusSource:
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: repos[{repo_idx}].source must be a mapping"
        )
    kind = str(raw.get("kind") or "")
    placeholder = bool(raw.get("placeholder"))
    if kind == "git":
        url = str(raw.get("url") or "")
        sha = str(raw.get("sha") or "")
        if not url:
            raise ValueError(
                f"{path}: repos[{repo_idx}].source.url is required for git"
            )
        if (
            len(sha) != 40
            or any(c not in "0123456789abcdefABCDEF" for c in sha)
        ):
            raise ValueError(
                f"{path}: repos[{repo_idx}].source.sha must be 40-char hex "
                f"(got {sha!r})"
            )
        return CorpusSource(
            kind="git", url=url, sha=sha, placeholder=placeholder,
        )
    if kind == "local":
        local_path = str(raw.get("path") or "")
        if not local_path:
            raise ValueError(
                f"{path}: repos[{repo_idx}].source.path is required for local"
            )
        return CorpusSource(
            kind="local", path=local_path, placeholder=placeholder,
        )
    raise ValueError(
        f"{path}: repos[{repo_idx}].source.kind must be 'git' or 'local' "
        f"(got {kind!r})"
    )


def _parse_setup(
    path: Path, repo_id: str, raw: object,
) -> SetupStep | None:
    """Parse the optional ``setup:`` clause for a repo.

    Returns None when the clause is absent. Validates that the
    ``requires_binary``, ``cmd``, and ``produces`` fields are
    well-formed; raises :class:`ValueError` on malformed input so a
    typo cannot silently disable the libclang variant.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: repos[{repo_id}].setup must be a mapping"
        )
    binary = str(raw.get("requires_binary") or "")
    if not binary:
        raise ValueError(
            f"{path}: repos[{repo_id}].setup.requires_binary is required"
        )
    cmd_raw = raw.get("cmd")
    if not isinstance(cmd_raw, list) or not cmd_raw:
        raise ValueError(
            f"{path}: repos[{repo_id}].setup.cmd must be a non-empty list"
        )
    cmd = tuple(str(item) for item in cmd_raw)
    produces = str(raw.get("produces") or "")
    if not produces:
        raise ValueError(
            f"{path}: repos[{repo_id}].setup.produces is required"
        )
    timeout_s = int(raw.get("timeout_s") or 120)
    return SetupStep(
        requires_binary=binary,
        cmd=cmd,
        produces=produces,
        timeout_s=timeout_s,
    )


def _parse_tasks(
    path: Path, repo_id: str, raw_list: list,
) -> list[PublicTask]:
    out: list[PublicTask] = []
    for ti, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("id") or "")
        family = str(raw.get("family") or "")
        if family not in _ALLOWED_FAMILIES:
            raise ValueError(
                f"{path}: repos[{repo_id}].tasks[{ti}].family={family!r} "
                f"not in {sorted(_ALLOWED_FAMILIES)}"
            )
        answer_files = tuple(
            str(p) for p in (raw.get("answer_files") or [])
        )
        out.append(
            PublicTask(
                repo_id=repo_id,
                id=task_id,
                family=family,
                prompt=str(raw.get("prompt") or ""),
                term=str(raw.get("term") or ""),
                symbol=(
                    str(raw["symbol"])
                    if raw.get("symbol")
                    else None
                ),
                answer_files=answer_files,
            )
        )
    return out


__all__ = [
    "CorpusSource",
    "PublicCorpus",
    "PublicRepo",
    "PublicTask",
    "SetupStep",
    "load_public_corpus",
]
