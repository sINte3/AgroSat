"""The frontend bind address (TASK_243).

The frontend listens on 127.0.0.1 unless the operator sets
``AGROSAT_FRONTEND_BIND_ADDRESS`` in the runtime environment file. MATERIALIZE
resolves the setting once and writes it into the release's
``application-release.json`` as ``frontend_bind_address``. The supervisor
launches the frontend on exactly that address. Every health, listener and
rollback check reads the address from the configuration of the release it
verifies, so a rollback returns to the address that release was built with.
The backend never follows the setting: it always listens on 127.0.0.1.

Accepted: a canonical IPv4 literal on loopback (127.0.0.0/8) or, for
production only, in a private RFC 1918 range. Refused: a wildcard (0.0.0.0),
a host name, IPv6, a port, whitespace, and anything else.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from .common import ControlPlaneError
from .pgclient import read_env_value

SETTING = "AGROSAT_FRONTEND_BIND_ADDRESS"
CONFIG_KEY = "frontend_bind_address"
LOOPBACK = "127.0.0.1"
OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
IPV4_LITERAL = re.compile(rf"{OCTET}(?:\.{OCTET}){{3}}")
LOOPBACK_NETWORK = ipaddress.IPv4Network("127.0.0.0/8")
PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(item) for item in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def validate_bind_address(value: Any, *, production: bool) -> str:
    """The address itself, or FRONTEND_BIND_ADDRESS_REJECTED (the value is never echoed)."""
    if not isinstance(value, str) or not IPV4_LITERAL.fullmatch(value):
        raise ControlPlaneError("FRONTEND_BIND_ADDRESS_REJECTED", "a canonical IPv4 literal is required")
    address = ipaddress.IPv4Address(value)
    if address in LOOPBACK_NETWORK or (production and any(address in network for network in PRIVATE_NETWORKS)):
        return value
    raise ControlPlaneError("FRONTEND_BIND_ADDRESS_REJECTED",
                            "loopback, or in production a private (RFC 1918) address; never a wildcard")


def configured_bind_address(profile) -> dict[str, str]:
    """The operator's setting from the profile's runtime environment file, validated."""
    value = read_env_value(profile.runtime_env_file, SETTING)
    if value is None:
        return {"address": LOOPBACK, "source": "default"}
    return {"address": validate_bind_address(value, production=profile.production), "source": "runtime_env_file"}


def bind_address_of(config: dict[str, Any], *, production: bool) -> str:
    """The address an application configuration binds; loopback when it names none (before TASK_243)."""
    if CONFIG_KEY not in config:
        return LOOPBACK
    return validate_bind_address(config[CONFIG_KEY], production=production)
