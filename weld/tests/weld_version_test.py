"""Resolution order and failure behaviour of ``weld._version``.

The two environments weld ships into disagree about where its version
lives: an installed distribution has metadata and no ``VERSION`` file, a
raw checkout has the file and no metadata. Both are pinned here, along
with the property every caller depends on -- the resolver reports an
absent version instead of raising, because it is called from startup and
telemetry paths where the version is never the point of the operation.

The environment running these tests may be either kind (weld is often not
pip-installed in the test sandbox), so each case installs the state it
asserts rather than reading the ambient one.
"""

from __future__ import annotations

import tempfile
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest import mock

from weld import _version


class _NoDistribution:
    """Stand-in for ``importlib.metadata.version`` with nothing installed."""

    def __call__(self, name: str) -> str:
        raise PackageNotFoundError(name)


class _RecordingVersionFile:
    """Duck-typed stand-in for the ``VERSION`` path that records its reads.

    ``weld_version`` only ever calls ``open()`` and ``readline(limit)``, so a
    stand-in can report the limit it was given -- the one way to assert the
    read is bounded rather than merely that an over-long value is discarded
    after the fact.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.readline_limits: list[int] = []

    def open(self, encoding: str | None = None) -> "_RecordingVersionFile":
        return self

    def __enter__(self) -> "_RecordingVersionFile":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def readline(self, limit: int = -1) -> str:
        self.readline_limits.append(limit)
        return self.text if limit < 0 else self.text[:limit]


class WeldVersionResolutionTest(unittest.TestCase):
    """Metadata wins; the ``VERSION`` file is the checkout fallback."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.version_file = Path(self._tmp.name) / "VERSION"

    def _resolve(self, *, metadata: object, file_text: str | None) -> str | None:
        if file_text is not None:
            self.version_file.write_text(file_text, encoding="utf-8")
        with mock.patch("importlib.metadata.version", metadata), mock.patch.object(
            _version, "version_file_path", lambda: self.version_file
        ):
            return _version.weld_version()

    def test_installed_distribution_metadata_wins(self) -> None:
        # An installed wheel has no repo-root VERSION beside the package, so
        # metadata is not merely preferred here -- it is the only answer.
        resolved = self._resolve(metadata=lambda name: "9.9.9", file_text=None)

        self.assertEqual(resolved, "9.9.9")

    def test_metadata_is_preferred_over_a_stale_version_file(self) -> None:
        # An editable install can carry both. Metadata reflects what is
        # actually importable, so a checkout mid-release-bump must not have
        # its uninstalled VERSION file override the running code.
        resolved = self._resolve(metadata=lambda name: "9.9.9", file_text="1.2.3\n")

        self.assertEqual(resolved, "9.9.9")

    def test_metadata_is_looked_up_under_the_distribution_name(self) -> None:
        # The import package is `weld`, the distribution is `configflux-weld`.
        # Looking up the import name finds nothing and silently degrades to
        # the file fallback -- which is empty in an installed wheel.
        seen: list[str] = []

        def _record(name: str) -> str:
            seen.append(name)
            return "9.9.9"

        self._resolve(metadata=_record, file_text=None)

        self.assertEqual(seen, [_version.DISTRIBUTION_NAME])
        self.assertEqual(_version.DISTRIBUTION_NAME, "configflux-weld")

    def test_source_checkout_falls_back_to_the_version_file(self) -> None:
        resolved = self._resolve(metadata=_NoDistribution(), file_text="1.2.3\n")

        self.assertEqual(resolved, "1.2.3")

    def test_blank_metadata_falls_through_to_the_version_file(self) -> None:
        # A blank metadata version is exactly as useless as no metadata --
        # the empty serverInfo.version this module exists to prevent.
        resolved = self._resolve(metadata=lambda name: "  ", file_text="1.2.3\n")

        self.assertEqual(resolved, "1.2.3")

    def test_missing_version_file_resolves_to_none_without_raising(self) -> None:
        resolved = self._resolve(metadata=_NoDistribution(), file_text=None)

        self.assertIsNone(resolved)

    def test_blank_version_file_resolves_to_none(self) -> None:
        # None means "cannot tell", which callers render on their own terms;
        # returning "" would push a blank version onto every surface.
        resolved = self._resolve(metadata=_NoDistribution(), file_text="\n")

        self.assertIsNone(resolved)

    def test_unreadable_version_file_resolves_to_none_without_raising(self) -> None:
        # A directory where the file should be stands in for any OSError --
        # the resolver must degrade, never propagate, on a startup path.
        directory = Path(self._tmp.name) / "VERSION_dir"
        directory.mkdir()
        with mock.patch(
            "importlib.metadata.version", _NoDistribution()
        ), mock.patch.object(_version, "version_file_path", lambda: directory):
            self.assertIsNone(_version.weld_version())

    def test_undecodable_version_file_resolves_to_none_without_raising(self) -> None:
        # UnicodeDecodeError is a ValueError, not an OSError, so a corrupt
        # VERSION file is the one failure an OSError-only guard would let
        # escape into a caller that was promised this cannot fail.
        self.version_file.write_bytes(b"\xff\xfe not utf-8")
        with mock.patch(
            "importlib.metadata.version", _NoDistribution()
        ), mock.patch.object(
            _version, "version_file_path", lambda: self.version_file
        ):
            self.assertIsNone(_version.weld_version())

    def test_unresolvable_file_location_does_not_propagate(self) -> None:
        # Locating the file can fail before any read (no ``__file__`` under
        # an exotic loader, an OSError from resolving a path). The promise
        # this function makes to startup paths is unconditional.
        def _explode() -> Path:
            raise RuntimeError("cannot locate the package directory")

        with mock.patch(
            "importlib.metadata.version", _NoDistribution()
        ), mock.patch.object(_version, "version_file_path", _explode):
            self.assertIsNone(_version.weld_version())

    def test_broken_metadata_backend_does_not_propagate(self) -> None:
        # importlib.metadata raises more than PackageNotFoundError on a
        # damaged site-packages; none of it may reach the caller.
        def _explode(name: str) -> str:
            raise RuntimeError("corrupt distribution metadata")

        resolved = self._resolve(metadata=_explode, file_text="1.2.3\n")

        self.assertEqual(resolved, "1.2.3")


class WeldVersionFileBoundsTest(unittest.TestCase):
    """What the ``VERSION`` file may contribute is bounded.

    Whatever this file says is republished: into the MCP ``initialize``
    reply, into schema-validated telemetry events, onto stdout. A version is
    short and single-line by construction, so anything else is corruption --
    and it must cost neither unbounded memory nor a junk identity.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.version_file = Path(self._tmp.name) / "VERSION"

    def _resolve_file(self, text: str) -> str | None:
        self.version_file.write_text(text, encoding="utf-8")
        with mock.patch(
            "importlib.metadata.version", _NoDistribution()
        ), mock.patch.object(
            _version, "version_file_path", lambda: self.version_file
        ):
            return _version.weld_version()

    def test_only_the_first_line_is_used(self) -> None:
        resolved = self._resolve_file("1.2.3\ntrailing junk\nmore junk\n")

        self.assertEqual(resolved, "1.2.3")

    def test_an_over_long_line_is_rejected_rather_than_republished(self) -> None:
        resolved = self._resolve_file("9" * (_version._MAX_VERSION_LEN + 1))

        self.assertIsNone(resolved)

    def test_a_version_at_the_length_limit_is_still_accepted(self) -> None:
        limit = "9" * _version._MAX_VERSION_LEN

        self.assertEqual(self._resolve_file(limit), limit)

    def test_an_over_long_line_is_rejected_rather_than_truncated(self) -> None:
        # The failure mode a bounded read invites: cutting a long line down
        # to the limit yields a string that still passes every later check
        # and gets republished as this server's identity.
        resolved = self._resolve_file("9" * 5000)

        self.assertIsNone(resolved)

    def test_the_read_itself_is_bounded(self) -> None:
        # Rejecting an over-long value afterwards is not enough: a huge
        # VERSION file must never reach memory on a startup path. The read
        # stops one character past the limit, which is exactly what makes
        # the over-long case distinguishable from a value at the limit.
        spy = _RecordingVersionFile("1.2.3\n")
        with mock.patch(
            "importlib.metadata.version", _NoDistribution()
        ), mock.patch.object(_version, "version_file_path", lambda: spy):
            resolved = _version.weld_version()

        self.assertEqual(resolved, "1.2.3")
        self.assertEqual(spy.readline_limits, [_version._MAX_VERSION_LEN + 1])


class WeldVersionFilePathTest(unittest.TestCase):
    """The fallback path must point at the repo root, not inside the package."""

    def test_version_file_sits_one_level_above_the_package(self) -> None:
        package_dir = Path(_version.__file__).resolve().parent

        path = _version.version_file_path()

        self.assertEqual(path.name, "VERSION")
        self.assertEqual(path.parent, package_dir.parent)


if __name__ == "__main__":
    unittest.main()
