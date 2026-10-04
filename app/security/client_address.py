"""
Client Address
==============

Overview
--------
Names the client a request came from, for the per-address limits and the sign-in audit record.

Scope
-----
In: choosing the address a limit or audit record is keyed on.
Out: the limits themselves (``limits``, ``issuance_limits``) and what the routes do with a refusal.

Design Principles
-----------------
- The backend is reachable only from Caddy, over the internal compose network (infra/Caddyfile;
  never exposed to the internet directly). Caddy is the sole, trusted first hop, and its own
  ``reverse_proxy`` directive sets X-Forwarded-For to the address it saw on its own accepted
  connection, verified against a real Caddy instance: a caller-supplied value in the same header
  does not survive, because Caddy replaces it rather than appending to it.
- Only the last entry is trusted, in case a future hop ever appends rather than replaces.
- Without this, ``request.client.host`` is the one address Caddy connects from, so every visitor
  collapses onto it and a per-address limit silently becomes a limit on the whole deployment.
- No header, or an empty last entry, falls back to the connecting address, which keeps unproxied
  local runs working. An empty entry is never a client identity.

Runtime Contract
----------------
``client_address(request) -> str`` is the last X-Forwarded-For entry when it is non-empty, else the
connecting address, else ``"unknown"``.

Limitations
-----------
The trust is only as good as the topology: without a proxy that replaces X-Forwarded-For, a caller
can rotate the header and is never limited. ``make run`` starts uvicorn directly with no proxy in
front, so locally the header is caller-controlled; the production compose file publishes no backend
port, so only Caddy reaches it there.
"""

from __future__ import annotations

# Third-party libraries
from fastapi import Request  # Request access


def client_address(request: Request) -> str:
    """The real connecting address, trusting the reverse proxy's own X-Forwarded-For.

    Behind the proxy the last header entry is the client; with no header it is the connecting
    address. Without a proxy in front the header is caller-controlled and must not be relied on.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"
