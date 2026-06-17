"""Eager-federation flag resolution (ADR 0063, default-on amendment).

Owns the small, cohesive responsibility of deciding whether a
:class:`weld.federation.FederatedGraph` should build the eager
inverted-index aggregation. Split out of
``weld/_federation_eager_index.py`` so the aggregation class and the
flag policy each stay well under the CLAUDE.md 400-line cap.

Policy: an explicit constructor argument always wins. Otherwise the
``WELD_FEDERATION_EAGER`` env var is a two-way override -- a documented
truthy value forces eager on, a documented falsy value force-disables
it. When the env var is unset, empty, or unrecognized, the default
(:data:`EAGER_DEFAULT`, on) is used. Eager covers fresh-sidecar
children only, so a federation with no fresh sidecars builds an empty
index and pays no aggregation tax; default-on is therefore safe.
"""

from __future__ import annotations

import os

__all__ = [
    "EAGER_DEFAULT",
    "EAGER_ENV_VAR",
    "env_var_disables",
    "env_var_truthy",
    "resolve_eager_flag",
]

#: Env var that overrides the eager default for any FederatedGraph
#: constructed without an explicit ``eager_index=`` argument.
EAGER_ENV_VAR = "WELD_FEDERATION_EAGER"

#: Default eager state when neither the constructor arg nor the env var
#: decides (ADR 0063, default-on amendment).
EAGER_DEFAULT = True

#: Documented truthy values (case-folded) that force eager on.
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

#: Documented falsy values (case-folded) that force eager off -- the
#: operator force-disable knob the default-on amendment keeps.
_FALSY_VALUES = frozenset({"0", "false", "no", "off"})


def env_var_truthy(value: str | None) -> bool:
    """Return True iff *value* is one of the documented truthy strings."""
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_VALUES


def env_var_disables(value: str | None) -> bool:
    """Return True iff *value* is one of the documented falsy strings.

    Only the documented falsy set force-disables; an unset, empty, or
    unrecognized value leaves the :data:`EAGER_DEFAULT` in force.
    """
    if value is None:
        return False
    return value.strip().lower() in _FALSY_VALUES


def resolve_eager_flag(explicit: bool | None) -> bool:
    """Resolve the eager flag (ADR 0063, default-on amendment).

    Explicit constructor arg wins; otherwise ``WELD_FEDERATION_EAGER``
    decides when set to a documented truthy (on) or falsy (off) value;
    an unset/empty/unrecognized value falls to :data:`EAGER_DEFAULT`.
    """
    if explicit is not None:
        return bool(explicit)
    raw = os.environ.get(EAGER_ENV_VAR)
    if env_var_truthy(raw):
        return True
    if env_var_disables(raw):
        return False
    return EAGER_DEFAULT
