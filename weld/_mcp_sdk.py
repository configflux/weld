"""What weld requires of the optional ``mcp`` SDK, defined once.

Two surfaces ask whether the installed SDK is one weld can drive, and a user
who consults both has to get one story:

* :mod:`weld._mcp_stdio` refuses to start the stdio server on an SDK that
  does not expose the 2.x request-handler API, and says so on stderr;
* :mod:`weld._doctor_optional` reports the state of the ``mcp`` extra in
  ``wd doctor``'s Optional section.

They probe differently, on purpose. The server is already importing the SDK,
so it can feature-probe the very class it is about to use -- the honest test,
and the one that stays right even when a version number lies. The doctor must
not import an optional dependency merely to describe it, so it reads
distribution metadata instead. What must *not* differ is the requirement
itself: the major version and the API name below are the single definition
both consult.

Metadata that cannot be read is reported as usable rather than outdated.
Doctor is advisory and the stdio server keeps its own hard guard, so an
unreadable version is a thing not observed -- not grounds for telling someone
with a working install to upgrade it.
"""

from __future__ import annotations

import re


#: Major version of the ``mcp`` SDK weld's stdio server targets. 2.0 removed
#: the 1.x ``@server.list_tools()`` / ``@server.call_tool()`` decorators, so
#: a 1.x SDK cannot run the server at all -- this is a floor, not a
#: preference.
REQUIRED_MAJOR = 2

#: The requirement as pip spells it, so install hints and error messages on
#: every surface quote the same token.
REQUIRED_SPEC = f"mcp>={REQUIRED_MAJOR}"

#: The command that fixes an installed-but-outdated SDK. Distinct from the
#: extra's install line by design: someone who already has the SDK must never
#: be told to install it.
UPGRADE_COMMAND = f"pip install -U '{REQUIRED_SPEC}'"

#: The ``Server`` method that exists only from 2.0 on. A pre-2.0 SDK still
#: exports every type the stdio server imports, which is why this attribute
#: -- not an ``ImportError`` -- is what separates the two.
HANDLER_API = "add_request_handler"

_LEADING_INT = re.compile(r"\s*(\d+)")


def installed_version() -> str | None:
    """Return the installed ``mcp`` distribution's version, or ``None``.

    ``None`` covers every way the answer can be unknown at once: no
    distribution metadata, an unreadable environment, or something that
    answers to ``mcp`` without being the SDK.
    """
    try:
        from importlib.metadata import version

        return version("mcp")
    except Exception:  # noqa: BLE001 - metadata absent or unreadable
        return None


def version_supported(version: str | None) -> bool:
    """Return whether *version* names an SDK weld can drive.

    Unknown (``None``) and unparseable versions return ``True``: this
    predicate reports only what it observed, and "I could not read it" is
    not an observation of an old SDK.
    """
    if version is None:
        return True
    match = _LEADING_INT.match(version)
    if match is None:
        return True
    return int(match.group(1)) >= REQUIRED_MAJOR


def sdk_usable() -> bool:
    """Return whether the installed SDK's version clears the floor.

    Presence is deliberately not part of this answer. Callers that need to
    tell an absent SDK from an outdated one probe for presence first,
    because the two have different remedies.
    """
    return version_supported(installed_version())


def provides_handler_api(server_cls: object) -> bool:
    """Return whether *server_cls* exposes the 2.x registration API."""
    return hasattr(server_cls, HANDLER_API)
