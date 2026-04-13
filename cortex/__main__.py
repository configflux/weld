"""Module entrypoint for ``python -m cortex`` compatibility."""

from __future__ import annotations

import sys

from cortex.cli import main

if __name__ == "__main__":
    sys.exit(main())
