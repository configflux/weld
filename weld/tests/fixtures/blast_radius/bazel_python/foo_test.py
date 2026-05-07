"""Bazel-style py_test for foo."""
import unittest

from foo import greet


class GreetTest(unittest.TestCase):
    def test_greet(self) -> None:
        self.assertEqual(greet("weld"), "hello, weld")


if __name__ == "__main__":
    unittest.main()
