"""Service entrypoint -- COPY'd into the Dockerfile."""
from app.lib import greet


def run() -> str:
    return greet("weld")


if __name__ == "__main__":
    print(run())
