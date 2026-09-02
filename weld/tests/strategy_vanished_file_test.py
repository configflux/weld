"""Walked files that vanish or refuse to decode (bd o642).

bd pt38 fixed ``python_module`` and ``_python_anchor``: ``read_text`` raises
``OSError``, never ``SyntaxError``, so an ``except SyntaxError`` guard let a
file that disappeared between the listing and the read propagate out of
``extract`` and take the whole discovery run down. A survey of every
``read_text`` under ``weld/strategies/`` found the same defect in five more
strategies; this module is their regression coverage.

Every case is deterministic -- no sleep, no race. There are two shapes, chosen
by how the strategy lists its files:

* ``sqlalchemy`` and ``ros2_interfaces`` list through ``walk_glob``, so a
  ``glob_scope`` memo (bd cjij) reproduces the production window exactly: the
  listing is taken when the run begins and every strategy re-resolving that
  glob is served it, naming files that need not still be on disk.
* ``fastapi``, ``pydantic`` and ``worker_stage`` are exercised through the
  narrower window *inside* a single ``extract``: between the listing (which
  shells git through the repo-boundary filter) and each file's own read. A
  dangling symlink holds exactly that window open permanently -- the listing
  names the path and the read raises ``FileNotFoundError`` on it -- so it is
  the same defect frozen still rather than raced for.

  ``fastapi`` and ``pydantic`` were in that group because they listed with
  ``Path.glob``, which the memo never reaches; bd b9xgd moved both onto the
  ADR 0112 shared resolver, so the memo serves them too and the wider window
  applies as well. Their cases stay on the dangling symlink rather than
  moving: it holds the read window open with or without a memo, so it keeps
  proving the read guard itself rather than the listing that fed it.

``worker_stage`` is the one that does not fit the vanish shape, and is not
forced into it. It is also the only one here that still re-lists inside its own
``extract``, with ``Path.iterdir`` over the glob's parent -- directory-shaped,
so the shared resolver has no file resolve of its own to take over. It
pre-checks ``init_py.exists()``, so a file already gone is skipped before the
read and its only vanish window is the tight ``exists()``-to-read race. That
pre-check stays: it is what separates "this directory is not a worker stage"
(a decision) from "the file is unreadable" (a failure), so the guard is proven
through the other arm of the same ``except`` -- bytes that will not decode.

Each case asserts two things. A sibling file must still anchor -- that is what
makes it a regression test rather than an assertion that passes just as well on
a strategy that crashed before emitting anything. And the lost file must be
recorded through ``note_strategy_failure``: bd hch4, a file weld could not read
is a repairable *failure*, never this strategy *deciding* the file yields
nothing. The decision set is keyed on the path alone and only a content change
re-dirties a file, so a file that came back unchanged would never be re-read.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.glob_match import glob_scope, walk_glob
from weld.strategies._strategy_failure import drain_strategy_failures
from weld.strategies.fastapi import extract as fastapi_extract
from weld.strategies.pydantic import extract as pydantic_extract
from weld.strategies.ros2_interfaces import extract as ros2_extract
from weld.strategies.sqlalchemy import extract as sqlalchemy_extract
from weld.strategies.worker_stage import extract as worker_stage_extract

# A latin-1 module is legal Python and a perfectly ordinary file; it is simply
# not UTF-8. It earns its own arm because ``UnicodeDecodeError`` is a
# ``ValueError``, so a guard widened to ``OSError`` alone still aborts on it.
_LATIN1 = b"# -*- coding: latin-1 -*-\nNAME = '\xe9'\n"

_ENTITY = """\
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
"""

_CONTRACT = """\
from pydantic import BaseModel


class Register(BaseModel):
    email: str
"""

_ROUTER = """\
from fastapi import APIRouter

router = APIRouter(prefix="/v1")


@router.get("/keep")
def keep():
    return 1
"""

_APP = """\
from fastapi import FastAPI

app = FastAPI()
"""

_STAGE_INIT = """\
__all__ = ["run"]
"""


class _TreeCase(unittest.TestCase):
    """A throwaway tree plus the two ways a listed file refuses to be read."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, rel: str, body: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def write_latin1(self, rel: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_LATIN1)
        return path

    def dangle(self, rel: str) -> None:
        """Put *rel* in the listing while its read raises ``FileNotFoundError``.

        A directory listing names entries, not readable files, so a symlink
        whose target does not exist is returned by ``Path.glob`` exactly as a
        file that is about to vanish is: present to the lister, gone to the
        reader.
        """
        link = self.root / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(self.root / f"{rel}.absent")

    @staticmethod
    def anchored(result) -> set[str]:
        return {
            node["props"]["file"]
            for node in result.nodes.values()
            if "file" in node.get("props", {})
        }


class _MemoCase(_TreeCase):
    """Strategies whose listing comes from the memoized ``walk_glob``."""

    GLOB = ""

    def extract(self, root: Path, source: dict, context: dict):
        raise NotImplementedError

    def _extract_after_removing(self, *rels: str):
        """Walk the glob, remove *rels*, then extract off the memoized walk.

        This is the production window and not a simulation of it: ``discover``
        is decorated with ``@glob_scope()`` (bd cjij), so the strategy's own
        ``walk_glob`` call is answered from the listing taken when the run
        began. Priming with the identical ``(root, pattern, excludes)`` triple
        the strategy passes is what makes the memo serve it.
        """
        context: dict = {}
        with glob_scope():
            walked = walk_glob(self.root, self.GLOB, excludes=[])
            self.assertTrue(walked, "fixture walked to nothing")
            for rel in rels:
                (self.root / rel).unlink()
            result = self.extract(self.root, {"glob": self.GLOB}, context)
        return result, context


class PydanticVanishedFileTest(_TreeCase):
    """``pydantic`` -- the shared resolver, then a guarded read."""

    SOURCE = {"glob": "contracts/*.py"}

    def setUp(self) -> None:
        super().setUp()
        self.write("contracts/keeper.py", _CONTRACT)

    def test_unreadable_file_does_not_abort_the_glob(self) -> None:
        self.dangle("contracts/vanished.py")
        context: dict = {}

        result = pydantic_extract(self.root, self.SOURCE, context)

        self.assertIn("contract:Register", result.nodes)
        self.assertEqual({"contracts/vanished.py"}, drain_strategy_failures(context))

    def test_undecodable_bytes_are_a_failure_not_a_crash(self) -> None:
        self.write_latin1("contracts/latin.py")
        context: dict = {}

        result = pydantic_extract(self.root, self.SOURCE, context)

        self.assertIn("contract:Register", result.nodes)
        self.assertEqual({"contracts/latin.py"}, drain_strategy_failures(context))


class FastapiVanishedFileTest(_TreeCase):
    """``fastapi`` -- two reads: the router loop and the boundary lookup."""

    SOURCE = {"glob": "services/svc/routers/*.py"}

    def setUp(self) -> None:
        super().setUp()
        self.write("services/svc/main.py", _APP)
        self.write("services/svc/routers/keeper.py", _ROUTER)

    def test_unreadable_router_does_not_abort_the_glob(self) -> None:
        self.dangle("services/svc/routers/vanished.py")
        context: dict = {}

        result = fastapi_extract(self.root, self.SOURCE, context)

        self.assertIn("route:GET:/v1/keep", result.nodes)
        self.assertEqual(
            {"services/svc/routers/vanished.py"}, drain_strategy_failures(context),
        )

    def test_undecodable_router_is_a_failure_not_a_crash(self) -> None:
        self.write_latin1("services/svc/routers/latin.py")
        context: dict = {}

        result = fastapi_extract(self.root, self.SOURCE, context)

        self.assertIn("route:GET:/v1/keep", result.nodes)
        self.assertEqual(
            {"services/svc/routers/latin.py"}, drain_strategy_failures(context),
        )

    def test_undecodable_boundary_candidate_still_finds_the_app(self) -> None:
        """The second read in this file: the app-file scan beside ``routers/``.

        It sorts before ``main.py``, so the scan meets it first. Nothing is
        recorded for it -- a boundary candidate is not this strategy's input
        (it never enters ``discovered_from``); ``python_module`` owns that file
        and records its own failure for it.
        """
        self.write_latin1("services/svc/latin.py")
        context: dict = {}

        result = fastapi_extract(self.root, self.SOURCE, context)

        self.assertIn(
            ("boundary:services/svc/main", "route:GET:/v1/keep"),
            {(e["from"], e["to"]) for e in result.edges},
        )
        self.assertEqual(set(), drain_strategy_failures(context))


class SqlalchemyVanishedFileTest(_MemoCase):
    """``sqlalchemy`` -- ``walk_glob``, so the run-wide memo window applies."""

    GLOB = "domain/*.py"

    def extract(self, root: Path, source: dict, context: dict):
        return sqlalchemy_extract(root, source, context)

    def setUp(self) -> None:
        super().setUp()
        self.write("domain/keeper.py", _ENTITY)
        self.write("domain/doomed.py", _ENTITY.replace("User", "Order"))

    def test_vanished_file_does_not_abort_the_glob(self) -> None:
        result, context = self._extract_after_removing("domain/doomed.py")

        self.assertIn("entity:User", result.nodes)
        self.assertNotIn("entity:Order", result.nodes)
        self.assertEqual({"domain/doomed.py"}, drain_strategy_failures(context))

    def test_every_file_vanishing_is_still_not_a_crash(self) -> None:
        result, context = self._extract_after_removing(
            "domain/doomed.py", "domain/keeper.py",
        )

        self.assertEqual(set(), self.anchored(result))
        self.assertEqual(
            {"domain/doomed.py", "domain/keeper.py"},
            drain_strategy_failures(context),
        )

    def test_undecodable_bytes_are_a_failure_not_a_crash(self) -> None:
        self.write_latin1("domain/latin.py")
        context: dict = {}

        result = sqlalchemy_extract(self.root, {"glob": self.GLOB}, context)

        self.assertIn("entity:User", result.nodes)
        self.assertEqual({"domain/latin.py"}, drain_strategy_failures(context))


class Ros2VanishedFileTest(_MemoCase):
    """``ros2_interfaces`` -- survives already; the *record* was missing.

    This strategy never crashed on a vanished file: ``extract`` pre-checks
    ``iface.is_file()``, which is False for a path that is gone, so the read is
    never reached. It recorded nothing either, and that is the defect. The file
    was hashed when the run began, so it is in the state inventory; it has no
    node; and with no failure noted it lands in ``files_with_no_nodes`` -- the
    set that means "a strategy looked and decided this file yields nothing".
    That exemption is keyed on the path, so a file that comes back byte
    for byte identical is never dirty and never repaired: it stays out of the
    graph while every freshness signal reads clean (bd hch4).
    """

    GLOB = "pkg/**/*"

    def extract(self, root: Path, source: dict, context: dict):
        return ros2_extract(root, source, context)

    def setUp(self) -> None:
        super().setUp()
        self.write("pkg/msg/Keep.msg", "int32 a\n")
        self.write("pkg/msg/Doom.msg", "int32 b\n")
        self.write("pkg/srv/Doom.srv", "int32 a\n---\nint32 b\n")
        self.write("pkg/action/Doom.action", "int32 a\n---\nint32 b\n---\nint32 c\n")

    def test_vanished_interface_is_recorded_as_a_repairable_failure(self) -> None:
        result, context = self._extract_after_removing("pkg/msg/Doom.msg")

        self.assertIn("ros_interface:pkg/msg/Keep", result.nodes)
        self.assertNotIn("ros_interface:pkg/msg/Doom", result.nodes)
        self.assertEqual({"pkg/msg/Doom.msg"}, drain_strategy_failures(context))

    def test_every_interface_kind_reports_its_loss(self) -> None:
        """One guard covers all three reads -- ``.msg``, ``.srv``, ``.action``."""
        result, context = self._extract_after_removing(
            "pkg/msg/Doom.msg", "pkg/srv/Doom.srv", "pkg/action/Doom.action",
        )

        self.assertIn("ros_interface:pkg/msg/Keep", result.nodes)
        self.assertEqual(
            {"pkg/msg/Doom.msg", "pkg/srv/Doom.srv", "pkg/action/Doom.action"},
            drain_strategy_failures(context),
        )

    def test_a_surviving_interface_is_not_reported_as_lost(self) -> None:
        """The guard must not have turned every file into a failure."""
        context: dict = {}

        result = ros2_extract(self.root, {"glob": self.GLOB}, context)

        self.assertIn("ros_interface:pkg/msg/Doom", result.nodes)
        self.assertEqual(set(), drain_strategy_failures(context))


class WorkerStageUnreadableFileTest(_TreeCase):
    """``worker_stage`` -- guarded through the arm its ``exists()`` cannot mask.

    ``init_py.exists()`` is a check-then-use: it narrows the vanish window to
    the microseconds between the two syscalls but cannot close it, because
    nothing holds the file still in between. The guard is what closes it, and
    undecodable bytes reach that guard deterministically -- ``exists()`` is
    true, the read still fails.
    """

    SOURCE = {"glob": "workers/*"}

    def setUp(self) -> None:
        super().setUp()
        self.write("workers/alpha/__init__.py", _STAGE_INIT)

    def test_undecodable_init_is_a_failure_not_a_crash(self) -> None:
        self.write_latin1("workers/beta/__init__.py")
        context: dict = {}

        result = worker_stage_extract(self.root, self.SOURCE, context)

        self.assertIn("stage:alpha", result.nodes)
        self.assertNotIn("stage:beta", result.nodes)
        self.assertEqual(
            {"workers/beta/__init__.py"}, drain_strategy_failures(context),
        )

    def test_a_directory_without_an_init_is_not_a_failure(self) -> None:
        """The decision the ``exists()`` pre-check exists to keep making.

        A subdirectory with no ``__init__.py`` is not a worker stage. Recording
        that as a failure would put a path weld never had a problem with into
        the repair set on every run, for good.
        """
        (self.root / "workers" / "gamma").mkdir(parents=True)
        context: dict = {}

        result = worker_stage_extract(self.root, self.SOURCE, context)

        self.assertIn("stage:alpha", result.nodes)
        self.assertEqual(set(), drain_strategy_failures(context))


if __name__ == "__main__":
    unittest.main()
