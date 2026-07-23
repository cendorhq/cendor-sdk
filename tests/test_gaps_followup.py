"""Gaps-closure follow-up wave (Phase-S remainder): S6 streamed/team ``conversation.id``, S11
streamed-run RAG pre-scope, S13 streamed checkpoints, S14 Bedrock forced-``toolChoice`` structured
output. Offline (no network) via stub clients. Mirrors the TS ``gaps-followup.test.ts``.
"""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import current_trace_id, instrument

from cendor.sdk import Agent, RunComplete, run
from cendor.sdk.providers import _STRUCTURED_OUTPUT_TOOL, BedrockProvider

# --------------------------------------------------------------------------- helpers


class _Sess:
    """A minimal keyed session: ``.id`` + ``snapshot``/``replace`` (the runner's SessionLike)."""

    def __init__(self, id: str) -> None:
        self.id = id
        self._m: list[dict] = []

    def snapshot(self) -> list[dict]:
        return list(self._m)

    def replace(self, msgs: list[dict]) -> None:
        self._m = list(msgs)


def _answer(content: str, finish: str = "stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish, message=SimpleNamespace(content=content, tool_calls=None)
            )
        ],
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
    )


def _stream_answer(text: str):
    """One streamed turn: text deltas + a terminal usage chunk (OpenAI-shape)."""

    def chunk(content=None, finish=None, usage=None):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish
                )
            ],
            usage=usage,
        )

    return iter(
        [
            chunk(text),
            chunk(None, finish="stop", usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2)),
        ]
    )


def _stream_client(turns):
    """A stub OpenAI-shaped client whose ``create(stream=True)`` yields each queued turn."""
    it = iter(turns)

    class Completions:
        def create(self, **kwargs):
            return next(it)

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def _raising_client():
    class Completions:
        def create(self, **kwargs):
            raise AssertionError("the model must not be called on a done-resume (S13)")

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


# --------------------------------------------------------------------------- S6: conversation.id


def test_s6_stream_stamps_conversation_id_from_session():
    sess = _Sess("chat-42")
    agent = Agent(name="a", model="gpt-4o", client=_stream_client([_stream_answer("hi")]))
    events = list(run.stream(agent, "hello", session=sess))
    done = events[-1]
    assert isinstance(done, RunComplete)
    assert done.result.conversation_id == "chat-42"  # S6 — was "" before this wave


def test_s6_stream_no_session_leaves_conversation_id_empty():
    agent = Agent(name="a", model="gpt-4o", client=_stream_client([_stream_answer("hi")]))
    done = list(run.stream(agent, "hello"))[-1]
    assert done.result.conversation_id == ""  # never synthesized


def test_s6_run_agents_stamps_conversation_id():
    sess = _Sess("team-7")
    client = instrument(
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k: _answer("done")))
        )
    )
    a = Agent(name="a", model="gpt-4o", client=client)
    b = Agent(name="b", model="gpt-4o", client=client)
    result = run([a, b], "hi", session=sess)  # team → run_agents
    assert result.conversation_id == "team-7"  # S6 — was "" before this wave


# ------------------------------------------------------------------- S11: streamed RAG pre-scope


def test_s11_stream_retriever_runs_inside_run_trace_scope():
    """The retriever (GLR-4/S11) must run INSIDE ``trace(run_id)`` so an embed it fires is
    attributed to the run. Before this wave it ran before the scope, so its trace id was empty."""
    seen: dict[str, str] = {}

    def retriever(query: str) -> list[str]:
        seen["trace"] = current_trace_id()  # captured at retrieval time
        return ["Refunds are available within 30 days."]

    agent = Agent(
        name="rag",
        model="gpt-4o",
        retriever=retriever,
        client=_stream_client([_stream_answer("ok")]),
    )
    done = list(run.stream(agent, "refund window?"))[-1]
    assert seen["trace"]  # non-empty — retriever ran inside a trace scope (S11)
    assert seen["trace"] == done.result.trace_id  # …the RUN's scope, so the embed is attributed


# ------------------------------------------------------------------- S13: streamed checkpoints


def test_s13_stream_writes_a_finished_checkpoint(tmp_path):
    ckpt = tmp_path / "run.json"
    agent = Agent(name="a", model="gpt-4o", client=_stream_client([_stream_answer("hello")]))
    done = list(run.stream(agent, "hi", checkpoint=str(ckpt)))[-1]
    assert done.result.output == "hello"
    import json

    state = json.loads(ckpt.read_text())
    assert state["done"] is True and state["output"] == "hello"


def test_s13_done_resume_replays_without_calling_the_model(tmp_path):
    ckpt = tmp_path / "run.json"
    agent = Agent(name="a", model="gpt-4o", client=_stream_client([_stream_answer("first")]))
    list(run.stream(agent, "hi", checkpoint=str(ckpt)))  # complete + persist
    # Re-run with a client that raises if the model is called — the done-resume must short-circuit.
    agent2 = Agent(name="a", model="gpt-4o", client=_raising_client())
    events = list(run.stream(agent2, "hi", checkpoint=str(ckpt)))
    assert len(events) == 1 and isinstance(events[0], RunComplete)  # lone terminal event, no deltas
    assert events[0].result.output == "first"


def test_s13_resume_unfinished_skips_prepare_and_continues(tmp_path):
    import json

    ckpt = tmp_path / "run.json"
    # Seed an UNFINISHED checkpoint with a prior user turn already prepared.
    ckpt.write_text(
        json.dumps(
            {
                "run_id": "prev",
                "messages": [{"role": "user", "content": "resumed question"}],
                "done": False,
            }
        )
    )
    seen: dict[str, bool] = {"retriever_ran": False}

    def retriever(query: str) -> list[str]:
        seen["retriever_ran"] = True
        return ["ctx"]

    agent = Agent(
        name="a",
        model="gpt-4o",
        retriever=retriever,
        client=_stream_client([_stream_answer("resumed answer")]),
    )
    done = list(run.stream(agent, "IGNORED on resume", checkpoint=str(ckpt)))[-1]
    assert done.result.output == "resumed answer"
    assert seen["retriever_ran"] is False  # resume skips prepare (S13-D: no re-prepare)
    # the resumed messages (not the new input) drive the run
    assert any(m.get("content") == "resumed question" for m in done.result.messages)


# ------------------------------------------------------------------- S14: Bedrock forced toolChoice


_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def test_s14_bedrock_forces_synthetic_tool_when_tool_less():
    kw = BedrockProvider().build_kwargs(
        "anthropic.claude-3", [], [], "", json_mode=True, output_schema=_SCHEMA
    )
    tc = kw["toolConfig"]
    assert tc["toolChoice"] == {"tool": {"name": _STRUCTURED_OUTPUT_TOOL}}
    assert tc["tools"][0]["toolSpec"]["name"] == _STRUCTURED_OUTPUT_TOOL
    assert tc["tools"][0]["toolSpec"]["inputSchema"]["json"] == _SCHEMA
    # no JSON nudge on the forced-tool path (the tool schema carries the shape)
    assert "system" not in kw or "Respond with ONLY" not in kw["system"][0]["text"]


def test_s14_bedrock_falls_back_to_nudge_when_tools_present():
    from cendor.sdk import tool

    @tool
    def lookup(city: str) -> str:
        """look up"""
        return city

    kw = BedrockProvider().build_kwargs(
        "anthropic.claude-3", [], [lookup], "", json_mode=True, output_schema=_SCHEMA
    )
    # a forced structured-output tool can't coexist with real tools → nudge fallback, no toolChoice
    assert "toolChoice" not in kw.get("toolConfig", {})
    assert "Respond with ONLY" in kw["system"][0]["text"]


def test_s14_bedrock_parse_unwraps_forced_tool_into_content():
    response = {
        "output": {
            "message": {
                "content": [
                    {"toolUse": {"name": _STRUCTURED_OUTPUT_TOOL, "input": {"answer": "42"}}}
                ]
            }
        },
        "stopReason": "tool_use",
    }
    parsed = BedrockProvider().parse(response)
    assert parsed.tool_calls == []  # not surfaced as a tool to execute
    import json

    assert json.loads(parsed.content) == {"answer": "42"}


def test_s14_bedrock_structured_output_end_to_end():
    def converse(**kwargs):
        return {
            "output": {
                "message": {
                    "content": [
                        {"toolUse": {"name": _STRUCTURED_OUTPUT_TOOL, "input": {"answer": "sunny"}}}
                    ]
                }
            },
            "stopReason": "tool_use",
        }

    client = instrument(SimpleNamespace(converse=converse))
    agent = Agent(
        name="b", model="anthropic.claude-3", provider="bedrock", output_type=_SCHEMA, client=client
    )
    result = run(agent, "weather?")
    assert result.output == {"answer": "sunny"}  # forced tool → structured output (S14)
