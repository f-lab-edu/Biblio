#!/usr/bin/env python3
"""Fake embedding HTTP server for local development.

Zero external dependencies — uses only Python stdlib.

Usage:
    python tools/fake_embedding_server.py          # default port 8090
    python tools/fake_embedding_server.py 9000     # custom port

Endpoints:
    GET  /health  → {"status": "ok", "model_version": "fake-v001"}
    POST /embed   → {"embeddings": [[...], ...]}   (384-dim random vectors)
"""

import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

EMBEDDING_DIM = 384
MODEL_VERSION = "fake-v001"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json_response({"status": "ok", "model_version": MODEL_VERSION})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/embed":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            texts = body.get("texts", [])
            embeddings = [[secrets.randbelow(10000) / 10000 for _ in range(EMBEDDING_DIM)] for _ in texts]
            self._json_response({"embeddings": embeddings})
        else:
            self.send_error(404)

    def _json_response(self, data: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        payload = json.dumps(data).encode()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[fake-embed] {fmt % args}")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"Fake embedding server on http://0.0.0.0:{port}")
    print(f"  GET  /health → model_version={MODEL_VERSION}")
    print(f"  POST /embed  → {EMBEDDING_DIM}-dim random vectors")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
