"""``exclude:`` coverage for a **flat** glob (no ``**``) -- bd 9gdq.

The ten strategies exercised here were once single-directory by
construction: they resolved their glob as ``(root / pattern).parent``
plus one ``parent.glob(name)`` call, so they only ever saw one
directory. Two consequences, both **measured** before bd 9gdq's fix:

1. Five of them (``dockerfile``, ``runbook``, ``sqlalchemy``,
   ``tool_script``, ``yaml_meta``) called ``should_skip`` *without*
   ``root=``, matching excludes by basename only -- so even the subtree
   form ``pkg/tests/**`` was silently ignored and the excluded file was
   read and emitted. Routing the resolve through the shared walker
   fixes it.
2. A flat glob cannot express the bare directory form; see
   :meth:`StrategyExcludeFlatGlobTest.test_directory_form_is_a_documented_gap`.

Since ADR 0112 these ten are no longer single-directory -- the
``parent.is_dir()`` guard that made a ``**`` pattern emit nothing is
gone (bd t06t), and they resolve through
:func:`weld.strategies._glob_resolve.resolve_glob` like everything else.
This battery keeps its flat-glob cases, which is the half that had to
stay byte-identical through that change; the recursive half is
``weld_strategy_recursive_glob_test``.

``discovered_from`` is asserted only through ``_DROP_MARKERS``, which
name the excluded *file*. These strategies record per-file provenance
(bd 8ia5 / bd od2a), so an existing-but-empty directory reports nothing
at all -- ``weld_firstline_md_strategy_test.test_empty_directory_returns_empty``
pins exactly that. The scanned directory is never an entry, because at
the repo root it degenerates to ``"./"``, the marker that makes every
path in the repository count as tracked source.

The strategies whose glob was always recursive have a stricter contract
and live in ``weld_strategy_exclude_wave3_test`` / the eerc battery.
"""

from __future__ import annotations

import unittest

from weld.strategies import (
    dockerfile,
    firstline_md,
    frontmatter_md,
    gh_workflow,
    markdown,
    runbook,
    sqlalchemy,
    tool_script,
    viz_frontend,
    yaml_meta,
)
from weld.tests._exclude_form_harness import EXCLUDED_DIR as _DROP_DIR
from weld.tests._exclude_form_harness import Case, CaseRunnerMixin

#: The subtree form is the only exclude form a single-directory glob can
#: express; see ``test_directory_form_is_a_documented_gap``.
_SUBTREE = f"{_DROP_DIR}/**"

#: Only the file's own evidence counts as a leak here -- the scanned
#: directory legitimately stays in ``discovered_from`` (see module docstring).
_DROP_MARKERS = ("zzdrop",)


def docker(tag: str) -> str:
    return f"""
    FROM python:3.11-slim
    LABEL app="{tag}"
    COPY {tag}.py /app/{tag}.py
    CMD ["python", "/app/{tag}.py"]
    """


def plain_md(tag: str) -> str:
    return f"# {tag} title\n\nSome {tag} prose.\n"


def frontmatter(tag: str) -> str:
    return f"""
    ---
    name: {tag}
    description: {tag} agent
    model: opus
    ---
    Body for {tag}.
    """


def sa_model(tag: str) -> str:
    return f"""
    from sqlalchemy.orm import DeclarativeBase
    from sqlalchemy import Column, Integer, String


    class Base(DeclarativeBase):
        pass


    class {tag.capitalize()}(Base):
        __tablename__ = "{tag}"
        id = Column(Integer, primary_key=True)
        name = Column(String)
    """


def shell(tag: str) -> str:
    return f"#!/usr/bin/env bash\n# {tag} helper\necho {tag}\n"


def html_page(tag: str) -> str:
    return f"<html><body><div id=\"{tag}\" class=\"{tag}-box\">{tag}</div></body></html>\n"


def workflow_yml(tag: str) -> str:
    return f"""
    name: {tag}
    on:
      push:
    jobs:
      build:
        runs-on: ubuntu-latest
    """


#: ``glob`` points *into* the excluded directory so the drop file is the
#: only match; the keep file sits one level up and is reached by
#: ``_kept_glob`` below, which proves the fix does not over-prune.
CASES: tuple[Case, ...] = (
    Case(
        "dockerfile", dockerfile, f"{_DROP_DIR}/Dockerfile*",
        "pkg/Dockerfile.zzkeep", docker("zzkeep"),
        f"{_DROP_DIR}/Dockerfile.zzdrop", docker("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    Case(
        "firstline_md", firstline_md, f"{_DROP_DIR}/*.md",
        "pkg/zzkeep.md", plain_md("zzkeep"),
        f"{_DROP_DIR}/zzdrop.md", plain_md("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    Case(
        "frontmatter_md", frontmatter_md, f"{_DROP_DIR}/*.md",
        "pkg/zzkeep.md", frontmatter("zzkeep"),
        f"{_DROP_DIR}/zzdrop.md", frontmatter("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    Case(
        "markdown", markdown, f"{_DROP_DIR}/*.md",
        "pkg/zzkeep.md", plain_md("zzkeep"),
        f"{_DROP_DIR}/zzdrop.md", plain_md("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    Case(
        "runbook", runbook, f"{_DROP_DIR}/*.md",
        "pkg/zzkeep.md", plain_md("zzkeep"),
        f"{_DROP_DIR}/zzdrop.md", plain_md("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    Case(
        "sqlalchemy", sqlalchemy, f"{_DROP_DIR}/*.py",
        "pkg/zzkeep.py", sa_model("zzkeep"),
        f"{_DROP_DIR}/zzdrop.py", sa_model("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    Case(
        "tool_script", tool_script, f"{_DROP_DIR}/*.sh",
        "pkg/zzkeep.sh", shell("zzkeep"),
        f"{_DROP_DIR}/zzdrop.sh", shell("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    # gh_workflow and viz_frontend measured clean on the subtree form
    # already; they are routed through walk_glob for one uniform resolve
    # path (the issue's audit query is `reads exclude: but never reaches
    # walk_glob`, and it must come back empty) and covered here so the
    # refactor cannot silently regress them.
    Case(
        "gh_workflow", gh_workflow, f"{_DROP_DIR}/*.yml",
        "pkg/zzkeep.yml", workflow_yml("zzkeep"),
        f"{_DROP_DIR}/zzdrop.yml", workflow_yml("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    Case(
        "viz_frontend", viz_frontend, f"{_DROP_DIR}/*.html",
        "pkg/zzkeep.html", html_page("zzkeep"),
        f"{_DROP_DIR}/zzdrop.html", html_page("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    Case(
        "yaml_meta", yaml_meta, f"{_DROP_DIR}/*.yml",
        "pkg/zzkeep.yml", workflow_yml("zzkeep"),
        f"{_DROP_DIR}/zzdrop.yml", workflow_yml("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
)


def _kept_glob(case: Case) -> str:
    """The same glob aimed at the parent dir, where the keep file lives."""
    return case.glob.replace(f"{_DROP_DIR}/", "pkg/")


class StrategyExcludeFlatGlobTest(CaseRunnerMixin, unittest.TestCase):
    """A single-directory glob must still honour the subtree form."""

    def test_baseline_emits_from_the_excludable_directory(self) -> None:
        """Control: with no excludes the droppable file is really emitted.

        Without this, an inert fixture would make every assertion below
        pass vacuously.
        """
        for case in CASES:
            with self.subTest(strategy=case.name):
                self._assert_baseline_is_live(case)

    def test_subtree_form_prunes_the_directory(self) -> None:
        """``pkg/tests/**`` must leave no node or edge from the drop file.

        Before the fix these strategies matched excludes by basename, so
        a slash-bearing pattern never fired and the file was read and
        emitted anyway.
        """
        for case in CASES:
            with self.subTest(strategy=case.name):
                blob = self._blob(case, [_SUBTREE])
                for marker in case.drop_markers:
                    self.assertNotIn(
                        marker.lower(), blob,
                        f"{case.name}: exclude {_SUBTREE!r} leaked {marker!r}",
                    )

    def test_excluding_one_directory_does_not_prune_a_sibling(self) -> None:
        """The same exclude must not touch a file outside that directory."""
        for case in CASES:
            with self.subTest(strategy=case.name):
                source = {"glob": _kept_glob(case), "exclude": [_SUBTREE],
                          **case.extra_source}
                root = self._build(case)
                result = case.module.extract(root, source, {})
                blob = (
                    repr(result.nodes) + repr(result.edges)
                    + repr(result.discovered_from)
                ).lower()
                self.assertIn(
                    case.keep_marker.lower(), blob,
                    f"{case.name}: exclude {_SUBTREE!r} over-pruned",
                )
                self.assertNotIn(_DROP_DIR, blob)

    def test_no_exclude_key_is_not_treated_as_an_exclude(self) -> None:
        """A source entry with no ``exclude:`` prunes nothing."""
        for case in CASES:
            with self.subTest(strategy=case.name):
                self.assertEqual(self._blob(case, None), self._blob(case, []))

    def test_directory_form_is_a_documented_gap(self) -> None:
        """The bare directory form is out of reach for a flat glob.

        ``matches_exclude`` tests the file path with no
        ancestor-directory check, and ``walk_glob``'s non-``**`` branch
        delegates to it, so ``exclude: [pkg/tests]`` against
        ``glob: pkg/tests/*.md`` cannot prune. Only a recursive glob
        reaches the prune-during-descent walker that gives the directory
        form meaning.

        Closing this would mean changing ``matches_exclude`` itself --
        a ripple bd 3abf judged and declined, and bd eerc reconfirmed.
        This test characterises the boundary so that a future change to
        those semantics fails here and gets a deliberate decision rather
        than landing unnoticed.
        """
        for case in CASES:
            with self.subTest(strategy=case.name):
                blob = self._blob(case, [_DROP_DIR])
                self.assertTrue(
                    any(m.lower() in blob for m in case.drop_markers),
                    f"{case.name}: directory-form exclude now prunes a flat "
                    f"glob -- matches_exclude semantics changed; revisit "
                    f"this documented gap (bd 9gdq)",
                )


if __name__ == "__main__":
    unittest.main()
