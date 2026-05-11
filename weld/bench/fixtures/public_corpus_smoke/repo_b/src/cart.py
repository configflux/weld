# Tiny fixture module for the public-benchmark smoke corpus (repo_b).


class Cart:
    """Toy shopping cart."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, item: str) -> None:
        self.items.append(item)

    def total(self) -> int:
        return len(self.items)
