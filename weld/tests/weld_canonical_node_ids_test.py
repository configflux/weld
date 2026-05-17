"""Unit tests for the canonical node-ID contract (ADR 0041, Layer 1).

Covers ``weld._node_ids.canonical_slug``, ``file_id``, ``package_id``,
and ``entity_id``. Each function must be pure, total, and deterministic.
Exercises edge cases that previously diverged across legacy ``_slug``
implementations: empty/whitespace input, Unicode, slashes, multi-dashes,
NUL bytes, path traversal, stem-only collisions, and multi-dot
extensions.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path, PurePosixPath

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._node_ids import (  # noqa: E402
    canonical_slug,
    canonical_slug_case_sensitive,
    entity_id,
    file_id,
    package_id,
)


class CanonicalSlugTest(unittest.TestCase):
    """Edge-case coverage for :func:`canonical_slug`."""

    def test_lowercases_and_keeps_alnum(self) -> None:
        self.assertEqual(canonical_slug("HelloWorld"), "helloworld")
        self.assertEqual(canonical_slug("MyProject"), "myproject")

    def test_keeps_permitted_punctuation(self) -> None:
        # Dot, colon, underscore, dash are all permitted.
        for value in ("foo.bar", "foo:bar", "foo_bar", "foo-bar"):
            self.assertEqual(canonical_slug(value), value)

    def test_collapses_disallowed_chars_to_dash(self) -> None:
        self.assertEqual(canonical_slug("foo/bar"), "foo-bar")
        self.assertEqual(canonical_slug("foo bar"), "foo-bar")
        self.assertEqual(canonical_slug("foo\\bar"), "foo-bar")
        self.assertEqual(canonical_slug("foo@bar"), "foo-bar")
        self.assertEqual(canonical_slug("foo#bar!baz"), "foo-bar-baz")

    def test_coalesces_multi_dashes(self) -> None:
        self.assertEqual(canonical_slug("foo----bar"), "foo-bar")
        self.assertEqual(canonical_slug("foo / / bar"), "foo-bar")

    def test_strips_leading_and_trailing_dashes_and_whitespace(self) -> None:
        self.assertEqual(canonical_slug("---foo---"), "foo")
        self.assertEqual(canonical_slug("/foo/"), "foo")
        self.assertEqual(canonical_slug("   foo   "), "foo")
        self.assertEqual(canonical_slug("\tfoo\n"), "foo")

    def test_empty_returns_unknown(self) -> None:
        self.assertEqual(canonical_slug(""), "unknown")
        self.assertEqual(canonical_slug("   "), "unknown")
        self.assertEqual(canonical_slug("---"), "unknown")
        self.assertEqual(canonical_slug("///"), "unknown")

    def test_unicode_collapses_to_dash(self) -> None:
        # Unicode is not in the permitted set; it collapses, not raises.
        self.assertEqual(canonical_slug("café"), "caf")
        self.assertEqual(canonical_slug("naïve"), "na-ve")
        self.assertEqual(canonical_slug("日本語"), "unknown")

    def test_nul_byte_collapses_to_dash(self) -> None:
        # NUL bytes must be collapsed; never preserved (path-injection guard).
        self.assertEqual(canonical_slug("foo\x00bar"), "foo-bar")
        self.assertEqual(canonical_slug("\x00\x00foo"), "foo")

    def test_control_chars_collapse_to_dash(self) -> None:
        self.assertEqual(canonical_slug("foo\nbar\tbaz"), "foo-bar-baz")

    def test_dots_preserved_for_packages(self) -> None:
        # Dotted package names must round-trip cleanly.
        self.assertEqual(canonical_slug("pkg.sub.module"), "pkg.sub.module")

    def test_double_dots_preserved(self) -> None:
        # Multi-dot stems (foo.tar.gz) keep their inner dots.
        self.assertEqual(canonical_slug("a..b"), "a..b")

    def test_deterministic(self) -> None:
        # Same input -> same output, regardless of how often called.
        for value in ("Hello", "foo/bar", "café", "  spaces  "):
            self.assertEqual(canonical_slug(value), canonical_slug(value))

    def test_total_never_raises(self) -> None:
        # Cover several adversarial inputs; the function must be total.
        for value in ("", " ", "\x00", "‮", "x" * 1000, "----", "."):
            try:
                canonical_slug(value)
            except Exception as exc:  # pragma: no cover - failure path
                self.fail(f"canonical_slug raised on {value!r}: {exc!r}")


class CanonicalSlugCaseSensitiveTest(unittest.TestCase):
    """Coverage for :func:`canonical_slug_case_sensitive`. Mirrors
    :class:`CanonicalSlugTest` but preserves case so ``symbol:*`` IDs
    (and post-vjxi.6 ``file:*`` IDs) keep case-distinct identifiers
    distinct, e.g. C# ``SIZE`` vs ``Size`` and ``Foo.cs`` vs ``foo.cs``.
    """

    def test_preserves_case(self) -> None:
        self.assertEqual(canonical_slug_case_sensitive("HelloWorld"), "HelloWorld")
        self.assertEqual(canonical_slug_case_sensitive("SIZE"), "SIZE")
        self.assertEqual(canonical_slug_case_sensitive("Size"), "Size")
        self.assertNotEqual(
            canonical_slug_case_sensitive("SIZE"),
            canonical_slug_case_sensitive("Size"),
        )

    def test_keeps_permitted_punctuation(self) -> None:
        for value in ("Foo.Bar", "Foo:Bar", "Foo_Bar", "Foo-Bar"):
            self.assertEqual(canonical_slug_case_sensitive(value), value)

    def test_collapses_disallowed_chars_to_dash(self) -> None:
        for value in ("Foo/Bar", "Foo Bar", "Foo\\Bar", "Foo@Bar"):
            self.assertEqual(canonical_slug_case_sensitive(value), "Foo-Bar")

    def test_coalesces_multi_dashes(self) -> None:
        # Punctuation collapse still happens; only case is preserved.
        self.assertEqual(canonical_slug_case_sensitive("Foo----Bar"), "Foo-Bar")
        self.assertEqual(canonical_slug_case_sensitive("Foo--Bar"), "Foo-Bar")

    def test_strips_dashes_and_whitespace(self) -> None:
        self.assertEqual(canonical_slug_case_sensitive("---Foo---"), "Foo")
        self.assertEqual(canonical_slug_case_sensitive("   Foo   "), "Foo")

    def test_empty_returns_unknown(self) -> None:
        for value in ("", "   ", "---", "///"):
            self.assertEqual(canonical_slug_case_sensitive(value), "unknown")

    def test_unicode_and_nul_collapse(self) -> None:
        # Unicode collapses regardless of case; NUL byte collapses too.
        self.assertEqual(canonical_slug_case_sensitive("Café"), "Caf")
        self.assertEqual(canonical_slug_case_sensitive("Foo\x00Bar"), "Foo-Bar")

    def test_dotted_name_preserved(self) -> None:
        # C# regression: dotted namespace + case-distinct member.
        self.assertEqual(
            canonical_slug_case_sensitive("ShareX.HelpersLib.Native"),
            "ShareX.HelpersLib.Native",
        )

    def test_deterministic(self) -> None:
        for value in ("HelloWorld", "Foo/Bar", "SIZE", "Size", "  spaces  "):
            self.assertEqual(
                canonical_slug_case_sensitive(value),
                canonical_slug_case_sensitive(value),
            )

    def test_total_never_raises(self) -> None:
        for value in ("", " ", "\x00", "‮", "X" * 1000, "----", "."):
            try:
                canonical_slug_case_sensitive(value)
            except Exception as exc:  # pragma: no cover - failure path
                self.fail(
                    f"canonical_slug_case_sensitive raised on {value!r}: {exc!r}"
                )


class FileIdTest(unittest.TestCase):
    """Edge-case coverage for :func:`file_id`."""

    def test_basic_python_module(self) -> None:
        self.assertEqual(
            file_id("weld/strategies/python_module.py"),
            "file:weld/strategies/python_module",
        )

    def test_underscored_python_module(self) -> None:
        # The _ros2_py.py case from the bug report.
        self.assertEqual(
            file_id("weld/strategies/_ros2_py.py"),
            "file:weld/strategies/_ros2_py",
        )

    def test_top_level_module(self) -> None:
        self.assertEqual(file_id("weld/_node_ids.py"), "file:weld/_node_ids")

    def test_markdown_doc(self) -> None:
        self.assertEqual(
            file_id("docs/adrs/0041-graph-closure-determinism.md"),
            "file:docs/adrs/0041-graph-closure-determinism",
        )

    def test_no_extension(self) -> None:
        # File IDs preserve on-disk case (vjxi.6); see
        # ``FileIdCasePreservationTest`` below for the full contract.
        self.assertEqual(file_id("README"), "file:README")

    def test_pure_posix_path(self) -> None:
        # PurePosixPath inputs pass through identically to str inputs.
        self.assertEqual(
            file_id(PurePosixPath("weld/_node_ids.py")),
            file_id("weld/_node_ids.py"),
        )

    def test_windows_separator(self) -> None:
        # Backslashes normalise to forward slashes.
        self.assertEqual(
            file_id("weld\\strategies\\python_module.py"),
            "file:weld/strategies/python_module",
        )

    def test_stem_collision_across_directories(self) -> None:
        # Same stem, different dirs: canonical IDs MUST differ.
        a = file_id("weld/strategies/python_module.py")
        b = file_id("tools/python_module.py")
        self.assertNotEqual(a, b)
        self.assertEqual(a, "file:weld/strategies/python_module")
        self.assertEqual(b, "file:tools/python_module")

    def test_path_traversal_does_not_escape_namespace(self) -> None:
        # ``..`` segments are permitted; the ``file:`` prefix namespaces
        # the result so it cannot escape into another ID class. The ID
        # is never used to open a file on disk; it is only a graph key.
        result = file_id("../etc/passwd")
        self.assertTrue(result.startswith("file:"))
        self.assertEqual(result.split(":", 1)[0], "file")
        self.assertIn("etc/passwd", result)

    def test_nul_byte_collapses(self) -> None:
        # NUL byte in the path collapses; cannot terminate the ID early.
        self.assertEqual(file_id("foo\x00bar/baz.py"), "file:foo-bar/baz")

    def test_empty_returns_unknown(self) -> None:
        self.assertEqual(file_id(""), "file:unknown")

    def test_multi_dot_extension(self) -> None:
        # foo.tar.gz strips only the final extension.
        self.assertEqual(file_id("dist/foo.tar.gz"), "file:dist/foo.tar")

    def test_init_module(self) -> None:
        # __init__.py paths keep the directory and the underscore-stem.
        self.assertEqual(file_id("weld/__init__.py"), "file:weld/__init__")

    def test_leading_and_consecutive_slashes(self) -> None:
        # Empty segments (from leading slash, double slash) are dropped.
        self.assertEqual(file_id("/weld/x.py"), "file:weld/x")
        self.assertEqual(file_id("weld//x.py"), "file:weld/x")

    def test_deterministic(self) -> None:
        for value in ("a/b.py", "weld/_node_ids.py", "x"):
            self.assertEqual(file_id(value), file_id(value))


class FileIdCasePreservationTest(unittest.TestCase):
    """File IDs preserve on-disk case (vjxi.6). POSIX filesystems are
    case-sensitive, so case-variant paths must mint distinct IDs.
    Complement of ADR 0060: ``package:*`` IDs case-fold (namespaces are
    case-insensitive); paths do not. Punctuation rules unchanged.
    """

    def test_preserves_case_in_segments(self) -> None:
        self.assertEqual(
            file_id("ShareX.HelpersLib/CLI/CliCommand.cs"),
            "file:ShareX.HelpersLib/CLI/CliCommand",
        )

    def test_case_variant_paths_yield_distinct_ids(self) -> None:
        # Live regression from the 2026-05-15 ShareX dogfood pass:
        # polyrepo federation (ADR 0064 § 5) must not collapse cases.
        self.assertNotEqual(file_id("Foo.cs"), file_id("foo.cs"))
        self.assertEqual(file_id("Foo.cs"), "file:Foo")
        self.assertEqual(file_id("foo.cs"), "file:foo")
        a = file_id("ShareX.HelpersLib/CLI/CliCommand.cs")
        b = file_id("sharex.helperslib/cli/clicommand.cs")
        self.assertNotEqual(a, b)
        self.assertEqual(a, "file:ShareX.HelpersLib/CLI/CliCommand")
        self.assertEqual(b, "file:sharex.helperslib/cli/clicommand")

    def test_capitalised_top_level_files(self) -> None:
        for path, expected in (
            ("README.md", "file:README"),
            ("BUILD", "file:BUILD"),
            ("Makefile", "file:Makefile"),
            ("Dockerfile", "file:Dockerfile"),
            ("CLAUDE.md", "file:CLAUDE"),
        ):
            self.assertEqual(file_id(path), expected)

    def test_packages_case_fold_and_unicode_collapse(self) -> None:
        # Regression guards: file-id fix must not leak into package
        # case-folding (ADR 0060 + 02b2c3e); unicode still collapses.
        self.assertEqual(
            package_id("csharp", "ShareX.HelpersLib"),
            "package:csharp:sharex.helperslib",
        )
        self.assertEqual(file_id("café/foo.py"), "file:caf/foo")


class PackageIdTest(unittest.TestCase):
    """Edge-case coverage for :func:`package_id`."""

    def test_with_language(self) -> None:
        self.assertEqual(package_id("python", "mypkg"), "package:python:mypkg")
        self.assertEqual(package_id("ros2", "rclpy"), "package:ros2:rclpy")

    def test_lowercases_name(self) -> None:
        self.assertEqual(package_id("csharp", "MyProject"), "package:csharp:myproject")

    def test_without_language(self) -> None:
        self.assertEqual(package_id(None, "third-party"), "package:third-party")
        self.assertEqual(package_id("", "third-party"), "package:third-party")

    def test_dotted_package_name_preserved(self) -> None:
        # Python dotted packages keep dots through the slug.
        self.assertEqual(
            package_id("python", "foo.bar.baz"),
            "package:python:foo.bar.baz",
        )

    def test_collapses_disallowed_chars(self) -> None:
        self.assertEqual(
            package_id("python", "weird name"),
            "package:python:weird-name",
        )

    def test_empty_name_uses_unknown(self) -> None:
        self.assertEqual(package_id("python", ""), "package:python:unknown")

    def test_deterministic(self) -> None:
        for lang, name in [("python", "mypkg"), (None, "x"), ("ros2", "Two_Words")]:
            self.assertEqual(package_id(lang, name), package_id(lang, name))


class EntityIdTest(unittest.TestCase):
    """Edge-case coverage for :func:`entity_id`."""

    def test_skill_with_platform(self) -> None:
        self.assertEqual(
            entity_id("skill", platform="generic", name="architecture-decision"),
            "skill:generic:architecture-decision",
        )

    def test_agent_with_platform(self) -> None:
        self.assertEqual(
            entity_id("agent", platform="claude", name="reviewer"),
            "agent:claude:reviewer",
        )

    def test_topic_without_platform(self) -> None:
        # Leading slash collapses to a dash, then strips per the slug
        # rule, yielding the bare canonical form expected in ADR 0041.
        self.assertEqual(
            entity_id("topic", platform=None, name="/cmd_vel"),
            "topic:cmd_vel",
        )

    def test_empty_name_uses_unknown(self) -> None:
        self.assertEqual(
            entity_id("skill", platform="generic", name=""),
            "skill:generic:unknown",
        )

    def test_no_path_hashed_suffix(self) -> None:
        # Two callers passing the same logical entity get the same ID,
        # without any sha1-suffix disambiguator. ADR 0041 explicitly
        # removes the path-hashed suffix from the legacy
        # ``_node_id_for_values`` helper.
        a = entity_id("skill", platform="generic", name="architecture-decision")
        b = entity_id("skill", platform="generic", name="architecture-decision")
        self.assertEqual(a, b)
        # Ensure no SHA1-shaped 8-hex tail was added.
        self.assertNotRegex(a, r":[0-9a-f]{8}$")

    def test_lowercases_platform_and_name(self) -> None:
        self.assertEqual(
            entity_id("skill", platform="GENERIC", name="ABC"),
            "skill:generic:abc",
        )

    def test_node_type_lowercased(self) -> None:
        self.assertEqual(
            entity_id("Skill", platform="generic", name="x"),
            "skill:generic:x",
        )

    def test_deterministic(self) -> None:
        for nt, plat, name in [
            ("skill", "generic", "x"),
            ("agent", None, "y"),
            ("topic", "ros2", "/cmd_vel"),
        ]:
            self.assertEqual(
                entity_id(nt, platform=plat, name=name),
                entity_id(nt, platform=plat, name=name),
            )


if __name__ == "__main__":
    unittest.main()
