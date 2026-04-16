"""Module entrypoint for ``python -m weld`` compatibility."""

from __future__ import annotations

import sys

from weld.cli import main

if __name__ == "__main__":
    sys.exit(main())
