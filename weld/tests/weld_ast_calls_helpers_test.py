"""Unit tests for the shared call-site AST-match primitives.

Covers :mod:`weld.strategies._ast_calls`, the module the ``events_callsite``,
``events_bindings``, and ``http_client`` strategies share. These are the
building blocks whose behavior the strategies' byte-identity goldens depend
on, so each primitive is pinned directly here.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from weld.strategies._ast_calls import (
    attribute_call_target,
    classify_receiver_verb,
    file_imports_root,
    iter_call_nodes,
    iter_python_asts,
    literal_first_arg,
    literal_str_or_list_arg,
    literal_string,
)

def _expr(src: str) -> ast.AST:
    return ast.parse(src, mode="eval").body

def _call(src: str) -> ast.Call:
    node = ast.parse(src, mode="eval").body
    assert isinstance(node, ast.Call)
    return node

_ROOTS = frozenset(["kafka", "redis", "aiokafka"])

class FileImportsRootTest(unittest.TestCase):
    def test_plain_import_matches(self) -> None:
        self.assertTrue(file_imports_root(ast.parse("import kafka"), _ROOTS))

    def test_dotted_import_matches_first_segment(self) -> None:
        self.assertTrue(
            file_imports_root(ast.parse("import kafka.producer"), _ROOTS)
        )

    def test_from_import_matches(self) -> None:
        self.assertTrue(
            file_imports_root(ast.parse("from redis.asyncio import X"), _ROOTS)
        )

    def test_unrelated_import_does_not_match(self) -> None:
        self.assertFalse(file_imports_root(ast.parse("import os"), _ROOTS))

    def test_partial_segment_does_not_match(self) -> None:
        # ``kafkaesque`` shares a prefix but is a distinct top-level name.
        self.assertFalse(
            file_imports_root(ast.parse("import kafkaesque"), _ROOTS)
        )

class LiteralStringTest(unittest.TestCase):
    def test_plain_string_constant(self) -> None:
        self.assertEqual(literal_string(_expr('"hello"')), "hello")

    def test_literal_only_fstring(self) -> None:
        self.assertEqual(literal_string(_expr('f"abc"')), "abc")

    def test_substituting_fstring_is_none(self) -> None:
        self.assertIsNone(literal_string(_expr('f"a{x}b"')))

    def test_non_string_constant_is_none(self) -> None:
        self.assertIsNone(literal_string(_expr("123")))

    def test_name_node_is_none(self) -> None:
        self.assertIsNone(literal_string(_expr("x")))

class LiteralFirstArgTest(unittest.TestCase):
    def test_literal_first_arg(self) -> None:
        self.assertEqual(literal_first_arg(_call('f("hello", 1)')), "hello")

    def test_no_args_is_none(self) -> None:
        self.assertIsNone(literal_first_arg(_call("f()")))

    def test_dynamic_first_arg_is_none(self) -> None:
        self.assertIsNone(literal_first_arg(_call("f(x)")))

class LiteralStrOrListArgTest(unittest.TestCase):
    """Single-or-list literal extraction shared by the consumer strategies."""

    def test_single_string(self) -> None:
        self.assertEqual(literal_str_or_list_arg(_call('f("t")')), ["t"])

    def test_literal_list(self) -> None:
        self.assertEqual(
            literal_str_or_list_arg(_call('f(["a", "b"])')), ["a", "b"]
        )

    def test_no_args_is_none(self) -> None:
        self.assertIsNone(literal_str_or_list_arg(_call("f()")))

    def test_empty_single_string_is_none(self) -> None:
        self.assertIsNone(literal_str_or_list_arg(_call('f("")')))

    def test_empty_list_is_none(self) -> None:
        self.assertIsNone(literal_str_or_list_arg(_call("f([])")))

    def test_all_empty_elements_is_none(self) -> None:
        self.assertIsNone(literal_str_or_list_arg(_call('f(["", ""])')))

    def test_empty_elements_skipped(self) -> None:
        self.assertEqual(
            literal_str_or_list_arg(_call('f(["a", "", "b"])')), ["a", "b"]
        )

    def test_dynamic_single_arg_is_none(self) -> None:
        self.assertIsNone(literal_str_or_list_arg(_call("f(x)")))

    def test_non_literal_list_element_drops_whole_list(self) -> None:
        self.assertIsNone(literal_str_or_list_arg(_call('f(["a", x])')))

    def test_tuple_arg_is_none(self) -> None:
        # paho's ``subscribe(("topic", qos))`` tuple form is not a list.
        self.assertIsNone(literal_str_or_list_arg(_call('f(("t", 1))')))

    def test_literal_fstring_single_is_none(self) -> None:
        # Raw Constant check (not literal_string): f-strings do not qualify,
        # preserving events_bindings' pre-existing consumer behavior.
        self.assertIsNone(literal_str_or_list_arg(_call('f(f"t")')))

class AttributeCallTargetTest(unittest.TestCase):
    def test_name_receiver(self) -> None:
        self.assertEqual(attribute_call_target(_call('kafka.send("x")')), ("kafka", "send"))

    def test_bare_name_call_is_none(self) -> None:
        self.assertIsNone(attribute_call_target(_call('send("x")')))

    def test_deep_attribute_chain_is_none(self) -> None:
        # Receiver is an Attribute, not a bare Name -> out of scope.
        self.assertIsNone(attribute_call_target(_call('self._client.get("x")')))

class ClassifyReceiverVerbTest(unittest.TestCase):
    _RULES = (
        (frozenset(["kafka", "KafkaProducer"]), frozenset(["send", "produce"]), "kafka"),
        (frozenset(["redis"]), frozenset(["publish"]), "tcp"),
    )

    def test_matching_rule_returns_transport(self) -> None:
        self.assertEqual(classify_receiver_verb(_call('kafka.send("x")'), self._RULES), "kafka")

    def test_second_rule_matches(self) -> None:
        self.assertEqual(classify_receiver_verb(_call('redis.publish("x")'), self._RULES), "tcp")

    def test_known_root_unknown_verb_is_none(self) -> None:
        self.assertIsNone(classify_receiver_verb(_call('kafka.flush("x")'), self._RULES))

    def test_unknown_root_is_none(self) -> None:
        self.assertIsNone(classify_receiver_verb(_call('other.send("x")'), self._RULES))

    def test_bare_call_is_none(self) -> None:
        self.assertIsNone(classify_receiver_verb(_call('send("x")'), self._RULES))

class IterCallNodesTest(unittest.TestCase):
    def test_yields_nested_calls_in_walk_order(self) -> None:
        tree = ast.parse("def f():\n    return g(h(1))\n")
        funcs = [n.func.id for n in iter_call_nodes(tree) if isinstance(n.func, ast.Name)]
        self.assertEqual(sorted(funcs), ["g", "h"])

    def test_no_calls_yields_nothing(self) -> None:
        self.assertEqual(list(iter_call_nodes(ast.parse("x = 1\n"))), [])

class IterPythonAstsTest(unittest.TestCase):
    def _build(self, root: Path) -> None:
        (root / "a.py").write_text("import kafka\n")
        (root / "_c.py").write_text("import redis\n")
        (root / "b.txt").write_text("not python\n")
        (root / "bad.py").write_text("def broken(:\n")  # SyntaxError

    def test_default_includes_underscore_skips_nonpy_and_syntaxerror(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._build(root)
            got = {rel for rel, _tree in iter_python_asts(root, "*.py")}
            self.assertEqual(got, {"a.py", "_c.py"})

    def test_skip_underscore(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._build(root)
            got = {rel for rel, _tree in iter_python_asts(root, "*.py", skip_underscore=True)}
            self.assertEqual(got, {"a.py"})

    def test_yields_parsed_module(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text("import kafka\n")
            pairs = list(iter_python_asts(root, "*.py"))
            self.assertEqual(len(pairs), 1)
            rel, tree = pairs[0]
            self.assertEqual(rel, "a.py")
            self.assertIsInstance(tree, ast.Module)

    def test_recursive_glob(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pkg").mkdir()
            (root / "pkg" / "deep.py").write_text("import kafka\n")
            got = {rel for rel, _tree in iter_python_asts(root, "**/*.py")}
            self.assertEqual(got, {"pkg/deep.py"})

if __name__ == "__main__":
    unittest.main()
