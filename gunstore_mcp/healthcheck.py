"""Container healthcheck for the remote connector. Healthy = the endpoint answers
401 (up, asking for a token) AND the POS it verifies tokens against answers at
all — a wrong FRAPPE_INTERNAL_URL or Host must not pass as healthy."""
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse


def _status(req: urllib.request.Request) -> int | None:
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return None


def main() -> int:
    endpoint = urllib.request.Request(
        f"http://127.0.0.1:{os.environ['GUNSTORE_MCP_PORT']}/mcp", data=b"{}", method="POST")
    if _status(endpoint) != 401:
        return 1
    base = os.environ["FRAPPE_BASE_URL"].rstrip("/")
    backend = (os.environ.get("FRAPPE_INTERNAL_URL") or base).rstrip("/")
    ping = urllib.request.Request(backend + "/api/method/ping")
    if backend != base:
        ping.add_header("Host", urlparse(base).netloc)
    return 0 if _status(ping) == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
