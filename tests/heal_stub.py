"""A stub Ollama host for the ``llm.self_heal`` tests: /api/ps, /api/tags and slow model requests, on 127.0.0.1.

Every POST (``/api/chat``, ``/v1/systemone``, any path) is a request that
needs the model: it is recorded raw, takes ``load_s`` (a cold load), then
the model counts as loaded and shows in ``/api/ps``. ``ollama = False``
makes every GET a 404, as a server that is not Ollama answers.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Host:
    """What the stub serves, and every request it received."""

    def __init__(self) -> None:
        self.url = ""
        self.ollama = True
        self.listed: list[str] = []   # /api/tags
        self.loaded: list[str] = []   # /api/ps
        self.load_s = 0.0
        self.post_status = 200
        self.posts: list[dict] = []   # {"path", "headers", "body" (bytes)}
        self.gets: list[str] = []
        self.stopped = threading.Event()
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        host: Host = self.server.host
        with host.lock:
            host.gets.append(self.path)
            names = {"/api/ps": host.loaded, "/api/tags": host.listed}.get(self.path)
            body = json.dumps({"models": [{"name": n, "model": n} for n in names or ()]}).encode()
        if not host.ollama or names is None:
            self.send_error(404)
            return
        self._send(200, body)

    def do_POST(self) -> None:
        host: Host = self.server.host
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with host.lock:
            host.posts.append({"path": self.path, "headers": dict(self.headers), "body": raw})
        if host.stopped.wait(host.load_s):
            return
        model = json.loads(raw).get("model", "")
        with host.lock:
            if host.post_status == 200 and model not in host.loaded:
                host.loaded.append(model)
        try:
            self._send(host.post_status, json.dumps({"message": {"content": "{}"}}).encode())
        except (BrokenPipeError, ConnectionResetError):
            # The hook's request gave up (its timeout) while the model loaded: no one is left to answer.
            pass

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Keep the stub's access log out of the test output."""


@contextmanager
def serve() -> Iterator[Host]:
    """A running stub host, shut down on exit."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.host = Host()
    server.host.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server.host
    finally:
        server.host.stopped.set()
        server.shutdown()
        server.server_close()
