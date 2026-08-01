"""Production hardening: retries recover, checkpointed runs resume, durable memory (plan §7 P4)."""

from __future__ import annotations

import json
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


# ------------------------------------------------------ resuming a FINISHED (done) checkpoint
#
# Regression: a done checkpoint was consulted only by the resume-from helper, which returned None
# on done → the caller treated it as "no checkpoint" and re-ran the whole loop (model + tools).
# A finished run must now short-circuit to the stored output with zero model/tool calls.


def _finished_ckpt(tmp_path, output):
    path = tmp_path / "done.ckpt.json"
    path.write_text(
        json.dumps(
            {
                "run_id": "r1",
                "messages": [
                    {"role": "user", "content": "weather in Paris?"},
                    {"role": "assistant", "content": output},
                ],
                "done": True,
                "output": output,
            }
        )
    )
    return str(path)


def test_finished_checkpoint_resume_does_not_rerun(tmp_path):
    """Resuming a done checkpoint returns the stored output without re-invoking model or tools."""
    path = _finished_ckpt(tmp_path, "It's sunny in Paris.")
    _tool_runs["n"] = 0
    model_calls = {"n": 0}

    def create(**kwargs):
        model_calls["n"] += 1
        return _text_response("SHOULD NOT BE CALLED")

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], client=_stub(create))
    result = run(agent, "weather in Paris?", checkpoint=path)

    assert result.output == "It's sunny in Paris."
    assert model_calls["n"] == 0  # 0 model calls on a finished-run resume
    assert _tool_runs["n"] == 0  # 0 tool invocations
    assert result.steps == []  # no bus events on a resume


async def test_finished_checkpoint_resume_does_not_rerun_async(tmp_path):
    """Same short-circuit on the async single-agent path."""
    path = _finished_ckpt(tmp_path, "It's sunny in Paris.")
    _tool_runs["n"] = 0
    model_calls = {"n": 0}

    def create(**kwargs):
        model_calls["n"] += 1
        return _text_response("SHOULD NOT BE CALLED")

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], client=_stub(create))
    result = await run.aio(agent, "weather in Paris?", checkpoint=path)

    assert result.output == "It's sunny in Paris."
    assert model_calls["n"] == 0
    assert _tool_runs["n"] == 0
    assert result.steps == []


# ------------------------------------------ resuming a SETTLED (unfinished-but-complete) checkpoint
#
# The crash window: every runner path saves the answering turn with done=False BEFORE the done=True
# save lands, so a crash there leaves an "unfinished" checkpoint whose transcript already ends with
# the final assistant answer. Resuming that used to re-enter the model loop on a semantically
# complete conversation — and what a model does with its own finished transcript is undefined;
# re-doing the task, tools included, is a legitimate sample (measured live by the external suite:
# BUG-sdk-resume-recalls-a-tool-already-in-the-replayed-messages). finished() now settles the shape.


def _settled_ckpt(tmp_path, answer, *, tail=None, output=None):
    """An UNFINISHED checkpoint whose transcript ends with `tail` (default: a final answer)."""
    messages = [
        {"role": "user", "content": "weather in Paris?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "get_weather",
            "content": "Sunny in Paris",
        },
    ]
    messages.extend(tail if tail is not None else [{"role": "assistant", "content": answer}])
    path = tmp_path / "settled.ckpt.json"
    path.write_text(
        json.dumps({"run_id": "r-settled", "messages": messages, "done": False, "output": output})
    )
    return str(path)


def test_settled_checkpoint_resume_does_not_reenter_the_loop(tmp_path):
    """An unfinished checkpoint ending in a final assistant answer is finished in substance: the
    resume must return that answer with ZERO model calls and ZERO tool runs (done-resume parity)."""
    path = _settled_ckpt(tmp_path, "It's sunny in Paris.")
    _tool_runs["n"] = 0
    model_calls = {"n": 0}

    def create(**kwargs):
        model_calls["n"] += 1
        return _text_response("SHOULD NOT BE CALLED")

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], client=_stub(create))
    result = run(agent, "weather in Paris?", checkpoint=path)

    assert result.output == "It's sunny in Paris."
    assert model_calls["n"] == 0  # the loop was never re-entered
    assert _tool_runs["n"] == 0  # the completed tool was not re-run
    assert result.steps == []  # no bus events on a resume
    assert result.trace_id == "r-settled"  # done-resume parity: the stored run id, no fresh mint
    assert result.incomplete is False


async def test_settled_checkpoint_resume_does_not_reenter_the_loop_async(tmp_path):
    """Same settle on the async single-agent path."""
    path = _settled_ckpt(tmp_path, "It's sunny in Paris.")
    _tool_runs["n"] = 0
    model_calls = {"n": 0}

    def create(**kwargs):
        model_calls["n"] += 1
        return _text_response("SHOULD NOT BE CALLED")

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], client=_stub(create))
    result = await run.aio(agent, "weather in Paris?", checkpoint=path)

    assert result.output == "It's sunny in Paris."
    assert model_calls["n"] == 0
    assert _tool_runs["n"] == 0
    assert result.trace_id == "r-settled"


def test_settled_checkpoint_keeps_stored_output_over_last_message(tmp_path):
    """A stream-path save carries `output` (possibly guardrail-transformed) with done=False: the
    settle must prefer the stored output over the raw last-message content."""
    path = _settled_ckpt(tmp_path, "raw answer", output="gated answer")
    agent = Agent(name="assistant", model="gpt-4o", client=_stub(lambda **kw: _text_response("no")))
    result = run(agent, "weather in Paris?", checkpoint=path)
    assert result.output == "gated answer"


def test_mid_run_checkpoint_still_resumes_through_the_loop(tmp_path):
    """Negative shape: a transcript ending at a TOOL RESULT is genuinely mid-run — the loop must
    re-enter (one model call) and continue from the saved result without the SDK re-running it."""
    path = _settled_ckpt(tmp_path, "", tail=[])  # ends at the tool-result message
    _tool_runs["n"] = 0
    model_calls = {"n": 0}

    def create(**kwargs):
        model_calls["n"] += 1
        return _text_response("It's sunny in Paris.")

    agent = Agent(name="assistant", model="gpt-4o", tools=[get_weather], client=_stub(create))
    result = run(agent, "weather in Paris?", checkpoint=path)

    assert result.output == "It's sunny in Paris."
    assert model_calls["n"] == 1  # the loop ran — this shape is NOT settled
    assert _tool_runs["n"] == 0  # the saved tool result was replayed, not re-executed by the SDK


def test_empty_answer_tail_is_not_settled(tmp_path):
    """Conservative predicate: an assistant tail with empty content falls through to the loop
    (status quo) rather than being declared a final answer."""
    path = _settled_ckpt(tmp_path, "", tail=[{"role": "assistant", "content": ""}])
    model_calls = {"n": 0}

    def create(**kwargs):
        model_calls["n"] += 1
        return _text_response("recovered answer")

    agent = Agent(name="assistant", model="gpt-4o", client=_stub(create))
    result = run(agent, "weather in Paris?", checkpoint=path)
    assert result.output == "recovered answer"
    assert model_calls["n"] == 1


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


# --- parameter-swap repair (Azure/Foundry reasoning deployments) --------------------------------
#
# Measured live 2026-07-31 against a real Foundry deployment: `Agent(max_tokens=…)` with
# `provider="azure"` 400s outright — `Unsupported parameter: 'max_tokens' is not supported with
# this model. Use 'max_completion_tokens' instead.` It cannot be predicted from the model id,
# because on Azure the id is the *deployment* name the user chose.


class _SwapError(Exception):
    """The provider's 400, shaped like OpenAI's."""

    def __init__(self) -> None:
        super().__init__(
            "Error code: 400 - {'error': {'message': \"Unsupported parameter: 'max_tokens' is "
            "not supported with this model. Use 'max_completion_tokens' instead.\", 'type': "
            "'invalid_request_error', 'param': 'max_tokens', 'code': 'unsupported_parameter'}}"
        )


def _swapping_create(seen: list[dict]):
    def create(**kwargs):
        seen.append(dict(kwargs))
        if "max_tokens" in kwargs:
            raise _SwapError()
        return {"ok": True, "max_completion_tokens": kwargs.get("max_completion_tokens")}

    return create


def test_param_swap_repairs_once_with_no_retry_policy():
    from cendor.sdk.resilience import call_with_retry

    seen: list[dict] = []
    out = call_with_retry(_swapping_create(seen), {"model": "dep", "max_tokens": 64}, None)
    assert out == {"ok": True, "max_completion_tokens": 64}
    assert len(seen) == 2  # exactly one repair, not a loop
    assert "max_tokens" not in seen[1] and seen[1]["max_completion_tokens"] == 64


def test_param_swap_repairs_under_a_retry_policy_without_spending_an_attempt():
    from cendor.sdk.resilience import RetryPolicy, call_with_retry

    slept: list[float] = []
    policy = RetryPolicy(max_attempts=2, sleep=slept.append)
    seen: list[dict] = []
    out = call_with_retry(_swapping_create(seen), {"model": "dep", "max_tokens": 8}, policy)
    assert out["ok"] is True
    assert len(seen) == 2
    assert slept == [], "a rename is not a transient failure — no backoff"


async def test_param_swap_repairs_on_the_async_path():
    from cendor.sdk.resilience import acall_with_retry

    seen: list[dict] = []
    sync = _swapping_create(seen)

    async def create(**kwargs):
        return sync(**kwargs)

    out = await acall_with_retry(create, {"model": "dep", "max_tokens": 16}, None)
    assert out == {"ok": True, "max_completion_tokens": 16}
    assert len(seen) == 2


def test_param_swap_does_not_fire_on_an_unrelated_error():
    from cendor.sdk.resilience import call_with_retry

    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise ValueError("something else entirely")

    with pytest.raises(ValueError, match="something else"):
        call_with_retry(create, {"model": "dep", "max_tokens": 8}, None)
    assert calls["n"] == 1  # no repair attempt


def test_param_swap_does_not_fire_when_the_named_key_is_absent():
    """Negative control: the message names `max_tokens`, but the request never sent one — so there
    is nothing to rename and the error must reach the caller unchanged."""
    from cendor.sdk.resilience import call_with_retry

    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise _SwapError()

    with pytest.raises(_SwapError):
        call_with_retry(create, {"model": "dep"}, None)
    assert calls["n"] == 1


def test_param_swap_raises_if_the_repaired_call_still_fails():
    """It repairs once. A second failure is the caller's."""
    from cendor.sdk.resilience import call_with_retry

    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        raise _SwapError() if calls["n"] == 1 else RuntimeError("still broken")

    with pytest.raises(RuntimeError, match="still broken"):
        call_with_retry(create, {"model": "dep", "max_tokens": 8}, None)
    assert calls["n"] == 2
