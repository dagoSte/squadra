"""A local fake of memoranda's MCP door for tests: loopback only, scripted per call.

Shapes follow hosted http.py (session header on initialize, 202 for notifications, tool results as
{content, structuredContent, isError}, 401/403 outside the tool envelope, 429 with Retry-After, 503 busy) and the
pinned core (propose answers status pending | committed; pending reads carry no cursor; decided reads carry
truncated/next, refuse a decided_since older than 30 days, null the content of a `sensitive` rejection and keep only
id, outcome and time for a forgotten one). Captured request headers stay in this process as test evidence.
"""
from __future__ import annotations

import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

TENANT = "acme"


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class Fake:
    def __init__(self, token, auto_scopes=(), page_size=200):
        self.token, self.auto_scopes, self.page_size = token, set(auto_scopes), page_size
        self.script = []
        self.proposals = {}
        self.decisions = []
        self.requests = []
        self.proposes = []
        self.reads = []
        self.counter = 0
        self.lock = threading.Lock()
        self.held, self.release = threading.Event(), threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    fake.handle(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/t/{TENANT}/mcp"

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()

    def new_id(self, prefix):
        self.counter += 1
        return prefix + str(self.counter).rjust(26, "0")

    def decide(self, proposal_id, outcome, *, when=None, reason_code=None, memory_id=None, merged_into=None,
               forgotten=False):
        when = when or datetime.datetime.now(datetime.timezone.utc)
        proposal = self.proposals[proposal_id]
        proposal["status"] = outcome
        entry = {"proposal_id": proposal_id, "outcome": outcome, "decided_at": stamp(when)}
        if not forgotten:
            entry.update(scope=proposal["scope"], content=None if reason_code == "sensitive" else proposal["content"],
                         reason_code=reason_code, decided_by="person", memory_id=memory_id, merged_into=merged_into)
        self.decisions.append(entry)

    @staticmethod
    def _reply(handler, status, body=None, headers=()):
        data = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        handler.send_response(status)
        for name, value in headers:
            handler.send_header(name, value)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _result(self, handler, request_id, payload):
        self._reply(handler, 200, {"jsonrpc": "2.0", "id": request_id, "result": {
            "content": [{"type": "text", "text": json.dumps(payload)}], "structuredContent": payload,
            "isError": "error" in payload}})

    def handle(self, handler):
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length)
        authorization = handler.headers.get("Authorization")
        with self.lock:
            self.requests.append({"authorization": authorization, "session": handler.headers.get("Mcp-Session-Id"),
                                  "at": time.monotonic()})
        if handler.path != f"/t/{TENANT}/mcp":
            return self._reply(handler, 404, {"error": "not found"})
        if authorization != "Bearer " + self.token:
            return self._reply(handler, 401, {"error": "unauthorized"}, [("WWW-Authenticate", "Bearer")])
        try:
            body = json.loads(raw)
        except ValueError:
            return self._reply(handler, 400, {"jsonrpc": "2.0", "id": None,
                                              "error": {"code": -32700, "message": "invalid JSON"}})
        method, request_id, params = body.get("method"), body.get("id"), body.get("params") or {}
        if method == "initialize":
            return self._reply(handler, 200, {"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fake-memoranda", "version": "0"}}}, [("Mcp-Session-Id", "fake-session-1")])
        if isinstance(method, str) and method.startswith("notifications/"):
            return self._reply(handler, 202, b"null")
        if handler.headers.get("Mcp-Session-Id") != "fake-session-1":
            return self._reply(handler, 404, {"error": "unknown session"})
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name == "propose":
            return self._propose(handler, request_id, arguments)
        if name == "review_queue":
            return self._review(handler, request_id, arguments)
        return self._result(handler, request_id, {"error": {"code": "not_found", "message": "unknown tool"}})

    def _propose(self, handler, request_id, arguments):
        with self.lock:
            self.proposes.append({"arguments": arguments, "at": time.monotonic()})
            step = self.script.pop(0) if self.script else "ok"
        if step in ("401", "403"):
            return self._reply(handler, int(step), {"error": "forbidden"})
        if step.startswith(("429", "503")):
            status, _, wait = step.partition(":")
            headers = [("Retry-After", wait)] if wait else []
            return self._reply(handler, int(status), {"error": "rate_limited" if status == "429" else "busy"}, headers)
        if step == "queue_full":
            return self._result(handler, request_id, {"error": {"code": "proposal_queue_full", "message": "full"}})
        if step == "reflect-code":
            return self._result(handler, request_id, {"error": {"code": self.token, "message": self.token}})
        if step == "reflect-message":
            return self._result(handler, request_id, {"error": {
                "code": "duplicate_waiting", "message": "an identical proposal of yours is already waiting: " + self.token}})
        if step == "reflect-id":
            return self._result(handler, request_id, {"proposal_id": self.token, "status": "pending", "conflicts": [],
                                                      "memory_id": None, "redacted": 0, "auto_refusal": None})
        if step == "reflect-malformed":
            return self._reply(handler, 200, ("not json " + self.token).encode())
        waiting = next((pid for pid, p in self.proposals.items() if p["status"] == "pending"
                        and p["scope"] == arguments.get("scope") and p["content"] == arguments.get("content")), None)
        if waiting:
            return self._result(handler, request_id, {"error": {
                "code": "duplicate_waiting", "message": f"an identical proposal of yours is already waiting: {waiting}"}})
        proposal_id = self.new_id("prp_")
        committed = arguments.get("scope") in self.auto_scopes
        memory_id = self.new_id("mem_") if committed else None
        self.proposals[proposal_id] = {"scope": arguments.get("scope"), "content": arguments.get("content"),
                                       "status": "approved" if committed else "pending"}
        if step == "lost":
            handler.close_connection = True
            return None
        if step == "hold":
            self.held.set()
            self.release.wait(60)
        self._result(handler, request_id, {"proposal_id": proposal_id,
                                           "status": "committed" if committed else "pending", "conflicts": [],
                                           "memory_id": memory_id, "redacted": 0, "auto_refusal": None})

    def _review(self, handler, request_id, arguments):
        with self.lock:
            self.reads.append(dict(arguments))
        since = arguments.get("decided_since")
        if since is None:
            pending = [{"id": pid, "scope": p["scope"], "content": p["content"], "status": "pending"}
                       for pid, p in self.proposals.items() if p["status"] == "pending"]
            return self._result(handler, request_id, {"proposals": pending[:20]})
        start = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
        if start < datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30):
            return self._result(handler, request_id, {"error": {
                "code": "invalid_arguments", "message": "decided_since reaches back more than 30 days"}})
        after = arguments.get("after")
        rows = sorted(self.decisions, key=lambda d: (d["decided_at"], d["proposal_id"]))
        if after:
            rows = [d for d in rows if (d["decided_at"], d["proposal_id"]) > (since, after)]
        else:
            rows = [d for d in rows if d["decided_at"] >= since]
        page = rows[:min(self.page_size, int(arguments.get("limit") or 200))]
        truncated = len(rows) > len(page)
        payload = {"decisions": page, "truncated": truncated}
        if truncated:
            payload["next"] = {"decided_since": page[-1]["decided_at"], "after": page[-1]["proposal_id"]}
        self._result(handler, request_id, payload)
