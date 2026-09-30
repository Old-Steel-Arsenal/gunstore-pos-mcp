"""Remote-connector auth — this server is an OAuth *resource server* for the POS.

The POS (Frappe) is the authorization server: the connector registers itself
there, the user signs in to the POS and approves, and every request then arrives
with that user's bearer token. This module only answers "is this a live token of
a POS user, issued to a connector?" — by asking the POS itself
(ffl_core.api.connector.connector_identity) — so no key, secret or user database
lives here. Tokens issued to OAuth apps created in Desk are refused: only a
client made by dynamic registration (a connector) may drive the MCP. The same
token is then forwarded on every Frappe call (frappe_client.get_client), so each
call runs with that user's own roles.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict

import requests
from mcp.server.auth.provider import AccessToken

from .frappe_client import remote_session

IDENTITY = "/api/method/ffl_core.api.connector.connector_identity"
# How long a positive answer is reused. Short on purpose: a token revoked in the
# POS stops working here within this window.
CACHE_SECONDS = 60
# A token the POS rejected is remembered briefly, so repeating one bad token
# costs one POS round-trip per window.
REJECT_SECONDS = 30
# lazy: a plain LRU bound; a flood of DISTINCT bogus tokens still costs one POS
# call each — rate-limit at the proxy if that ever shows up.
CACHE_MAX = 5000


class AuthUnavailable(RuntimeError):
    """The POS could not answer (restarting, 5xx, rate-limited, not JSON). Not
    a verdict on the token: nothing is cached and the request fails with a
    server error, so clients retry instead of throwing their token away."""


def _key(token: str) -> str:
    # The cache never holds the token itself.
    return hashlib.sha256(token.encode()).hexdigest()


class FrappeTokenVerifier:
    """mcp TokenVerifier backed by the POS."""

    def __init__(self, backend_url: str, host_header: str = "", timeout: int = 10) -> None:
        self.backend_url = backend_url.rstrip("/")
        self.host_header = host_header
        self.timeout = timeout
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def _identity(self, token: str) -> str:
        """The token's user; "" when the POS refuses it. Raises AuthUnavailable
        when the POS gives no verdict."""
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if self.host_header:
            headers["Host"] = self.host_header
        try:
            resp = remote_session().get(self.backend_url + IDENTITY, headers=headers,
                                        timeout=self.timeout)
        except requests.RequestException as e:
            raise AuthUnavailable(f"POS unreachable: {type(e).__name__}") from None
        if resp.status_code in (401, 403, 417):
            return ""
        if resp.status_code != 200:
            raise AuthUnavailable(f"POS answered {resp.status_code}")
        try:
            me = (resp.json() or {}).get("message") or {}
        except ValueError:
            raise AuthUnavailable("POS answered non-JSON") from None
        user = me.get("user") if isinstance(me, dict) else None
        if not user or user == "Guest" or not me.get("connector"):
            return ""
        return user

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        now = time.monotonic()
        key = _key(token)
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            self._cache.move_to_end(key)
            user = hit[1]
        else:
            user = await asyncio.to_thread(self._identity, token)
            self._cache[key] = (now + (CACHE_SECONDS if user else REJECT_SECONDS), user)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_MAX:
                self._cache.popitem(last=False)
        if not user:
            return None
        return AccessToken(token=token, client_id=user, scopes=[])
