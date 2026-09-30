# Deploying the remote connectors on a POS host

Each store's connectors run on that store's own POS box, next to the POS, and are
reached under the POS domain — no new DNS, no extra hop:

| Store | cpa (read-only) | full |
|---|---|---|
| OSA (`gunstore-prod`) | `https://pos.oldsteelarsenal.com/connector/cpa/mcp` | `…/connector/full/mcp` |
| CGA (`cga-prod`) | `https://pos.caligunsandammo.com/connector/cpa/mcp` | `…/connector/full/mcp` |

Prerequisites: the POS runs a release with `ffl_core/api/connector.py` and the
Connector Audit Log doctype (1.5.0-beta.11+); the box is logged in to ghcr.io
(it already pulls the POS image).

## First install (per box — every step is a production change, confirm first)

1. **POS OAuth Settings** (Desk → OAuth Settings): tick *Show Auth Server Metadata*
   and *Enable Dynamic Client Registration*; leave *Skip Authorization* unticked.
   Check: `curl -s https://pos.<domain>/.well-known/oauth-authorization-server` lists
   a `registration_endpoint`.
2. **Files**: `mkdir -p ~/gunstore-pos-mcp/deploy`, copy `compose.yaml` there and
   `.env.example` as `.env` (set the store's URL). Nothing secret goes in `.env`.
3. **Start**: `cd ~/gunstore-pos-mcp/deploy && docker compose pull && docker compose up -d`
   → `docker compose ps` shows both services `healthy`;
   `curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8781/mcp` → `401`.
4. **Caddy** (human on the box, per the POS RUNBOOK): back up `/etc/caddy/Caddyfile`,
   paste `caddy-connector.caddy` inside the `pos.<domain>` block above its
   `reverse_proxy`, `caddy validate`, `systemctl reload caddy`.
5. **Verify**:
   - `curl -si -X POST https://pos.<domain>/connector/cpa/mcp` → `401` with
     `resource_metadata="https://pos.<domain>/.well-known/oauth-protected-resource/connector/cpa/mcp"`;
   - that metadata URL → `authorization_servers: ["https://pos.<domain>"]`;
   - the POS itself still works (login page, one report export);
   - add the URL in Claude, sign in, run one read → a row in Connector Audit Log.

## Update

`cd ~/gunstore-pos-mcp/deploy && docker compose pull && docker compose up -d`
(pin with `MCP_TAG=sha-<7>` in `.env` to hold a known-good build).

## Roll back

- Connectors off: `docker compose down` (the POS is untouched).
- Caddy: restore the backup, validate, reload.
- OAuth: untick the two OAuth Settings boxes; revoke tokens under OAuth Bearer Token.
