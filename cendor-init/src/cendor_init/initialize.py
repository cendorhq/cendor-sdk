"""``init`` — make a project Cendor-ready and Cendor-fluent for its AI assistant.

Writes the matching assistant rules file(s) from the vendored templates (idempotent, marker
delimited, never clobbers user content), optionally the MCP connect config and a correct starter,
then reports what it did. Offline: no network, no key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import templates
from .detect import Detected, detect_project
from .io import upsert_managed

ALL_ASSISTANTS = ("copilot", "cursor", "agents", "claude", "windsurf")


@dataclass
class InitOptions:
    root: Path
    assistants: list[str] | None = None
    all: bool = False
    mcp: bool = False
    scaffold: bool = False
    force: bool = False
    dry_run: bool = False


@dataclass
class FileAction:
    path: str
    status: str
    note: str | None = None


@dataclass
class InitResult:
    detected: Detected
    chosen: list[str]
    actions: list[FileAction] = field(default_factory=list)
    mcp_guidance: str = ""


# assistant -> (relative path, mode, body). mode: "owned" (verbatim file) | "shared" (managed block).
def _target(assistant: str) -> tuple[str, str, str]:
    if assistant == "copilot":
        return ".github/copilot-instructions.md", "shared", templates.COPILOT
    if assistant == "cursor":
        return ".cursor/rules/cendor.mdc", "owned", templates.CURSOR
    if assistant == "agents":
        return "AGENTS.md", "shared", templates.AGENTS
    if assistant == "claude":
        return "CLAUDE.md", "shared", templates.CLAUDE
    if assistant == "windsurf":
        # Windsurf has no dedicated template; the generic AGENTS body is the right cheatsheet.
        return ".windsurf/rules", "shared", templates.AGENTS
    raise ValueError(f"unknown assistant: {assistant}")


def _choose(opts: InitOptions, detected: Detected) -> list[str]:
    if opts.all:
        return list(ALL_ASSISTANTS)
    if opts.assistants:
        seen: list[str] = []
        for a in opts.assistants:
            if a not in seen:
                seen.append(a)
        return seen
    # Auto: everything already configured, plus AGENTS.md as the cross-tool default.
    chosen = list(detected.assistants)
    if "agents" not in chosen:
        chosen.append("agents")
    return chosen


def _write(abs_path: Path, content: str) -> None:
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(content, encoding="utf-8")


def _write_target(root: Path, assistant: str, opts: InitOptions) -> FileAction:
    relpath, mode, body = _target(assistant)
    abs_path = root / relpath
    existing = abs_path.read_text(encoding="utf-8") if abs_path.exists() else None
    dry = opts.dry_run

    if mode == "owned":
        if existing is None:
            if not dry:
                _write(abs_path, body.rstrip() + "\n")
            return FileAction(relpath, "would-create" if dry else "created")
        ours = templates.SENTINEL in existing
        if not ours and not opts.force:
            return FileAction(
                relpath,
                "would-skip" if dry else "skipped",
                "exists and not managed by cendor — re-run with --force to overwrite",
            )
        if not dry:
            _write(abs_path, body.rstrip() + "\n")
        return FileAction(relpath, "would-update" if dry else "updated")

    # Shared file: insert/refresh our managed block, never disturb the user's other content.
    content, kind = upsert_managed(existing, body)
    if not dry:
        _write(abs_path, content)
    would = {"created": "would-create", "updated": "would-update", "appended": "would-append"}
    return FileAction(relpath, would[kind] if dry else kind)


def mcp_guidance() -> str:
    return "\n".join(
        [
            "Agent-mode assistant (Claude Code / Cursor / Copilot agent / Windsurf)? Connect the Cendor MCP",
            "server so it can look up the correct call-shape live:",
            "  • Remote (always current):  https://mcp.cendor.ai",
            "  • Local  (offline, bundled): uvx cendor-mcp   |   npx -y @cendor/mcp",
            "  Claude Code:  claude mcp add --transport http cendor https://mcp.cendor.ai",
            "  Full setup for every assistant: https://cendor.ai/mcp",
        ]
    )


def _mcp_config_files() -> list[tuple[str, str]]:
    cursor = json.dumps({"mcpServers": {"cendor": {"url": "https://mcp.cendor.ai"}}}, indent=2)
    vscode = json.dumps(
        {"servers": {"cendor": {"type": "http", "url": "https://mcp.cendor.ai"}}}, indent=2
    )
    return [(".cursor/mcp.json", cursor + "\n"), (".vscode/mcp.json", vscode + "\n")]


_PY_SCAFFOLD = '''"""Minimal Cendor starter — instrument once, then cap spend. Offline-safe scaffold.

Install:  pip install cendor-tokenguard "cendor-sdk[openai]"   (or just what you call)
Docs:     https://cendor.ai/docs/getting-started
"""

from cendor.core import instrument
from cendor.tokenguard import budget, report, track


def main() -> None:
    from openai import OpenAI

    client = instrument(OpenAI())  # wrap the client ONCE — idempotent, additive

    @budget(usd=0.50, on_exceed="raise")  # trips before a runaway loop overspends
    def answer(question: str) -> str:
        with track(feature="support", user_id="alice"):
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": question}],
            )
            return resp.choices[0].message.content or ""

    print(answer("Why was I charged twice?"))
    print(report(group_by=["feature"]))  # spend grouped by tag — for free


if __name__ == "__main__":
    main()
'''

_NODE_SCAFFOLD = """// Minimal Cendor starter — instrument once, then cap spend. Offline-safe scaffold.
//
// Install:  npm i @cendor/core @cendor/tokenguard openai   (or just what you call)
// Docs:     https://cendor.ai/docs/getting-started
import { instrument } from '@cendor/core';
import { budget, track, report } from '@cendor/tokenguard';
import OpenAI from 'openai';

const client = instrument(new OpenAI()); // wrap the client ONCE — idempotent, additive

// NOTE: budget is CURRIED — budget(cfg)(fn), never budget(cfg, fn).
const answer = budget({ usd: 0.5, onExceed: 'raise' })((question) =>
  track({ feature: 'support', userId: 'alice' }, async () => {
    const resp = await client.chat.completions.create({
      model: 'gpt-4o',
      messages: [{ role: 'user', content: question }],
    });
    return resp.choices[0].message.content;
  }),
);

console.log(await answer('Why was I charged twice?'));
console.log(report(['feature'])); // spend grouped by tag — for free
"""


def _scaffold_target(detected: Detected) -> tuple[str, str] | None:
    lang = (
        "python"
        if (detected.python and not detected.node)
        else ("node" if detected.node else detected.ecosystem)
    )
    if lang == "python":
        return "cendor_quickstart.py", _PY_SCAFFOLD
    if lang == "node":
        return "cendor-quickstart.mjs", _NODE_SCAFFOLD
    return None


def run_init(opts: InitOptions) -> InitResult:
    root = Path(opts.root)
    detected = detect_project(root)
    chosen = _choose(opts, detected)
    actions: list[FileAction] = []

    for assistant in chosen:
        actions.append(_write_target(root, assistant, opts))

    if opts.mcp:
        for relpath, body in _mcp_config_files():
            abs_path = root / relpath
            if abs_path.exists():
                actions.append(FileAction(relpath, "skipped", "exists — left as-is; see /mcp"))
                continue
            if not opts.dry_run:
                _write(abs_path, body)
            actions.append(FileAction(relpath, "would-create" if opts.dry_run else "created"))

    if opts.scaffold:
        target = _scaffold_target(detected)
        if target:
            relpath, body = target
            abs_path = root / relpath
            if abs_path.exists():
                actions.append(FileAction(relpath, "skipped", "exists — left as-is"))
            else:
                if not opts.dry_run:
                    _write(abs_path, body)
                actions.append(FileAction(relpath, "would-create" if opts.dry_run else "created"))

    return InitResult(
        detected=detected, chosen=chosen, actions=actions, mcp_guidance=mcp_guidance()
    )
