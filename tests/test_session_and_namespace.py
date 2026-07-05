"""Session memory across runs, plus the cardinal namespace + import checks (CLAUDE.md rule 1)."""

from __future__ import annotations

from pathlib import Path

import respx

from cendor.sdk import Agent, Session, run


def test_session_memory_across_runs(build):
    agent = Agent(name="a", model="gpt-4o", instructions="Remember.")
    session = Session()
    with respx.mock:
        respx.post(build.CHAT_URL).mock(
            side_effect=[
                build.resp(build.openai_chat("Hi Alice.")),
                build.resp(build.openai_chat("Your name is Alice.")),
            ]
        )
        run(agent, "My name is Alice.", session=session)
        result = run(agent, "What's my name?", session=session)
    assert "Alice" in result.output
    # both user turns + both assistant turns are retained
    roles = [m["role"] for m in session.messages]
    assert roles.count("user") == 2
    assert roles.count("assistant") == 2


def test_no_top_level_cendor_init():
    """The #1 namespace failure mode: a src/cendor/__init__.py must NOT exist (PEP 420)."""
    src = Path(__file__).resolve().parents[1] / "src"
    assert not (src / "cendor" / "__init__.py").exists()
    assert (src / "cendor" / "sdk" / "__init__.py").exists()  # the subpackage DOES have one


def test_import_cendor_sdk():
    import cendor.sdk

    assert cendor.sdk.__version__
    # the whole bundled stack imports under one namespace
    import cendor.acttrace  # noqa: F401
    import cendor.cassette  # noqa: F401
    import cendor.contextkit  # noqa: F401
    import cendor.core  # noqa: F401
    import cendor.squeeze  # noqa: F401
    import cendor.tokenguard  # noqa: F401
