"""Library module under test."""


def add(a: int, b: int) -> int:
    return a + b


def double(x: int) -> int:
    return add(x, x)
