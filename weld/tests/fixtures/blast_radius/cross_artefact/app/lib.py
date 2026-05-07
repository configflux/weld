"""Library used by the entrypoint, the test, and packaged into the image."""


def greet(name: str) -> str:
    return f"hello, {name}"
