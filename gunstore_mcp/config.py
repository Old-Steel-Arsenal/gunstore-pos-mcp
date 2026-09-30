"""Environment-driven configuration (loaded once, lazily)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Schema / permission doctypes blocked from writes by default. Changing these is
# a code/migration concern, not something to do ad-hoc against production.
DEFAULT_WRITE_DENYLIST = (
    "DocType", "Custom Field", "Property Setter", "Server Script",
    "Client Script", "Role", "DocPerm", "Custom DocPerm", "User",
)


STDIO = "stdio"
HTTP = "http"
_VALID_TRANSPORTS = (STDIO, HTTP)


def get_transport() -> str:
    """GUNSTORE_MCP_TRANSPORT: stdio (default — the local, API-key server) or
    http (the remote connector: OAuth bearer tokens, no API key). An unknown
    value refuses to start rather than guessing which auth model was meant."""
    _load_env()
    raw = (os.environ.get("GUNSTORE_MCP_TRANSPORT") or STDIO).strip().lower()
    if raw not in _VALID_TRANSPORTS:
        raise RuntimeError(
            f"GUNSTORE_MCP_TRANSPORT must be one of {_VALID_TRANSPORTS} (got {raw!r})."
        )
    return raw


@dataclass(frozen=True)
class Config:
    base_url: str
    api_key: str
    api_secret: str
    timeout: int
    write_denylist: frozenset[str]
    transport: str = STDIO
    # http transport only: the public resource URL a connector is given
    # (https://mcp.example.com/osa/cpa/mcp) and the local port behind the proxy.
    public_url: str = ""
    port: int = 0


_config: Config | None = None


def _load_env() -> None:
    # .env sits at the repo root, two levels up from gunstore_mcp/config.py.
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()  # fall back to process env / cwd .env


def get_config() -> Config:
    global _config
    if _config is not None:
        return _config

    _load_env()
    transport = get_transport()
    base_url = (os.environ.get("FRAPPE_BASE_URL") or "").rstrip("/")
    public_url = port = None
    if transport == HTTP:
        # The remote server holds NO key: every call carries the signed-in
        # user's own OAuth token. A key left in the env is ignored, not used.
        api_key = api_secret = ""
        public_url = (os.environ.get("GUNSTORE_MCP_PUBLIC_URL") or "").rstrip("/")
        raw_port = os.environ.get("GUNSTORE_MCP_PORT") or ""
        local = public_url.startswith(("http://localhost:", "http://127.0.0.1:"))
        if not (base_url and (public_url.startswith("https://") or local)
                and public_url.endswith("/mcp") and raw_port.isdigit()):
            raise RuntimeError(
                "http transport needs FRAPPE_BASE_URL, GUNSTORE_MCP_PUBLIC_URL "
                "(https://…/mcp; plain http only for localhost) and GUNSTORE_MCP_PORT."
            )
        port = int(raw_port)
    else:
        api_key = os.environ.get("FRAPPE_API_KEY") or ""
        api_secret = os.environ.get("FRAPPE_API_SECRET") or ""
        if not (base_url and api_key and api_secret):
            raise RuntimeError(
                "Missing Frappe credentials. Set FRAPPE_BASE_URL, FRAPPE_API_KEY and "
                "FRAPPE_API_SECRET in mcp/.env (see mcp/.env.example)."
            )

    raw = os.environ.get("FRAPPE_WRITE_DENYLIST")
    denylist = (
        frozenset(d.strip() for d in raw.split(",") if d.strip())
        if raw is not None
        else frozenset(DEFAULT_WRITE_DENYLIST)
    )

    raw_timeout = os.environ.get("FRAPPE_TIMEOUT") or "30"
    try:
        timeout = int(raw_timeout)
    except ValueError:
        raise RuntimeError(f"FRAPPE_TIMEOUT must be an integer (got {raw_timeout!r}).")

    _config = Config(
        base_url=base_url,
        api_key=api_key,
        api_secret=api_secret,
        timeout=timeout,
        write_denylist=denylist,
        transport=transport,
        public_url=public_url or "",
        port=port or 0,
    )
    return _config
