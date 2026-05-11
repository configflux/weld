# Tiny fixture module for the public-benchmark smoke corpus (repo_a).
# Intentionally trivial so adapters can be tested deterministically.


class Store:
    """Toy Store entity."""

    def __init__(self, name: str) -> None:
        self.name = name

    def get(self, key: str) -> str:
        return f"{self.name}:{key}"


def make_store(name: str) -> Store:
    return Store(name)
