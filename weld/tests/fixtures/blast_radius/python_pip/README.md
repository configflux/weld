# Fixture: python_pip

Tiny pip-style Python repo. Three source files plus a filename
test peer:

```
python_pip/
  pyproject.toml
  requirements.txt
  src/
    lib.py        # add(), double()
    main.py       # run() -> double(21)
    tests/
      lib_test.py # test_add, test_double
```

## Strategies wired

- `python_module` -- top-level functions/classes per file.
- `python_callgraph` -- intra-module + import-table call edges.
- `test_peer` -- surfaces `src/tests/*_test.py` as test nodes and
  emits a `tests` edge to the production peer
  (`src/tests/lib_test.py --tests--> src/lib.py`).

## Documented seed

- `impact_lib` (`target.input = src/lib.py`, depth=3): a change to
  the library reverse-traverses through `main` (`depends_on`) and
  through the test peer (`tests`), and through the symbol-level
  `calls` edges produced by `python_callgraph`. This is the
  baseline pip-style "what does my edit affect" scenario.

## Notes

- The test stem follows the canonical `<area>_test.py` convention so
  `test_peer` resolves the production peer via its filesystem probe.
- No Bazel files in this fixture; see `bazel_python` for that
  combination.
