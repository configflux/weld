"""The weld version stamped into the public benchmark report -- both places.

``wd bench --public`` opens every report with the version of the weld that
produced it *and* names the committable artifact after that same version,
so a published result says which weld earned those numbers. The two are
resolved at different moments -- the filename before the run starts, the
header while the report is built -- which is exactly how a report ends up
named after one version and headered with another. Both are pinned here to
:mod:`weld._version`, the one resolver that also answers ``wd --version``.

That has to hold in both environments weld ships into -- and the one it
matters most in, an installed wheel with no repo-root ``VERSION`` beside
site-packages, is exactly the one this test process is not. So each case
installs the environment it asserts (distribution metadata, or a checkout's
``VERSION`` file) instead of reading the ambient one.

Sibling of ``weld_public_bench_test.py``, which covers the rest of the
runner; these live apart because that file sits at the line-count cap.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from typing import Callable, Iterator
from unittest import mock


from weld import _version  # noqa: E402
from weld.bench._public_report import render_public_report  # noqa: E402
from weld.bench._public_runner import (  # noqa: E402
    PublicCorpus,
    PublicRunReport,
    run_public,
)
from weld.bench.bench_cli import (  # noqa: E402
    _resolve_public_report_path,
)


def _no_distribution(name: str) -> str:
    """Stand-in for ``importlib.metadata.version`` with nothing installed."""
    raise PackageNotFoundError(name)


def _installed(name: str) -> str:
    """Stand-in for a real ``pip install configflux-weld``."""
    if name != _version.DISTRIBUTION_NAME:
        raise PackageNotFoundError(name)
    return "9.9.9"


@contextlib.contextmanager
def _version_environment(
    *, metadata: Callable[[str], str], version_file: Path,
) -> Iterator[None]:
    """Pin both things weld can learn its own version from.

    Shared by the header and filename cases on purpose: an agreement
    assertion is only worth anything if both surfaces answered the same
    environment.
    """
    with mock.patch(
        "importlib.metadata.version", metadata,
    ), mock.patch.object(
        _version, "version_file_path", lambda: version_file,
    ):
        yield


def _empty_corpus() -> PublicCorpus:
    """A corpus with no repos: no adapter dispatch and no clone.

    A report produced from it exists only to show what the runner
    resolved for its header.
    """
    return PublicCorpus(
        schema_version=1,
        corpus_id="version-probe",
        description="",
        repos=(),
    )


class ReportHeaderVersionTest(unittest.TestCase):
    """Which weld ran, on a run nobody handed a version to."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.absent_file = self.tmp / "absent" / "VERSION"

    def _run(
        self, *, metadata: Callable[[str], str], version_file: Path,
    ) -> PublicRunReport:
        """Run an empty corpus under a pinned version environment."""
        with _version_environment(
            metadata=metadata, version_file=version_file,
        ):
            return run_public(_empty_corpus(), self.tmp)

    def test_installed_distribution_version_reaches_the_header(self) -> None:
        # The regression: the runner resolved from <package>/../../VERSION,
        # which is the repo root in a checkout but site-packages in a wheel
        # -- where no VERSION exists -- so every installed run headered
        # "unknown" rather than naming itself.
        report = self._run(metadata=_installed, version_file=self.absent_file)

        self.assertEqual(report.weld_version, "9.9.9")
        self.assertEqual(
            render_public_report(report).splitlines()[0],
            "# Weld public benchmark (9.9.9)",
        )

    def test_source_checkout_version_file_reaches_the_header(self) -> None:
        version_file = self.tmp / "VERSION"
        version_file.write_text("1.2.3\n", encoding="utf-8")

        report = self._run(
            metadata=_no_distribution, version_file=version_file,
        )

        self.assertEqual(report.weld_version, "1.2.3")
        self.assertEqual(
            render_public_report(report).splitlines()[0],
            "# Weld public benchmark (1.2.3)",
        )

    def test_unresolvable_version_keeps_the_placeholder(self) -> None:
        # A partial checkout has neither source. The header is prose, not a
        # version-shaped field, so it says so in words -- and not knowing
        # its own version must never fail a benchmark run.
        report = self._run(
            metadata=_no_distribution, version_file=self.absent_file,
        )

        self.assertEqual(report.weld_version, "unknown")


class ReportPathVersionTest(unittest.TestCase):
    """Which weld the report *filename* names.

    Unlike the header, the path is chosen before the run produces
    anything, so there is no artifact to read it off -- these assert on
    the resolver the CLI calls. What they are really guarding is that
    ``--root`` stops feeding it: the benchmarked repo's ``VERSION`` says
    how old *that* project is, which is a confidently wrong answer to
    "which weld earned these numbers".
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.absent_file = self.tmp / "absent" / "VERSION"
        # The repository under benchmark -- someone else's project, with
        # its own release history and no bearing on this one.
        self.foreign_root = self.tmp / "foreign-repo"
        self.foreign_root.mkdir()
        (self.foreign_root / "VERSION").write_text(
            "7.7.7\n", encoding="utf-8",
        )

    def _resolve(
        self, *, metadata: Callable[[str], str], version_file: Path,
    ) -> Path:
        with _version_environment(
            metadata=metadata, version_file=version_file,
        ):
            return _resolve_public_report_path(self.foreign_root)

    def test_installed_weld_ignores_the_benchmarked_repos_version(
        self,
    ) -> None:
        # The regression: an installed weld pointed at another repository
        # named the report after THAT repo's VERSION.
        path = self._resolve(
            metadata=_installed, version_file=self.absent_file,
        )

        self.assertEqual(
            path,
            self.foreign_root / "docs" / "bench"
            / "PUBLIC-BENCHMARK-9.9.9.md",
        )

    def test_source_checkout_version_file_names_the_report(self) -> None:
        weld_version_file = self.tmp / "weld-checkout" / "VERSION"
        weld_version_file.parent.mkdir()
        weld_version_file.write_text("1.2.3\n", encoding="utf-8")

        path = self._resolve(
            metadata=_no_distribution, version_file=weld_version_file,
        )

        self.assertEqual(path.name, "PUBLIC-BENCHMARK-1.2.3.md")

    def test_filename_and_header_name_the_same_weld(self) -> None:
        # The invariant, asserted without naming a number: whatever this
        # environment resolves, both surfaces have to say it. Naming one
        # would only re-test the case above and would stop catching the
        # two resolvers drifting apart.
        with _version_environment(
            metadata=_installed, version_file=self.absent_file,
        ):
            path = _resolve_public_report_path(self.foreign_root)
            report = run_public(_empty_corpus(), self.tmp)

        self.assertEqual(
            path.name, f"PUBLIC-BENCHMARK-{report.weld_version}.md",
        )

    def test_unresolvable_version_keeps_the_unversioned_path(self) -> None:
        # A partial checkout has neither source. The header can say
        # "unknown" because it is prose; a filename cannot, so it drops
        # the version rather than stamping a version-shaped non-version
        # -- and still must not reach for the foreign repo's 7.7.7.
        path = self._resolve(
            metadata=_no_distribution, version_file=self.absent_file,
        )

        self.assertEqual(
            path,
            self.foreign_root / "docs" / "bench" / "PUBLIC-BENCHMARK.md",
        )


if __name__ == "__main__":
    unittest.main()
