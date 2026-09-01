"""The traversal half of the query-quality eval corpus (ADR 0113, bd 2gvr).

Split out of :mod:`weld.tests.query_corpus` to keep both files under the repo
line-count cap. That module keeps the ranking half and re-exports
``TRAVERSAL_CORPUS``, so the two gates that read the corpus keep importing the
whole of it from one place.

Reported gaps whose subject is a *traversal*, not a ranking.

``CORPUS`` next door pins queries: text goes in, ranked ids come out, and the
adversarial ``concept:`` population is what keeps a reported query from
answering itself. Some reported gaps are not questions of text at all. "Who
calls this symbol" is answered by walking edges, and it can be wrong -- can
answer "no callers" for a symbol with many, or name a caller of a function
that does not exist -- while every ranking assertion over there still passes.

Such an entry is pinned by running the verb the reporter ran, over source
built to the shape they hit, through real discovery. That is what makes it
immune to the bd 9ucf self-heal by construction rather than by assertion: a
node minted from an issue title is a ``concept``, and a ``concept`` is never
the source of a ``calls`` edge, so no amount of backlog can satisfy one of
these. It is also why the gap issues behind the entries below mint no node in
``CONCEPT_NODES``: their titles quote no query, so adding them would shift
BM25 for every ranking entry without adding an adversary to any of them --
the same reason bd 9ucf's own title is deliberately absent there.

* ``sources``/``glob`` -- the tree to discover, ``relative/path.py`` -> file
  body, and the glob the two Python strategies are wired to.
* ``verb``/``argument`` -- the read command, exactly as reported.
* ``must_answer_with`` -- ids the answer must name.
* ``must_not_exist`` -- ids the graph must not hold at all.
"""

from __future__ import annotations

TRAVERSAL_CORPUS: tuple[dict, ...] = (
    {
        "bd": "gkpqa",
        "question": (
            "callers of a symbol whose call sites import it from a "
            "re-export facade"
        ),
        "verb": "callers",
        "argument": "symbol:py:pkg.definer:widget",
        "glob": "pkg/**/*.py",
        "sources": {
            "pkg/__init__.py": "",
            "pkg/definer.py": "def widget():\n    return 1\n",
            "pkg/facade.py": (
                '"""Public import path; the implementation lives next door."""\n'
                "\nfrom pkg.definer import widget\n\n"
                '__all__ = ["widget"]\n'
            ),
            "pkg/caller.py": (
                "from pkg.facade import widget\n\n\n"
                "def run():\n    return widget()\n"
            ),
        },
        "must_answer_with": ["symbol:py:pkg.caller:run"],
        "must_not_exist": ["symbol:py:pkg.facade:widget"],
        "why": (
            "Reported against this repo's own weld/contract.py, which defines "
            "no validators and re-exports the whole family from private "
            "siblings. Every real consumer takes that documented public "
            "import path, so python_callgraph resolved each call against the "
            "caller's import table onto symbol:py:weld.contract:<name> -- a "
            "module that defines nothing -- and wd callers on the actual "
            "definition answered 'no callers'. The blast radius of changing a "
            "re-exported symbol read as empty. A control one line away in the "
            "same function, imported straight from its defining module, "
            "resolved, which is what ruled out staleness and ranking. The "
            "must_not_exist half matters as much as the answer: leaving the "
            "stub behind would keep a node asserting that the facade defines "
            "a name it only forwards."
        ),
    },
    {
        "bd": "1m1g9",
        "question": (
            "callers of a first-party function name that only ever existed "
            "as a method on an imported value"
        ),
        "verb": "callers",
        "argument": "symbol:py:pkg.tables:lookup",
        "glob": "pkg/**/*.py",
        "sources": {
            "pkg/__init__.py": "",
            "pkg/tables.py": (
                'TABLE = {"a": 1}\n\n\n'
                "def lookup(key):\n    return TABLE[key]\n"
            ),
            "pkg/caller.py": (
                "from pkg.tables import TABLE, lookup\n\n\n"
                'def run():\n    return TABLE.get(lookup("a"))\n'
            ),
        },
        "must_answer_with": ["symbol:py:pkg.caller:run"],
        "must_not_exist": ["symbol:py:pkg.tables:get"],
        "why": (
            "Reported against this repo's own weld/_contract_validators.py, "
            "which imports a protocol-compatibility dict and calls .get on "
            "it. python_callgraph resolved the attribute against the "
            "import-table module slot without reading the attr slot that "
            "already distinguishes a module alias from a from-imported "
            "name, so an ordinary dict method minted a first-party symbol "
            "id for a get that exists under no spelling -- and callers on "
            "it answered, confidently, with a function that calls no such "
            "thing. That is the same class of wrong answer as bd gkpqa "
            "above, inverted: there a real symbol read as having no "
            "callers, here a name that is no symbol at all read as having "
            "one. The must_answer_with half is what keeps the fix honest: "
            "the bare-name import of lookup shares the very import-table "
            "entry the attribute call misread, so a fix that stopped "
            "reading the table at all would satisfy must_not_exist and "
            "lose a real caller edge."
        ),
    },
    {
        "bd": "vrdcj",
        "question": (
            "callers of a classmethod whose call sites import the class "
            "and call the method on it"
        ),
        "verb": "callers",
        "argument": "symbol:py:pkg.tables:Corpus.build",
        "glob": "pkg/**/*.py",
        "sources": {
            "pkg/__init__.py": "",
            "pkg/tables.py": (
                'TABLE = {"a": 1}\n\n\n'
                "class Corpus:\n"
                "    @classmethod\n"
                "    def build(cls, rows):\n        return cls()\n"
            ),
            "pkg/caller.py": (
                "from pkg.tables import TABLE, Corpus\n\n\n"
                "def run():\n    return Corpus.build(TABLE.get('a'))\n"
            ),
        },
        "must_answer_with": ["symbol:py:pkg.caller:run"],
        "must_not_exist": [
            "symbol:py:pkg.tables:build", "symbol:py:pkg.tables:get",
        ],
        "why": (
            "The sub-shape bd 1m1g9 left behind. Stopping the fabricated "
            "symbol:py:pkg.tables:build was right, but it sent the call to "
            "the sentinel for every base, and one base has a real answer: "
            "when Corpus is a class the module actually defines, the method "
            "is symbol:py:pkg.tables:Corpus.build, a node the walk already "
            "emitted under its dotted qualname. Measured on this repo, the "
            "method had read 'no callers' throughout -- fabricated target "
            "before the 1m1g9 fix, sentinel after it -- so the blast radius "
            "of changing a classmethod reached through an import read empty "
            "the whole time, the same symptom as bd gkpqa on a third shape. "
            "The entry keeps TABLE.get in the same function on purpose: it "
            "is the constant base the rule must keep refusing, and it "
            "carries the same hint through the same code path, so a rule "
            "that resolved on the base alone would satisfy must_answer_with "
            "and quietly re-mint the id must_not_exist forbids."
        ),
    },
)


__all__ = ["TRAVERSAL_CORPUS"]
