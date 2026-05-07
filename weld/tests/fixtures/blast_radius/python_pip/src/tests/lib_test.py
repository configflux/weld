"""Tests for the lib module (filename test_peer convention)."""
from src.lib import add, double


def test_add() -> None:
    assert add(2, 3) == 5


def test_double() -> None:
    assert double(7) == 14
