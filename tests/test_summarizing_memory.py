"""SummarizingSession — rolling summarization of old turns into a durable memory note (offline)."""

from __future__ import annotations

from types import SimpleNamespace

from cendor.core import instrument

from cendor.sdk import Agent, SummarizingSession, run


def _fake_summarizer(old, prior):
    """Deterministic offline summarizer: count folded messages, threading the prior summary."""
    base = f"{prior} " if prior else ""
    return f"{base}[folded {len(old)}]"


def _msgs(n):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(n)]


def test_folds_old_turns_into_memory_note():
    mem = SummarizingSession(summarizer=_fake_summarizer, max_messages=4, keep_recent=2)
    mem.replace(_msgs(5))  # 5 > 4 → summarize
    # head is a memory note, then the 2 most-recent turns kept verbatim
    assert mem.messages[0]["role"] == "system"
    assert mem.messages[0]["content"].startswith("Conversation summary so far:\n")
    assert "[folded 3]" in mem.messages[0]["content"]
    assert [m["content"] for m in mem.messages[1:]] == ["m3", "m4"]
    assert len(mem) == 3  # note + 2 recent (bounded)


def test_prior_summary_is_threaded_and_stays_bounded():
    mem = SummarizingSession(summarizer=_fake_summarizer, max_messages=4, keep_recent=2)
    mem.replace(_msgs(5))  # first fold
    first_note = mem.messages[0]["content"]
    # grow again: prior note + 2 recent + 3 new = 6 > 4 → re-summarize, threading the prior note
    mem.replace(mem.messages + _msgs(3))
    note = mem.messages[0]["content"]
    assert note != first_note  # updated
    assert "[folded" in note and note.count("[folded") >= 2  # prior summary carried forward
    assert len(mem) <= 4  # stays bounded across rounds


def _stub_client(answer="ok"):
    class Completions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=answer, tool_calls=None),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=1),
            )

    return instrument(SimpleNamespace(chat=SimpleNamespace(completions=Completions())))


def test_summarizing_session_across_runs():
    """After several runs the session stays bounded but retains a memory note."""
    mem = SummarizingSession(summarizer=_fake_summarizer, max_messages=4, keep_recent=2)
    agent = Agent(name="a", model="gpt-4o", client=_stub_client(), instructions="x")
    for i in range(4):
        run(agent, f"turn {i}", session=mem)
    assert len(mem) <= 4
    assert mem.messages[0]["content"].startswith("Conversation summary so far:\n")
