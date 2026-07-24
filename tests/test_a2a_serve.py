"""G5 (coverage): A2A ``serve()`` real HTTP round-trip over loopback. Previously only the in-process
``A2AClient``/``A2AServer.handle`` path was exercised; ``serve()`` (stdlib ``ThreadingHTTPServer``)
had no test. Binds ``127.0.0.1:0`` (ephemeral), serves in a daemon thread, and drives it with
``urllib`` — the agent card via ``GET`` and a JSON-RPC ``message/send`` via ``POST``. Loopback only;
offline (stub client). Mirrors the TS ``serve()`` round-trip added the same wave."""

from __future__ import annotations

import json
import threading
import urllib.request
from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent
from cendor.sdk.a2a import serve


def _client(text: str):
    def create(**k):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=text, tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
        )

    return instrument(
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    )


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - loopback only
        return json.loads(resp.read())


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - loopback only
        return json.loads(resp.read())


def test_a2a_serve_http_round_trip_over_loopback():
    agent = Agent(name="greeter", model="gpt-4o", client=_client("Hello over HTTP."))
    srv = serve(agent)  # binds 127.0.0.1:0 by default
    host, port = srv.server_address[0], srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://{host}:{port}"
        card = _get(f"{base}/.well-known/agent-card.json")
        assert card["name"] == "greeter"
        assert card["capabilities"]["streaming"] is False  # honestly advertised

        rpc = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "hi"}]}},
        }
        response = _post(f"{base}/", rpc)
        parts = response["result"]["parts"]
        reply = "\n".join(p.get("text", "") for p in parts if p.get("kind") == "text")
        assert reply == "Hello over HTTP."
        assert response["result"]["metadata"]["trace_id"]  # governance metadata rides the wire
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
