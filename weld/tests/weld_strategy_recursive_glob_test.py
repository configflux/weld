"""``**`` must work in the ten single-directory strategies (bd t06t).

Nine of the ten below resolved their glob as a
``(root / pattern).parent.is_dir()`` early-return plus one directory's worth
of matching (``tool_script`` lost that guard earlier, to bd 0edz, and is
carried here so the whole family shares one pin). For any pattern
containing ``**`` that parent is the literal path ``pkg/**``, which is never a
directory, so the guard returned and the strategy emitted **nothing**. A user
who wrote ``glob: docs/**/*.md`` for ``markdown`` got silence, not a subset --
arguably a worse trap than the ``exclude:`` leak bd 9gdq fixed, because there
is no partial result to notice.

bd 9gdq deliberately left this alone: it routed all ten through ``walk_glob``
(which does handle ``**``) but kept each guard, so ``**`` behaviour stayed
byte-identical to before. ADR 0112 removes the guard as part of moving every
strategy onto the one shared resolver, which is what this battery pins.

Sibling batteries, same fixture harness, different contract:

* ``weld_strategy_exclude_flat_glob_test`` -- what a *flat* glob owes
  ``exclude:``, including the documented bare-directory-form gap.
* ``weld_strategy_exclude_directory_form_test`` /
  ``weld_strategy_exclude_wave3_test`` -- the recursive-glob strategies.
* ``weld_glob_resolve_test`` -- the resolver itself, without any strategy.

The assertion here is deliberately the *positive* one ("the nested file is
emitted"), because the defect was absence. Each case also re-checks the flat
form, so removing the guard cannot have cost the single-directory path
anything.
"""

from __future__ import annotations

import unittest

from weld.strategies import (
    compose,
    dockerfile,
    events,
    fastapi,
    firstline_md,
    frontmatter_md,
    gh_workflow,
    markdown,
    pydantic,
    runbook,
    sqlalchemy,
    tool_script,
    viz_frontend,
    yaml_meta,
)
from weld.tests._exclude_form_harness import EXCLUDED_DIR as _DROP_DIR
from weld.tests._exclude_form_harness import Case, CaseRunnerMixin

#: The keep file sits *below* a directory the flat form could not reach, so
#: only a working ``**`` resolve can emit it.
_KEEP_DIR = "pkg/deep/further"

_DROP_MARKERS = ("zzdrop",)


def docker(tag: str) -> str:
    return f"""
    FROM python:3.11-slim
    LABEL app="{tag}"
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
    return (
        f'<html><body><div id="{tag}" class="{tag}-box">{tag}</div>'
        "</body></html>\n"
    )


def router_py(tag: str) -> str:
    return f"""
    from fastapi import APIRouter

    router = APIRouter()


    @router.get("/{tag}")
    def {tag}_index():
        return {{"tag": "{tag}"}}
    """


def contract_py(tag: str) -> str:
    return f"""
    from pydantic import BaseModel


    class {tag.capitalize()}Contract(BaseModel):
        identifier: str
    """


def compose_yml(tag: str) -> str:
    return f"""
    services:
      {tag}:
        image: alpine:3
        environment:
          KAFKA_{tag.upper()}_TOPIC: {tag}-topic
    """


def workflow_yml(tag: str) -> str:
    return f"""
    name: {tag}
    on:
      push:
    jobs:
      build:
        runs-on: ubuntu-latest
    """


def _case(name, module, ext, body, *, stem_prefix="") -> Case:
    """One strategy, with a nested keep file and an excludable drop file."""
    return Case(
        name, module, f"pkg/**/{stem_prefix}*{ext}",
        f"{_KEEP_DIR}/{stem_prefix}zzkeep{ext}", body("zzkeep"),
        f"{_DROP_DIR}/{stem_prefix}zzdrop{ext}", body("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    )


CASES: tuple[Case, ...] = (
    Case(
        "dockerfile", dockerfile, "pkg/**/Dockerfile*",
        f"{_KEEP_DIR}/Dockerfile.zzkeep", docker("zzkeep"),
        f"{_DROP_DIR}/Dockerfile.zzdrop", docker("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
    ),
    _case("firstline_md", firstline_md, ".md", plain_md),
    _case("frontmatter_md", frontmatter_md, ".md", frontmatter),
    _case("gh_workflow", gh_workflow, ".yml", workflow_yml),
    _case("markdown", markdown, ".md", plain_md),
    _case("runbook", runbook, ".md", plain_md),
    _case("sqlalchemy", sqlalchemy, ".py", sa_model),
    _case("tool_script", tool_script, ".sh", shell),
    _case("viz_frontend", viz_frontend, ".html", html_page),
    _case("yaml_meta", yaml_meta, ".yml", workflow_yml),
    # bd b9xgd: four more of the same family, found after ADR 0112 landed.
    # These never moved onto the shared resolver at all, so they kept the
    # guard in two shapes -- `fastapi`/`pydantic` early-return (silence),
    # `compose`/`events` fall back to `parent = root` and emit the root
    # directory's files instead (the wrong set). One battery, because the
    # contract is the same one the ten above pin.
    _case("fastapi", fastapi, ".py", router_py),
    _case("pydantic", pydantic, ".py", contract_py),
    _case("compose", compose, ".yml", compose_yml),
    Case(
        "events", events, "pkg/**/*.yml",
        f"{_KEEP_DIR}/zzkeep.yml", compose_yml("zzkeep"),
        f"{_DROP_DIR}/zzdrop.yml", compose_yml("zzdrop"),
        keep_marker="zzkeep", drop_markers=_DROP_MARKERS,
        # The facade dispatches on `kind`, so the case goes through the real
        # `events.extract` rather than reaching into `events_config`.
        extra_source={"kind": "compose_env"},
    ),
)


class StrategyRecursiveGlobTest(CaseRunnerMixin, unittest.TestCase):
    """A ``**`` glob must reach a nested file, and still honour excludes."""

    def test_recursive_glob_emits_the_nested_file(self) -> None:
        """The bd t06t fix. Before it, nine of these ten emitted nothing."""
        for case in CASES:
            with self.subTest(strategy=case.name):
                blob = self._blob(case, None)
                self.assertIn(
                    case.keep_marker.lower(), blob,
                    f"{case.name}: recursive glob {case.glob!r} emitted "
                    f"nothing for a file nested under {_KEEP_DIR!r} -- the "
                    f"(root / pattern).parent.is_dir() guard is back",
                )

    def test_recursive_glob_reaches_more_than_one_directory(self) -> None:
        """Control: the drop file, one directory over, is also reached.

        Without this the test above could pass on a resolve that only ever
        looked at a single directory that happened to be the right one.
        """
        for case in CASES:
            with self.subTest(strategy=case.name):
                self._assert_baseline_is_live(case)

    def test_directory_form_exclude_prunes_under_a_recursive_glob(self) -> None:
        """``pkg/tests`` prunes the subtree -- reachable now the glob is ``**``.

        This is the form ``weld_strategy_exclude_flat_glob_test`` records as
        out of reach for a flat glob: ``matches_exclude`` has no
        ancestor-directory check, so only the prune-during-descent walker
        gives the bare directory form meaning.
        """
        for case in CASES:
            with self.subTest(strategy=case.name):
                self._assert_pruned(case, [_DROP_DIR])

    def test_subtree_form_exclude_prunes_under_a_recursive_glob(self) -> None:
        for case in CASES:
            with self.subTest(strategy=case.name):
                self._assert_pruned(case, [f"{_DROP_DIR}/**"])

    def test_bare_directory_name_exclude_prunes_at_depth(self) -> None:
        for case in CASES:
            with self.subTest(strategy=case.name):
                self._assert_pruned(case, ["tests"])

    def test_flat_glob_still_resolves_its_one_directory(self) -> None:
        """Removing the guard must cost the single-directory path nothing.

        The guard was byte-identical to what ``walk_glob``'s own non-``**``
        branch does, so this is the half of the change that must be a no-op.
        """
        for case in CASES:
            with self.subTest(strategy=case.name):
                flat = case.glob.replace("pkg/**/", f"{_KEEP_DIR}/")
                source = {"glob": flat, **case.extra_source}
                root = self._build(case)
                result = case.module.extract(root, source, {})
                blob = (
                    repr(result.nodes) + repr(result.edges)
                    + repr(result.discovered_from)
                ).lower()
                self.assertIn(case.keep_marker.lower(), blob, case.name)
                self.assertNotIn("zzdrop", blob, case.name)

    def test_flat_glob_into_a_missing_directory_is_empty(self) -> None:
        """The one thing the guard did that had to keep working."""
        for case in CASES:
            with self.subTest(strategy=case.name):
                flat = case.glob.replace("pkg/**/", "nope/")
                source = {"glob": flat, **case.extra_source}
                root = self._build(case)
                result = case.module.extract(root, source, {})
                self.assertEqual(result.nodes, {}, case.name)
                self.assertEqual(result.discovered_from, [], case.name)

    def test_no_exclude_key_is_not_treated_as_an_exclude(self) -> None:
        for case in CASES:
            with self.subTest(strategy=case.name):
                self.assertEqual(self._blob(case, None), self._blob(case, []))


if __name__ == "__main__":
    unittest.main()
