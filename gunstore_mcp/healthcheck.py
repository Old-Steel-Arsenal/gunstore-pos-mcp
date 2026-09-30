"""Container healthcheck for the remote connector: the endpoint answering 401
(asking for a token) means it is up. Exit 0 = healthy."""
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{os.environ['GUNSTORE_MCP_PORT']}/mcp",
                                 data=b"{}", method="POST")
    try:
        urllib.request.urlopen(req, timeout=4)
    except urllib.error.HTTPError as e:
        return 0 if e.code == 401 else 1
    except OSError:
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
