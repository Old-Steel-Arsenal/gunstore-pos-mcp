"""Remote-connector auth — this server is an OAuth *resource server* for the POS.

The POS (Frappe) is the authorization server: the connector registers itself
there, the user signs in to the POS and approves, and every request then arrives
with that user's bearer token. This module only answers "is this token a signed-in
POS user right now?" — by asking the POS itself — so no key, secret or user
database lives here. The same token is then forwarded on every Frappe call
(frappe_client.get_client), so each call runs with that user's own roles.
"""
from __future__ import annotations

import asyncio
import hashlib
import time

import requests
from mcp.server.auth.provider import AccessToken

# How long a positive answer is reused. Short on purpose: a token revoked in the
# POS stops working here within this window.
CACHE_SECONDS = 60
# A rejected token is remembered briefly too, so a flood of bogus bearers costs
# one POS round-trip each per window rather than one per request.
REJECT_SECONDS = 30


def _key(token: str) -> str:
    # The cache never holds the token itself.
    return hashlib.sha256(token.encode()).hexdigest()


class FrappeTokenVerifier:
    """mcp TokenVerifier: a token is valid iff the POS says it belongs to a
    signed-in, non-Guest user."""

    def __init__(self, base_url: str, timeout: int = 10) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cache: dict[str, tuple[float, str | None]] = {}

    def _logged_user(self, token: str) -> str | None:
        """The token's user; "" = the POS said no; None = no answer (network)."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/method/frappe.auth.get_logged_user",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return ""
        try:
            user = (resp.json() or {}).get("message")
        except ValueError:
            return ""
        return user if user and user != "Guest" else ""

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        now = time.monotonic()
        key = _key(token)
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            user = hit[1]
        else:
            user = await asyncio.to_thread(self._logged_user, token)
            if user is None:
                # The POS did not answer: reject this request, remember nothing —
                # a valid user must not be locked out for a network blip.
                return None
            # Drop expired entries on every write: refreshed tokens are new keys,
            # so nothing else would ever remove the old ones.
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            self._cache[key] = (now + (CACHE_SECONDS if user else REJECT_SECONDS), user)
        if not user:
            return None
        return AccessToken(token=token, client_id=user, scopes=[])
