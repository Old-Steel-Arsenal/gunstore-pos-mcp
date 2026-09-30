"""GunStore-POS admin MCP server.

Run: `gunstore-mcp` (console script) or `python -m gunstore_mcp.server`.

Transports (GUNSTORE_MCP_TRANSPORT): "stdio" (default) = the local server, one
API key from mcp/.env (see .env.example); "http" = the remote connector — an
OAuth resource server whose authorization server is the POS itself. It holds no
key: every request carries the signed-in user's bearer token, verified against
the POS (auth.FrappeTokenVerifier) and forwarded on every call.

Modes (GUNSTORE_MCP_MODE): "full" (default) = the whole surface; "cpa" = the
read-only accountant surface (exactly the 20 tools in modes.CPA_TOOL_NAMES; the
write tools are never registered).

GUNSTORE_MCP_DISTRIBUTOR_ACTIONS=1 additionally opts into the 4 distributor queue
actions, which are NOT registered otherwise. Sizes are asserted in tests rather
than restated here — this docstring is where the last stale count lived."""
from __future__ import annotations

import functools
import inspect
from urllib.parse import urlparse

import anyio

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__
from .auth import FrappeTokenVerifier
from .config import HTTP, get_config, get_transport
from .modes import CPA_MODE, CPA_TOOL_NAMES, FULL_MODE, FilteredMCP, get_mode
from .tools import curated, distributor, generic, reports


class ThreadedMCP:
    """Remote registration wrapper: every sync tool runs in a worker thread.

    The tools block on requests; FastMCP calls a sync tool on the event loop, so
    one slow report would stall every other user of the process. anyio copies the
    request's contextvars into the worker, so get_access_token() still sees this
    request's token there."""

    def __init__(self, mcp) -> None:
        self._mcp = mcp

    def tool(self, *args, **kwargs):
        real = self._mcp.tool(*args, **kwargs)

        def deco(fn):
            if inspect.iscoroutinefunction(fn):
                return real(fn)

            @functools.wraps(fn)
            async def threaded(*a, **kw):
                return await anyio.to_thread.run_sync(functools.partial(fn, *a, **kw))

            # FastMCP builds the schema from the signature and resolves string
            # annotations against the function's globals; hand it the resolved
            # signature so the wrapper's (server.py) globals never matter.
            threaded.__signature__ = inspect.signature(fn, eval_str=True)
            real(threaded)
            return fn

        return deco


def register_tools(mcp, mode: str | None = None) -> None:
    """Register the tool surface for `mode` (default: env GUNSTORE_MCP_MODE).

    cpa mode routes every registration through FilteredMCP so only the
    18 allowlisted tools ever reach tools/list."""
    mode = mode or get_mode()
    if mode not in (FULL_MODE, CPA_MODE):
        raise RuntimeError(f"Unknown server mode {mode!r}.")
    target = mcp if mode == FULL_MODE else FilteredMCP(mcp, CPA_TOOL_NAMES)
    generic.register(target)
    curated.register(target)
    distributor.register(target)
    reports.register(target)


def _http_settings() -> dict:
    """FastMCP kwargs for the remote connector.

    The proxy maps https://<host>/<store>/<mode>/mcp to this process's /mcp, and
    forwards /.well-known/oauth-protected-resource/<store>/<mode>/mcp unchanged
    (the SDK derives that metadata route from the public URL, RFC 9728)."""
    cfg = get_config()
    if distributor.actions_enabled() or curated.gunbroker_actions_enabled():
        # A developer .env can carry these; the remote surface never opens them.
        raise RuntimeError("The action gates stay off on the remote connector — "
            "unset GUNSTORE_MCP_DISTRIBUTOR_ACTIONS / GUNSTORE_MCP_GUNBROKER_ACTIONS.")
    public = urlparse(cfg.public_url)
    return {
        "host": "127.0.0.1",
        "port": cfg.port,
        "streamable_http_path": "/mcp",
        # Stateless: every request runs the server in ITS OWN context, so tools see
        # that request's token (a refreshed one included), nothing is kept between
        # requests, and a session id is not a credential anybody could replay.
        "stateless_http": True,
        "token_verifier": FrappeTokenVerifier(cfg.base_url),
        "auth": AuthSettings(issuer_url=cfg.base_url, resource_server_url=cfg.public_url),
        # Bound to loopback, the SDK would otherwise allow only localhost Host
        # headers and refuse every request the proxy forwards.
        "transport_security": TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public.netloc, "127.0.0.1:*", "localhost:*"],
            allowed_origins=[f"{public.scheme}://{public.netloc}", "https://claude.ai"],
        ),
    }


def build() -> FastMCP:
    mode = get_mode()
    remote = get_transport() == HTTP
    kwargs = _http_settings() if remote else {}
    mcp = FastMCP("gunstore-pos" if mode == FULL_MODE else "gunstore-pos-cpa", **kwargs)
    # FastMCP takes no version; without this serverInfo reports the mcp SDK's
    # version, so clients can't tell which build of ours they're talking to.
    mcp._mcp_server.version = __version__
    register_tools(ThreadedMCP(mcp) if remote else mcp, mode)
    return mcp


mcp = build()


def main() -> None:
    mcp.run("streamable-http" if get_transport() == HTTP else "stdio")


if __name__ == "__main__":
    main()
