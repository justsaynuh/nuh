"""A stub model for tests that run nuh's LLM step: an Ollama /api/chat server on 127.0.0.1, in-process.

The ``stub_model`` fixture (conftest) starts one. It answers every request
with the verdict set by ``answer`` (or by ``respond``, from the request) and
records each request, so "the LLM was called" means a request reached it.
No test reaches a real model, or the developer's config and log.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def reply(decision: str, reasoning: str = "looks routine") -> dict:
    """An Ollama /api/chat reply whose message is nuh's verdict JSON."""
    return {"message": {"content": json.dumps({"decision": decision, "reasoning": reasoning})}}


def llm_config(url: str, **extra: object) -> dict:
    """``llm`` enabled with the stub as the only provider (``ollama``)."""
    return {"enabled": True, "providers": ["ollama"],
            "ollama": {"url": f"{url}/api/chat", "model": "stub", "timeout": 5}, **extra}


def last_entry(path: Path) -> dict:
    """The newest decision-log entry in *path*."""
    return json.loads(path.read_text().splitlines()[-1])


def closed_port() -> int:
    """A loopback port nothing listens on (bound, then released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StubModel:
    """What the stub answers, and every request it received."""

    def __init__(self) -> None:
        self.url = ""
        self.status = 200
        self.body = json.dumps(reply("allow")).encode()
        self.delay = 0.0
        # Seconds between single bytes of the reply when set: the body alone, or the whole
        # response from the status line on with drip_headers. Each wait is far below any
        # socket timeout, so only a deadline on the whole request stops the reader.
        self.drip = 0.0
        self.drip_headers = False
        self.stopped = threading.Event()  # set at shutdown, so a drip in progress ends
        self.requests: list[dict] = []
        # When set, answers each request from its body.
        self.respond: Callable[[dict], dict] | None = None

    def answer(self, decision: str = "", reasoning: str = "looks routine", *, status: int = 200,
               body: object = None) -> None:
        """Answer *decision* with *reasoning*; or *body* (bytes or JSON) with *status* as is."""
        self.status = status
        if body is None:
            body = reply(decision, reasoning)
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()

    @property
    def last(self) -> dict:
        return self.requests[-1]["body"]

    @property
    def last_prompt(self) -> str:
        """The system and user text of the newest request."""
        return "\n\n".join(m["content"] for m in self.last["messages"])


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        stub = self.server.stub
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        request = json.loads(raw)
        stub.requests.append({"path": self.path, "headers": dict(self.headers), "body": request})
        time.sleep(stub.delay)
        body = json.dumps(stub.respond(request)).encode() if stub.respond else stub.body
        try:
            if stub.drip_headers:
                head = (f"HTTP/1.1 {stub.status} OK\r\nContent-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\n\r\n").encode()
                self._drip(head + body, stub)
                return
            self.send_response(stub.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if stub.drip:
                self._drip(body, stub)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # A test's client gave up (timeout) and closed the socket: no one is left to answer.
            pass

    def _drip(self, data: bytes, stub: StubModel) -> None:
        """Write *data* one byte per ``stub.drip`` seconds, until it is all out or the stub stops."""
        for i in range(len(data)):
            if stub.stopped.wait(stub.drip):
                return
            self.wfile.write(data[i:i + 1])
            self.wfile.flush()

    def log_message(self, *args: object) -> None:
        """Keep the stub's access log out of the test output."""


@contextmanager
def serve() -> Iterator[StubModel]:
    """A running stub, shut down on exit."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    server.stub = StubModel()
    server.stub.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server.stub
    finally:
        server.stub.stopped.set()
        server.shutdown()
        server.server_close()
