"""The ``tsconfig`` alias map (bd lrnx1.4, ADR 0142 D3, second half).

``@/lib/greeting`` is not a package -- it is ``src/lib/greeting`` spelled
through ``compilerOptions.paths``. Two properties of that spelling are what
these cases exist for, because getting either wrong turns a fix into a new
class of wrong edge:

* an alias is **scoped** to the config that declares it, so two Next.js apps
  in one monorepo may give ``@/*`` two different meanings and each importer
  must get its own;
* ``paths`` is a **pattern language** with a documented tie-break -- longest
  literal prefix, exact keys first -- not a dictionary lookup.

The confinement cases are here for the same reason as in the workspace-member
suite: a ``tsconfig.json`` is a file in someone else's repository.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.strategies._ts_tsconfig_paths import (
    alias_map,
    alias_targets,
    nearest_alias_map,
    read_config,
    resolve_alias,
    strip_jsonc,
)


def write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def config(paths: dict, base_url: str | None = None) -> str:
    options: dict = {"paths": paths}
    if base_url is not None:
        options["baseUrl"] = base_url
    return json.dumps({"compilerOptions": options})


class Jsonc(unittest.TestCase):
    """``tsc --init`` writes a file strict JSON cannot read."""

    def test_line_and_block_comments_and_trailing_commas_are_tolerated(self) -> None:
        text = """{
          // the alias every create-next-app writes
          "compilerOptions": {
            /* block */
            "baseUrl": ".",
            "paths": {"@/*": ["src/*"]},
          },
        }"""
        self.assertEqual(
            json.loads(strip_jsonc(text))["compilerOptions"]["paths"],
            {"@/*": ["src/*"]},
        )

    def test_comment_markers_inside_string_values_survive(self) -> None:
        text = '{"a": "http://x/y", "b": "/* not a comment */"}'
        self.assertEqual(json.loads(strip_jsonc(text)), json.loads(text))

    def test_an_escaped_quote_does_not_end_the_string(self) -> None:
        text = r'{"a": "he said \" // not a comment", "b": 1}'
        self.assertEqual(json.loads(strip_jsonc(text)), json.loads(text))

    def test_a_value_that_looks_like_a_trailing_comma_is_not_rewritten(self) -> None:
        """The failure a regex-based comma pass makes: silent data loss.

        ``", }"`` inside a string matches the obvious regex, and rewriting it
        leaves a config that still parses and no longer says what it said.
        """
        text = '{"a": "one, } two", "b": [1, 2,],}'
        self.assertEqual(
            json.loads(strip_jsonc(text)), {"a": "one, } two", "b": [1, 2]},
        )

    def test_a_comma_before_a_nested_close_is_dropped(self) -> None:
        text = '{"outer": {"inner": [1,],},}'
        self.assertEqual(json.loads(strip_jsonc(text)), {"outer": {"inner": [1]}})

    def test_read_config_accepts_both_strict_and_commented_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            strict = write(root, "strict.json", '{"compilerOptions": {}}')
            loose = write(root, "loose.json", '{"a": 1, /* c */ "b": 2,}')
            self.assertEqual(read_config(strict), {"compilerOptions": {}})
            self.assertEqual(read_config(loose), {"a": 1, "b": 2})
            self.assertEqual(read_config(write(root, "bad.json", "{{{")), {})


class AliasMapBuilding(unittest.TestCase):
    def test_a_config_without_paths_is_not_an_answer(self) -> None:
        """``None``, not an empty map -- the caller must keep walking up."""
        self.assertIsNone(alias_map("apps/web", {}))
        self.assertIsNone(alias_map("apps/web", {"compilerOptions": {}}))
        self.assertIsNone(
            alias_map("apps/web", {"compilerOptions": {"paths": {}}})
        )

    def test_base_url_is_applied_to_the_config_directory(self) -> None:
        built = alias_map(
            "apps/web",
            json.loads(config({"@/*": ["src/*"]}, base_url=".")),
        )
        assert built is not None
        self.assertEqual(built.base, "apps/web")
        self.assertEqual(alias_targets(built, "@/lib/greeting"),
                         ["apps/web/src/lib/greeting"])

    def test_without_a_base_url_targets_resolve_against_the_config(self) -> None:
        built = alias_map("apps/web", json.loads(config({"@/*": ["src/*"]})))
        assert built is not None
        self.assertEqual(built.base, "apps/web")

    def test_a_base_url_climbing_out_of_the_repository_is_refused(self) -> None:
        self.assertIsNone(alias_map(
            "apps/web", json.loads(config({"@/*": ["src/*"]}, base_url="../../.."))
        ))

    def test_a_target_climbing_out_of_the_repository_is_dropped(self) -> None:
        built = alias_map("apps/web", json.loads(config({
            "@evil/*": ["../../../../etc/*"],
            "@ok/*": ["src/*"],
        })))
        assert built is not None
        self.assertEqual(alias_targets(built, "@evil/passwd"), [])
        self.assertEqual(alias_targets(built, "@ok/lib"), ["apps/web/src/lib"])

    def test_a_target_reaching_a_sibling_package_is_kept(self) -> None:
        """``../../packages/shared/src/*`` is legitimate and common."""
        built = alias_map("apps/web", json.loads(config({
            "@shared/*": ["../../packages/shared/src/*"],
        })))
        assert built is not None
        self.assertEqual(
            alias_targets(built, "@shared/money"), ["packages/shared/src/money"]
        )

    def test_a_pattern_with_two_stars_is_refused(self) -> None:
        built = alias_map("", json.loads(config({"a/*/*": ["x/*"], "b/*": ["y/*"]})))
        assert built is not None
        self.assertEqual([pattern for pattern, _ in built.patterns], ["b/*"])


class PatternPrecedence(unittest.TestCase):
    def test_the_longest_literal_prefix_wins(self) -> None:
        built = alias_map("", json.loads(config({
            "@/*": ["src/*"],
            "@/lib/*": ["shared/lib/*"],
        })))
        assert built is not None
        self.assertEqual(alias_targets(built, "@/lib/greeting"),
                         ["shared/lib/greeting"])
        self.assertEqual(alias_targets(built, "@/app/page"), ["src/app/page"])

    def test_an_exact_key_beats_every_wildcard(self) -> None:
        built = alias_map("", json.loads(config({
            "@/lib/*": ["wild/*"],
            "@/lib/greeting": ["exact/greeting.ts"],
        })))
        assert built is not None
        self.assertEqual(alias_targets(built, "@/lib/greeting"),
                         ["exact/greeting.ts"])

    def test_a_specifier_cannot_climb_out_through_the_substitution(self) -> None:
        """The one input a *source file* controls, not a config.

        ``@/*`` is a benign alias; what the ``*`` stands for is whatever an
        import statement in the repository says. A specifier that spells its
        way up and out has to be refused after substitution, not before.
        """
        built = alias_map("apps/web", json.loads(config({"@/*": ["src/*"]}, ".")))
        assert built is not None
        self.assertEqual(alias_targets(built, "@/../../../../etc/passwd"), [])
        self.assertEqual(alias_targets(built, "@//etc/passwd"), ["apps/web/src/etc/passwd"])
        self.assertEqual(
            alias_targets(built, "@/../lib/x"), ["apps/web/lib/x"],
        )

    def test_a_specifier_matching_nothing_answers_nothing(self) -> None:
        built = alias_map("", json.loads(config({"@/*": ["src/*"]})))
        assert built is not None
        self.assertEqual(alias_targets(built, "react"), [])
        self.assertEqual(alias_targets(built, "@acme/shared"), [])

    def test_every_target_of_the_winning_pattern_is_offered_in_order(self) -> None:
        built = alias_map("", json.loads(config({"@/*": ["src/*", "generated/*"]})))
        assert built is not None
        self.assertEqual(alias_targets(built, "@/lib"), ["src/lib", "generated/lib"])


class NearestConfigWins(unittest.TestCase):
    def _two_apps(self, root: Path) -> None:
        write(root, "apps/web/tsconfig.json", config({"@/*": ["src/*"]}, "."))
        write(root, "apps/web/src/lib/greeting.ts", "export const g = 1;\n")
        write(root, "apps/admin/tsconfig.json", config({"@/*": ["src/*"]}, "."))
        write(root, "apps/admin/src/lib/greeting.ts", "export const g = 2;\n")

    def test_one_spelling_answers_differently_in_two_apps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._two_apps(root)
            cache: dict = {}
            for app in ("web", "admin"):
                aliases = nearest_alias_map(
                    root, f"apps/{app}/src/app/page.ts", cache)
                assert aliases is not None
                self.assertEqual(
                    resolve_alias(root, aliases, "@/lib/greeting"),
                    f"apps/{app}/src/lib/greeting.ts",
                )

    def test_a_config_without_paths_does_not_stop_the_walk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._two_apps(root)
            write(root, "apps/web/src/app/tsconfig.json",
                  json.dumps({"compilerOptions": {"strict": True}}))
            aliases = nearest_alias_map(root, "apps/web/src/app/page.ts", {})
            assert aliases is not None
            self.assertEqual(
                resolve_alias(root, aliases, "@/lib/greeting"),
                "apps/web/src/lib/greeting.ts",
            )

    def test_a_repository_with_no_config_answers_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                nearest_alias_map(Path(tmp), "src/app/page.ts", {})
            )

    def test_jsconfig_is_read_when_no_tsconfig_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "jsconfig.json", config({"@/*": ["src/*"]}, "."))
            write(root, "src/lib/greeting.js", "module.exports = {};\n")
            aliases = nearest_alias_map(root, "src/app/page.js", {})
            assert aliases is not None
            self.assertEqual(
                resolve_alias(root, aliases, "@/lib/greeting"),
                "src/lib/greeting.js",
            )

    def test_the_directory_cache_is_reused_across_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._two_apps(root)
            cache: dict = {}
            nearest_alias_map(root, "apps/web/src/app/page.ts", cache)
            self.assertIn("apps/web", cache)
            before = dict(cache)
            nearest_alias_map(root, "apps/web/src/app/other.ts", cache)
            self.assertEqual({k: v for k, v in cache.items() if k in before}, before)

    def test_an_alias_naming_no_file_resolves_to_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._two_apps(root)
            aliases = nearest_alias_map(root, "apps/web/src/app/page.ts", {})
            assert aliases is not None
            self.assertEqual(resolve_alias(root, aliases, "@/lib/absent"), "")


if __name__ == "__main__":
    unittest.main()
