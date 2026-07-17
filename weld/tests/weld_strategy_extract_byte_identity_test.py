"""Byte-identity regression for the interaction-strategy extract refactor.

The ``events_callsite``, ``events_bindings``, and ``http_client`` strategies
were collapsed onto shared call-site AST-match primitives in
:mod:`weld.strategies._ast_calls`. Per ADR 0012 (determinism contract) the
refactor must not change a single byte of strategy output.

This test runs each strategy on a representative multi-shape fixture,
canonicalizes ``{nodes, edges, discovered_from}`` (dict keys sorted; edge and
``discovered_from`` order preserved as emitted), and asserts equality against a
golden captured from the *pre-refactor* code. The fixtures deliberately
exercise the traps that a careless extraction could break:

- ``http_client`` skips ``_``-prefixed filenames; the events strategies do not.
- ``events_bindings`` keeps its own (smaller) primitive-annotation set, so a
  ``Request``-annotated producer parameter still links a payload contract.
- literal-only f-strings are accepted; substituting f-strings / variables /
  dynamic methods are dropped.
- ``events_callsite`` mints the channel node at subscribe sites too, so a
  consumer-only topic (``users.created``) survives, a topic shared with a
  producer collapses onto one node, and a dynamic or mixed-literal
  ``subscribe`` list is dropped.

Regenerate goldens (only against known-good code) with::

    WELD_REGEN_GOLDEN=1 PYTHONPATH=. \
        python3 weld/tests/weld_strategy_extract_byte_identity_test.py
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.strategies.events_bindings import extract as events_bindings_extract
from weld.strategies.events_callsite import extract_py_callsite
from weld.strategies.http_client import extract as http_client_extract

_GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "strategy_byte_identity"

def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))

def _canonical(nodes: dict, edges: list, discovered_from: list) -> str:
    """Canonical JSON: sorted dict keys, preserved edge/discovered order."""
    return json.dumps(
        {
            "nodes": nodes,
            "edges": edges,
            "discovered_from": list(discovered_from),
        },
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    )

# ---------------------------------------------------------------------------
# http_client fixture + runner
# ---------------------------------------------------------------------------

def _build_http_client_fixture(root: Path) -> None:
    src = root / "src" / "pkg"
    _write(src / "full_urls.py", """\
        import httpx
        import requests
        def a():
            return httpx.get("https://example.com/api/v1/widgets")
        def b():
            return requests.post("https://api.example.com/v2/events", json={})
    """)
    _write(src / "path_and_kw.py", """\
        import httpx
        def health():
            return httpx.get("/health")
        def kw():
            return httpx.get(url="https://kw.example/x")
    """)
    _write(src / "dropped.py", """\
        import httpx
        URL = "https://example.com/api"
        def var():
            return httpx.get(URL)
        def fstr(host: str):
            return httpx.get(f"https://{host}/api")
        def dyn(method: str):
            return httpx.request(method, "https://example.com/api")
        def litf():
            return httpx.get(f"https://example.com/lit")
    """)
    # http_client skips ``_``-prefixed files: this call must NOT appear.
    _write(src / "_private.py", """\
        import httpx
        def hidden():
            return httpx.get("https://example.com/hidden")
    """)
    # No http import: dropped by the pre-filter, and ``get`` is not a client.
    _write(src / "no_import.py", """\
        def get(x):
            return x
        def fetch():
            return get("https://example.com/api")
    """)

def _run_http_client(root: Path):
    r = http_client_extract(root, {"glob": "src/**/*.py"}, {})
    return r.nodes, r.edges, r.discovered_from

# ---------------------------------------------------------------------------
# events_callsite fixture + runner
# ---------------------------------------------------------------------------

def _build_events_callsite_fixture(root: Path) -> None:
    src = root / "src" / "pkg"
    _write(src / "producers.py", """\
        from kafka import KafkaProducer
        import redis
        def send_order():
            KafkaProducer.send("orders.events", b"payload")
        def produce_more():
            kafka.produce("topic.x")
        def broadcast():
            redis.publish("notify:users", "hello")
        def send_dynamic(topic):
            KafkaProducer.send(topic, b"payload")
        def literal_fstring():
            KafkaProducer.send(f"orders.literal")
    """)
    # Consumer (subscribe) sites mint the channel node too, symmetric with
    # events_mqtt: list-form + single-form literals survive; the whole
    # argument drops on any non-literal element. ``orders.events`` and
    # ``notify:users`` collapse onto the producer-minted nodes above (last
    # writer wins ``declared_in``); ``users.created`` is consumer-only.
    _write(src / "consumers.py", """\
        from kafka import KafkaConsumer
        import redis
        def consume_orders():
            KafkaConsumer.subscribe(["orders.events", "users.created"])
        def listen():
            redis.subscribe("notify:users")
        def consume_dynamic(topics):
            KafkaConsumer.subscribe(topics)
        def consume_mixed(extra):
            KafkaConsumer.subscribe(["mixed.topic", extra])
    """)
    # events_callsite does NOT skip ``_``-prefixed files: this must appear.
    _write(src / "_shared.py", """\
        from aiokafka import AIOKafkaProducer
        def helper():
            kafka.produce("shared.topic")
    """)
    _write(src / "plain.py", "x = 1\n")

def _run_events_callsite(root: Path):
    return extract_py_callsite(root, "src/**/*.py")

# ---------------------------------------------------------------------------
# events_bindings fixture + runner
# ---------------------------------------------------------------------------

def _build_events_bindings_fixture(root: Path) -> None:
    src = root / "src" / "pkg"
    _write(src / "producers.py", """\
        from kafka import KafkaProducer
        from contracts import OrderEvent
        import redis
        def send_order(event: OrderEvent):
            KafkaProducer.send("orders.events", event.json())
        def send_raw(data: bytes):
            KafkaProducer.send("raw.topic", data)
        def broadcast():
            redis.publish("notify:users", "hello")
        def send_dynamic(topic):
            KafkaProducer.send(topic, b"payload")
    """)
    # ``Request`` is NOT in events_bindings' local primitive set, so this
    # producer still links a payload contract to ``contract:Request``.
    _write(src / "request_typed.py", """\
        from kafka import KafkaProducer
        def handle(req: Request):
            KafkaProducer.send("req.topic", req)
    """)
    _write(src / "consumers.py", """\
        from kafka import KafkaConsumer
        import redis
        def consume_all():
            KafkaConsumer.subscribe(["orders.events", "users.created"])
        def listen():
            redis.subscribe("notify:users")
        def consume_dynamic(topics):
            KafkaConsumer.subscribe(topics)
    """)

def _run_events_bindings(root: Path):
    r = events_bindings_extract(root, {"glob": "src/**/*.py"}, {})
    return r.nodes, r.edges, r.discovered_from

_CASES = {
    "http_client": (_build_http_client_fixture, _run_http_client),
    "events_callsite": (_build_events_callsite_fixture, _run_events_callsite),
    "events_bindings": (_build_events_bindings_fixture, _run_events_bindings),
}

def _produce(name: str) -> str:
    build, run = _CASES[name]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        build(root)
        nodes, edges, discovered = run(root)
    return _canonical(nodes, edges, discovered)

class StrategyExtractByteIdentityTest(unittest.TestCase):
    """Each refactored strategy reproduces its pre-refactor output exactly."""

    def _check(self, name: str) -> None:
        golden_path = _GOLDEN_DIR / f"{name}.json"
        self.assertTrue(
            golden_path.is_file(),
            f"missing golden {golden_path}; regenerate with WELD_REGEN_GOLDEN=1",
        )
        expected = golden_path.read_text(encoding="utf-8")
        self.assertEqual(
            _produce(name),
            expected,
            f"{name} extract output drifted from the byte-identity golden",
        )

    def test_http_client_byte_identical(self) -> None:
        self._check("http_client")

    def test_events_callsite_byte_identical(self) -> None:
        self._check("events_callsite")

    def test_events_bindings_byte_identical(self) -> None:
        self._check("events_bindings")

def _regen() -> None:
    _GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name in _CASES:
        out = _GOLDEN_DIR / f"{name}.json"
        out.write_text(_produce(name), encoding="utf-8")
        print(f"wrote {out}")

if __name__ == "__main__":
    if os.environ.get("WELD_REGEN_GOLDEN"):
        _regen()
    else:
        unittest.main()
