from pathlib import Path

from cendor_init.initialize import InitOptions, run_init
from cendor_init.templates import SENTINEL


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def test_auto_detects_assistants_and_always_adds_agents(tmp_path: Path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".cursor").mkdir()
    (tmp_path / "CLAUDE.md").write_text("# app\n", encoding="utf-8")
    r = run_init(InitOptions(root=tmp_path))
    assert set(r.chosen) == {"copilot", "cursor", "claude", "agents"}
    assert (tmp_path / ".github/copilot-instructions.md").exists()
    assert (tmp_path / ".cursor/rules/cendor.mdc").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert SENTINEL in _read(tmp_path, ".cursor/rules/cendor.mdc")


def test_never_clobbers_existing_user_content(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("# My rules\n\nDo not delete me.\n", encoding="utf-8")
    run_init(InitOptions(root=tmp_path, assistants=["claude"]))
    out = _read(tmp_path, "CLAUDE.md")
    assert "# My rules" in out
    assert "Do not delete me." in out
    assert "Calling Cendor" in out


def test_idempotent_re_run(tmp_path: Path):
    run_init(InitOptions(root=tmp_path, assistants=["agents"]))
    first = _read(tmp_path, "AGENTS.md")
    r2 = run_init(InitOptions(root=tmp_path, assistants=["agents"]))
    assert _read(tmp_path, "AGENTS.md") == first
    assert r2.actions[0].status == "updated"


def test_dry_run_writes_nothing(tmp_path: Path):
    r = run_init(InitOptions(root=tmp_path, all=True, dry_run=True))
    assert all(a.status.startswith("would-") for a in r.actions)
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / ".cursor/rules/cendor.mdc").exists()


def test_skips_owned_file_unless_force(tmp_path: Path):
    p = tmp_path / ".cursor/rules/cendor.mdc"
    p.parent.mkdir(parents=True)
    p.write_text("my own unrelated rule\n", encoding="utf-8")
    skipped = run_init(InitOptions(root=tmp_path, assistants=["cursor"]))
    assert skipped.actions[0].status == "skipped"
    assert _read(tmp_path, ".cursor/rules/cendor.mdc") == "my own unrelated rule\n"
    forced = run_init(InitOptions(root=tmp_path, assistants=["cursor"], force=True))
    assert forced.actions[0].status == "updated"
    assert SENTINEL in _read(tmp_path, ".cursor/rules/cendor.mdc")


def test_mcp_writes_config_only_where_absent(tmp_path: Path):
    import json

    run_init(InitOptions(root=tmp_path, assistants=["agents"], mcp=True))
    assert (
        json.loads(_read(tmp_path, ".cursor/mcp.json"))["mcpServers"]["cendor"]["url"]
        == "https://mcp.cendor.ai"
    )
    assert json.loads(_read(tmp_path, ".vscode/mcp.json"))["servers"]["cendor"]["type"] == "http"
    r2 = run_init(InitOptions(root=tmp_path, assistants=["agents"], mcp=True))
    assert any(a.path == ".cursor/mcp.json" and a.status == "skipped" for a in r2.actions)


def test_scaffold_python(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nversion="0.1.0"\n', encoding="utf-8"
    )
    run_init(InitOptions(root=tmp_path, assistants=["agents"], scaffold=True))
    body = _read(tmp_path, "cendor_quickstart.py")
    assert "from cendor.core import instrument" in body
    assert '@budget(usd=0.50, on_exceed="raise")' in body


def test_scaffold_node(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name":"x","version":"1.0.0"}', encoding="utf-8")
    run_init(InitOptions(root=tmp_path, assistants=["agents"], scaffold=True))
    body = _read(tmp_path, "cendor-quickstart.mjs")
    assert "budget({ usd: 0.5" in body
    assert "instrument(new OpenAI())" in body
