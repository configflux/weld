"""Tests for the ``csharp_test_framework`` strategy (ADR 0056 Wave 2)."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.contract import CONFIDENCE_VALUES, VALID_NODE_TYPES
from weld.strategies._helpers import StrategyResult
from weld.strategies.csharp_test_framework import (
    _detect_class_framework,
    _detect_method_framework,
    _test_suite_id,
    extract,
)


class FrameworkDetectorsTest(unittest.TestCase):
    """The attribute -> framework lookup must be exact-name."""

    def test_method_xunit_fact(self) -> None:
        self.assertEqual(_detect_method_framework(["Fact"]), "xunit")

    def test_method_xunit_theory(self) -> None:
        self.assertEqual(_detect_method_framework(["Theory"]), "xunit")

    def test_method_nunit(self) -> None:
        self.assertEqual(_detect_method_framework(["Test"]), "nunit")

    def test_method_mstest(self) -> None:
        self.assertEqual(_detect_method_framework(["TestMethod"]), "mstest")

    def test_class_nunit_testfixture(self) -> None:
        self.assertEqual(
            _detect_class_framework(["TestFixture"]), "nunit",
        )

    def test_class_mstest_testclass(self) -> None:
        self.assertEqual(_detect_class_framework(["TestClass"]), "mstest")

    def test_unrelated_attribute_returns_none(self) -> None:
        # ``[Factory]`` must not be classified as ``Fact``; the regex
        # uses a word boundary specifically for this case.
        self.assertIsNone(_detect_method_framework(["Factory"]))
        self.assertIsNone(_detect_class_framework(["Serializable"]))


class TestSuiteIdTest(unittest.TestCase):
    """ID-shape contract."""

    def test_id_with_namespace(self) -> None:
        self.assertEqual(
            _test_suite_id("Sample.Tests", "OrdersControllerTests"),
            "test-suite:Sample.Tests.OrdersControllerTests",
        )

    def test_id_without_namespace(self) -> None:
        self.assertEqual(
            _test_suite_id("", "OrphanTests"),
            "test-suite:OrphanTests",
        )


class XUnitExtractTest(unittest.TestCase):
    """xUnit ``[Fact]``/``[Theory]`` detection."""

    def _write(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")

    def test_fact_method_emits_test_suite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "tests" / "FooTests.cs",
                """\
                using Xunit;

                namespace Sample.Tests;

                public class FooTests
                {
                    [Fact]
                    public void Bar_returns_value() { }
                }
                """,
            )
            result = extract(root, {"glob": "**/*.cs"}, {})

            self.assertIsInstance(result, StrategyResult)
            nid = "test-suite:Sample.Tests.FooTests"
            self.assertIn(nid, result.nodes)
            node = result.nodes[nid]
            self.assertEqual(node["type"], "test-suite")
            self.assertIn("test-suite", VALID_NODE_TYPES)
            self.assertEqual(node["props"]["test_framework"], "xunit")
            self.assertEqual(node["props"]["methods"], ["Bar_returns_value"])
            self.assertEqual(node["props"]["namespace"], "Sample.Tests")
            self.assertEqual(node["props"]["class_name"], "FooTests")
            self.assertEqual(node["props"]["confidence"], "definite")
            self.assertIn("test", node["props"]["roles"])

    def test_theory_method_classifies_as_xunit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "tests" / "BarTests.cs",
                """\
                using Xunit;
                namespace Sample.Tests;

                public class BarTests
                {
                    [Theory]
                    [InlineData(1)]
                    public void Param_test(int x) { }
                }
                """,
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            node = result.nodes["test-suite:Sample.Tests.BarTests"]
            self.assertEqual(node["props"]["test_framework"], "xunit")
            self.assertEqual(node["props"]["methods"], ["Param_test"])


class NUnitExtractTest(unittest.TestCase):
    """NUnit ``[Test]``/``[TestFixture]`` detection."""

    def test_test_method_classifies_as_nunit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "NUnitTests.cs").write_text(
                textwrap.dedent("""\
                using NUnit.Framework;
                namespace Suite;

                [TestFixture]
                public class NUnitTests
                {
                    [Test]
                    public void Method_one() { }

                    [Test]
                    public void Method_two() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            node = result.nodes["test-suite:Suite.NUnitTests"]
            self.assertEqual(node["props"]["test_framework"], "nunit")
            self.assertEqual(
                node["props"]["methods"], ["Method_one", "Method_two"],
            )


class MSTestExtractTest(unittest.TestCase):
    """MSTest ``[TestClass]``/``[TestMethod]`` detection."""

    def test_testmethod_classifies_as_mstest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "MsTests.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.VisualStudio.TestTools.UnitTesting;
                namespace MsSuite;

                [TestClass]
                public class MsTests
                {
                    [TestMethod]
                    public void Should_pass() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            node = result.nodes["test-suite:MsSuite.MsTests"]
            self.assertEqual(node["props"]["test_framework"], "mstest")
            self.assertEqual(node["props"]["methods"], ["Should_pass"])


class EdgeContractTest(unittest.TestCase):
    """Every edge must satisfy ADR 0050 confidence + the ADR 0046 seam."""

    def test_contains_edge_to_test_file_is_emitted(self) -> None:
        # The ``test-suite -> contains -> file:`` edge is the join seam
        # consumed by ADR 0046 test-peer downstream consumers.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "FooTests.cs").write_text(
                textwrap.dedent("""\
                using Xunit;
                namespace Suite;
                public class FooTests
                {
                    [Fact]
                    public void M() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            edge = next(e for e in result.edges if e["type"] == "contains")
            self.assertEqual(edge["from"], "test-suite:Suite.FooTests")
            # ``file_id`` canonicalises path segments via
            # ``canonical_slug_case_sensitive`` (vjxi.6), preserving the
            # on-disk case so case-variant files on POSIX filesystems
            # mint distinct IDs.
            self.assertEqual(edge["to"], "file:FooTests")
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertEqual(edge["props"]["test_framework"], "xunit")
            self.assertEqual(
                edge["props"]["source_strategy"], "csharp_test_framework",
            )

    def test_every_emitted_edge_has_valid_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "TestsFile.cs").write_text(
                textwrap.dedent("""\
                using Xunit;
                namespace S;
                public class TestsFile
                {
                    [Fact] public void A() { }
                    [Fact] public void B() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            for edge in result.edges:
                self.assertEqual(edge["props"]["confidence"], "definite")
                self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)


class RobustnessTest(unittest.TestCase):
    """Pathological inputs must not crash discovery."""

    def test_non_test_class_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Plain.cs").write_text(
                textwrap.dedent("""\
                namespace App;
                public class Plain
                {
                    public void Method() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_factory_attribute_does_not_match_fact(self) -> None:
        # ``[FactoryAttribute]`` and ``[Factory]`` share a prefix with
        # ``Fact``; the regex word boundary must not classify them.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "X.cs").write_text(
                textwrap.dedent("""\
                namespace App;
                public class X
                {
                    [Factory]
                    public void M() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})

    def test_file_with_two_test_classes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Two.cs").write_text(
                textwrap.dedent("""\
                using Xunit;
                namespace S;
                public class AlphaTests
                {
                    [Fact] public void A() { }
                }
                public class BetaTests
                {
                    [Fact] public void B() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertIn("test-suite:S.AlphaTests", result.nodes)
            self.assertIn("test-suite:S.BetaTests", result.nodes)

    def test_unreadable_file_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Bad.cs").write_bytes(b"\xff\xfe\xfd not utf-8")
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})

    def test_empty_directory_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])


class DeterminismTest(unittest.TestCase):
    """Two consecutive runs on the same tree produce identical output."""

    def test_method_list_is_sorted(self) -> None:
        # The strategy preserves discovery order while writing, then
        # sorts before emit. Methods declared as "Z, A" must come out
        # as "A, Z".
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "X.cs").write_text(
                textwrap.dedent("""\
                using Xunit;
                namespace S;
                public class XTests
                {
                    [Fact] public void Z_method() { }
                    [Fact] public void A_method() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            methods = result.nodes["test-suite:S.XTests"]["props"]["methods"]
            self.assertEqual(methods, ["A_method", "Z_method"])

    def test_consecutive_runs_yield_identical_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "X.cs").write_text(
                textwrap.dedent("""\
                using Xunit;
                namespace S;
                public class XTests
                {
                    [Fact] public void M() { }
                }
                """),
                encoding="utf-8",
            )
            first = extract(root, {"glob": "**/*.cs"}, {})
            second = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(first.nodes, second.nodes)
            self.assertEqual(first.edges, second.edges)


if __name__ == "__main__":
    unittest.main()
