"""Entrypoint that consumes the library."""
from src.lib import double


def run() -> int:
    return double(21)


if __name__ == "__main__":
    print(run())
