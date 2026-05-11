# Caller of Store used to exercise callgraph/dependency adapters.

from .store import Store, make_store


def consume() -> str:
    s = make_store("alpha")
    return s.get("k")


def consume_explicit() -> Store:
    return Store("beta")
