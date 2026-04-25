"""Bundled Weld demo bootstrap scripts.

The shell scripts under :mod:`weld.demos.scripts` are byte-identical
copies of the canonical scripts under ``scripts/`` at the repository
root. They are vendored here so ``wd demo`` keeps working when Weld is
installed from a wheel (where the repo root ``scripts/`` directory is
not present).

A regression test (``weld_demos_scripts_parity_test``) keeps the two
copies in lockstep.
"""
