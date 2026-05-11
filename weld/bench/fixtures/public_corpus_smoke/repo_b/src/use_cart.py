# Caller of Cart used to exercise callgraph adapters.

from .cart import Cart


def use_cart() -> int:
    c = Cart()
    c.add("apple")
    return c.total()
