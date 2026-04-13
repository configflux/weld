"""Strategy: declared async channels (project-xoq.6.1).

Thin facade that dispatches to one of two conservative extractors:

- :mod:`cortex.strategies.events_config` scans ``docker-compose*.yml``
  files for ``services.<svc>.environment`` entries whose key matches a
  known channel env-var pattern (``KAFKA_*_TOPIC``,
  ``CELERY_*_QUEUE``, ``REDIS_*_CHANNEL``) and whose value is a bare
  literal string. Both mapping and list forms are supported.

- :mod:`cortex.strategies.events_callsite` walks Python files for calls
  shaped ``<Root>.<verb>("literal", ...)`` where ``<Root>`` is a known
  async client identifier (``KafkaProducer``, ``kafka``, ``redis``)
  and ``<verb>`` is a known publish verb. Dynamic first args are
  dropped per ADR 0018's static-truth policy.

Both halves emit ``channel`` nodes stamped with ADR 0018 /
project-xoq.1.2 metadata::

    protocol="event", surface_kind="pub_sub",
    transport=<kafka|tcp|amqp>, boundary_kind="internal",
    declared_in="<rel-path>"

plus a ``contains`` edge from the declaring ``file:<rel-path>`` node
to the channel. Producer/consumer linking lives in project-xoq.6.2
and is explicitly out of scope here.
"""

from __future__ import annotations

from pathlib import Path

from cortex.strategies._helpers import StrategyResult
from cortex.strategies.events_callsite import extract_py_callsite
from cortex.strategies.events_config import extract_compose_env

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Dispatch to the config or callsite extractor based on ``kind``.

    ``source`` keys:

    - ``kind``: ``compose_env`` or ``py_callsite`` (required).
    - ``glob``: a path glob relative to ``root`` (required).

    Unknown kinds and missing globs return an empty result rather than
    raising, matching the fail-open convention other strategies use.
    """
    pattern = source.get("glob")
    if not pattern:
        return StrategyResult({}, [], [])

    kind = source.get("kind", "compose_env")
    if kind == "compose_env":
        nodes, edges, discovered = extract_compose_env(root, pattern)
    elif kind == "py_callsite":
        nodes, edges, discovered = extract_py_callsite(root, pattern)
    else:
        return StrategyResult({}, [], [])

    return StrategyResult(nodes, edges, discovered)
