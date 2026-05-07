"""Bazel py_test for the lib module."""
import unittest

from app.lib import greet


class GreetTest(unittest.TestCase):
    def test_greet(self) -> None:
        self.assertEqual(greet("weld"), "hello, weld")


if __name__ == "__main__":
    unittest.main()
