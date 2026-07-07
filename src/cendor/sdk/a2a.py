"""A2A: expose a governed ``cendor.sdk`` agent over the Agent-to-Agent protocol.

A minimal, dependency-free implementation of A2A's JSON-RPC ``message/send`` plus the agent card.
``A2AServer.handle(request)`` runs the agent and returns an A2A message result (with governance
metadata: trace id, cost); ``A2AClient`` calls a server **in-process** (no socket) for tests and
embedding. ``serve()`` is an optional local HTTP server (stdlib only — local-first, never required).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .agent import Agent
from .runner import run


def _text_of_message(message: dict) -> str:
    parts = message.get("parts") or []
    texts = [str(p.get("text", "")) for p in parts if p.get("kind", "text") == "text"]
    return "\n".join(t for t in texts if t)


def _message_result(text: str, metadata: dict) -> dict:
    return {
        "messageId": uuid.uuid4().hex,
        "role": "agent",
        "parts": [{"kind": "text", "text": text}],
        "kind": "message",
        "metadata": metadata,
    }


class A2AServer:
    """Serve one agent over A2A. In-process via :meth:`handle`; over HTTP via :func:`serve`."""

    def __init__(self, agent: Agent, *, audit: Any = None) -> None:
        self.agent = agent
        self.audit = audit

    def agent_card(self) -> dict:
        """The A2A agent card advertising this agent's identity and skills."""
        return {
            "name": self.agent.name,
            "description": self.agent.instructions or f"The {self.agent.name} agent.",
            "version": "1.0.0",
            "protocolVersion": "0.2",
            "capabilities": {"streaming": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": t.name,
                    "name": t.name,
                    "description": t.description,
                }
                for t in self.agent.toolset
            ],
        }

    def handle(self, request: dict) -> dict:
        """Dispatch a JSON-RPC A2A request. Supports ``message/send``."""
        rpc_id = request.get("id")
        method = request.get("method")
        if method != "message/send":
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        message = (request.get("params") or {}).get("message") or {}
        text = _text_of_message(message)
        result = run(self.agent, text, audit=self.audit)
        metadata = {
            "trace_id": result.trace_id,
            "cost_usd": str(result.cost.amount),
            "agents": result.agents,
        }
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": _message_result(str(result.output), metadata),
        }


class A2AClient:
    """Call an :class:`A2AServer` in-process (no network) — the offline/embedded path."""

    def __init__(self, server: A2AServer) -> None:
        self.server = server

    def card(self) -> dict:
        return self.server.agent_card()

    def send(self, text: str) -> str:
        """Send a user message and return the agent's text reply."""
        response = self.server.handle(self._request(text))
        if "error" in response:
            raise RuntimeError(f"A2A error: {response['error']}")
        parts = response["result"]["parts"]
        return "\n".join(p.get("text", "") for p in parts if p.get("kind") == "text")

    def send_full(self, text: str) -> dict:
        """Send a message and return the full A2A message result (incl. governance metadata)."""
        return self.server.handle(self._request(text))["result"]

    @staticmethod
    def _request(text: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": text}]}},
        }


def serve(agent: Agent, *, host: str = "127.0.0.1", port: int = 0, audit: Any = None) -> Any:
    """Start a local A2A HTTP server (stdlib ``http.server``). Optional, opt-in; returns the server.

    The agent card is served at ``GET /.well-known/agent-card.json``; JSON-RPC at ``POST /``.
    Call ``.serve_forever()`` (blocking) or run it in a thread; ``.shutdown()`` to stop.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    a2a = A2AServer(agent, audit=audit)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
            if self.path.rstrip("/").endswith("agent-card.json") or self.path == "/":
                self._send(200, a2a.agent_card())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length) or b"{}")
            self._send(200, a2a.handle(request))

        def log_message(self, *args: Any) -> None:  # silence the default stderr logging
            return

    return ThreadingHTTPServer((host, port), Handler)
