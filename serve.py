"""Start the collector's local web server.

    python serve.py [--port 8000] [--no-browser]
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def free_port(start: int = 8000, stop: int = 8060) -> int:
    for port in range(start, stop + 1):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"No free port between {start} and {stop}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    import uvicorn

    from dotbo.web.server import app

    port = args.port or free_port()
    url = f"http://{args.host}:{port}"
    print(f"\nDoT Blocking Order Collector\n{url}\n")
    print("Leave this window open while you use it. Close it, or press Ctrl-C, to stop.\n")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
