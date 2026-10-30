"""The system 1 test stub (a /v1/systemone server on 127.0.0.1, in-process) and the hook payload helpers.

The ``s1`` and ``env`` fixtures (conftest.py) start the stub and give a test
a fake HOME; test_system1*.py drive the hook with ``use``, ``invoke`` and ``bash``.
"""

import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nuh import config, hook
from nuh.config import _merge_configs

QUESTIONS = ["destructive", "out_of_scope", "network", "verdict", "risk"]


def answers(verdict: str | None = "allow", p: float = 0.9, destructive: float = 0.05, risk: float = 0.3) -> dict:
    """A /v1/systemone reply for the shipped default's questions; verdict None leaves it unanswered."""
    rest = round((1 - p) / 2, 4)
    out = {"destructive": {"type": "noul", "noul": destructive},
           "out_of_scope": {"type": "noul", "noul": 0.02},
           "network": {"type": "noul", "noul": 0.01},
           "risk": {"type": "score", "score": risk, "confidence": 0.9, "probabilities": {}}}
    if verdict is not None:
        probs = {o: (p if o == verdict else rest) for o in ("allow", "ask", "block")}
        out["verdict"] = {"type": "choice", "choice": verdict, "confidence": p, "probabilities": probs}
    return {"answers": out, "model": "stub", "usage": {"input_tokens": 100, "output_tokens": 0}}


class Server:
    def __init__(self) -> None:
        self.url = ""
        self.status = 200
        self.body: object = answers()
        self.delay = 0.0
        self.requests: list[dict] = []

    @property
    def last(self) -> dict:
        return self.requests[-1]["body"]


class S1Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server = self.server.stub
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        server.requests.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(raw)})
        time.sleep(server.delay)
        body = server.body if isinstance(server.body, bytes) else json.dumps(server.body).encode()
        try:
            self.send_response(server.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The timeout test's client has already given up and closed the socket.
            pass

    def log_message(self, *args: object) -> None:
        """Keep the stub's access log out of the test output."""


def serve(handler) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return server, thread


def use(url: str, providers: tuple = ("system1",), **llm_extra: object) -> None:
    section = {"url": url, "model": "nimble:9b", "keep_alive": "24h", "timeout": 5}
    section.update(llm_extra.pop("system1", {}))
    llm = {"enabled": True, "providers": list(providers), "system1": section, **llm_extra}
    config._cached_config = _merge_configs({"llm": llm}, {})


def invoke(monkeypatch, payload: dict) -> dict:
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", out)
    hook.main()
    return json.loads(out.getvalue())["hookSpecificOutput"]


def last_entry(env: dict) -> dict:
    return json.loads(env["log"].read_text().splitlines()[-1])


def bash(project_root: str, command: str = "somethingunknown123 --flag", **extra) -> dict:
    return {"tool_name": "Bash", "session_id": "sess", "tool_use_id": "toolu_s1", "cwd": project_root,
            "tool_input": {"command": command, "description": "probe the thing", **extra}}
