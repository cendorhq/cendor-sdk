"""Production hardening: retries recover, checkpointed runs resume, durable memory (plan §7 P4)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import respx

from cendor.sdk import Agent, RetryPolicy, SQLiteSessionStore, run, tool


def _text_response(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=content, tool_calls=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _tool_call_response(name, args_json, call_id="call_1"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id=call_id,
                            type="function",
                            function=SimpleNamespace(name=name, arguments=args_json),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=8),
    )


class TransientError(Exception):
    status_code = 503


def _stub(create_fn) -> object:
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_fn)))


def test_retry_recovers_from_transient_failure():
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("temporarily unavailable")
        return _text_response("recovered")

    agent = Agent(name="a", model="gpt-4o", instructions="x", client=_stub(create))
    retry = RetryPolicy(max_attempts=5, backoff_base=0, sleep=lambda _: None)
    result = run(agent, "hi", retry=retry)

    assert result.output == "recovered"
    assert calls["n"] == 3  # two failures, then success
    assert len(result.llm_steps) == 1  # only the successful call emits an LLMCall


def test_retry_gives_up_and_reraises():
    def create(**kwargs):
        raise TransientError("always down")

    agent = Agent(name="a", model="gpt-4o", instructions="x", client=_stub(create))
    retry = RetryPolicy(max_attempts=3, backoff_base=0, sleep=lambda _: None)
    with pytest.raises(TransientError):
        run(agent, "hi", retry=retry)


def test_non_transient_error_is_not_retried():
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise ValueError("bad request")  # not transient

    agent = Agent(name="a", model="gpt-4o", instructions="x", client=_stub(create))
    retry = RetryPolicy(max_attempts=5, backoff_base=0, sleep=lambda _: None)
    with pytest.raises(ValueError):
        run(agent, "hi", retry=retry)
    assert calls["n"] == 1  # tried once, not retried


# --------------------------------------------------------------------------- checkpoint / resume


_tool_runs = {"n": 0}


@tool
def get_weather(city: str) -> str:
    """Current weather for a city."""
    _tool_runs["n"] += 1
    return f"Sunny in {city}"


class CrashError(Exception):
    """A non-transient crash (simulates the process dying)."""


def test_checkpointed_run_resumes_after_crash(tmp_path):
    _tool_runs["n"] = 0
    path = str(tmp_path / "run.ckpt.json")

    # Attempt 1: turn 1 asks for the tool (runs it, checkpoint saved), turn 2 crashes.
    attempt1 = {"n": 0}

    def create1(**kwargs):
        attempt1["n"] += 1
        if attempt1["n"] == 1:
            return _tool_call_response("get_weather", '{"city": "Paris"}')
        raise CrashError("process died mid-run")

    agent1 = Agent(
        name="assistant",
        model="gpt-4o",
        tools=[get_weather],
        instructions="Use tools.",
        client=_stub(create1),
    )
    with pytest.raises(CrashError):
        run(agent1, "weather in Paris?", checkpoint=path)
    assert _tool_runs["n"] == 1  # the tool ran once before the crash

    # Attempt 2 (resume): a fresh client only needs to produce the final answer.
    def create2(**kwargs):
        return _text_response("It's sunny in Paris.")

    agent2 = Agent(
        name="assistant",
        model="gpt-4o",
        tools=[get_weather],
        instructions="Use tools.",
        client=_stub(create2),
    )
    result = run(agent2, "weather in Paris?", checkpoint=path)

    assert result.output == "It's sunny in Paris."
    assert _tool_runs["n"] == 1  # the tool was NOT re-run on resume (loaded from the checkpoint)
    # the resumed conversation carried the earlier tool result
    assert any(m.get("role") == "tool" for m in result.messages)


# --------------------------------------------------------------------------- durable memory


def test_sqlite_session_store_persists_across_reopen(build, tmp_path):
    db = str(tmp_path / "sessions.db")
    store = SQLiteSessionStore(db)
    session = store.load("user-1")
    assert len(session) == 0

    agent = Agent(name="a", model="gpt-4o", instructions="Remember.")
    with respx.mock:
        respx.post(build.CHAT_URL).mock(return_value=build.resp(build.openai_chat("Hi Alice.")))
        run(agent, "My name is Alice.", session=session)
    store.save("user-1", session)
    store.close()

    reopened = SQLiteSessionStore(db)
    loaded = reopened.load("user-1")
    assert loaded.messages == session.messages
    assert len(loaded) > 0
    assert "user-1" in reopened.ids()
    reopened.close()
