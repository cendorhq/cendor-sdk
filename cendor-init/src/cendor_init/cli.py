"""``cendor-init`` — one command to make a project Cendor-ready and Cendor-fluent for its AI
assistant, plus a ``doctor`` that catches wiring mistakes before they bite. Offline: no network, no key.

    uvx cendor-init            # detect + write assistant rules (idempotent)
    uvx cendor-init doctor     # validate wiring; non-zero exit on hard problems (CI-usable)

See https://cendor.ai/docs/for-ai-assistants and https://cendor.ai/mcp.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from .doctor import SEVERITY_RANK, Finding, run_doctor
from .initialize import ALL_ASSISTANTS, InitOptions, run_init

__all__ = ["main"]

_ICON = {
    "created": "+",
    "updated": "~",
    "appended": "~",
    "skipped": "·",
    "would-create": "+",
    "would-update": "~",
    "would-append": "~",
    "would-skip": "·",
}


def _version() -> str:
    try:
        from importlib import metadata

        return metadata.version("cendor-init")
    except Exception:
        return "0.1.0"


def _parse_assistants(raw: list[str] | None) -> list[str] | None:
    if not raw:
        return None
    wanted: list[str] = []
    for item in raw:
        for part in item.split(","):
            part = part.strip().lower()
            if part and part not in wanted:
                wanted.append(part)
    bad = [w for w in wanted if w not in ALL_ASSISTANTS]
    if bad:
        sys.stderr.write(f"cendor-init: unknown assistant(s): {', '.join(bad)}\n")
        sys.stderr.write(f"             valid: {', '.join(ALL_ASSISTANTS)}\n")
        raise SystemExit(2)
    return wanted


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path.cwd()
    opts = InitOptions(
        root=root,
        assistants=_parse_assistants(args.assistant),
        all=args.all,
        mcp=args.mcp,
        scaffold=args.scaffold,
        force=args.force,
        dry_run=args.dry_run,
    )
    result = run_init(opts)
    dry = opts.dry_run
    eco = result.detected.ecosystem

    out = sys.stdout.write
    out(
        f"\ncendor-init — {'dry run (no files written)' if dry else 'wiring Cendor for your assistant'}\n"
    )
    line = "no package.json / pyproject found" if eco == "unknown" else f"{eco} project"
    if result.detected.declared_providers:
        line += f"  ·  providers: {', '.join(sorted(result.detected.declared_providers))}"
    out(f"project: {line}\n\n")

    for a in result.actions:
        icon = _ICON.get(a.status, "·")
        note = f"  ({a.note})" if a.note else ""
        out(f"  {icon} {a.status:<13} {a.path}{note}\n")

    out("\nMCP (agent-mode assistants):\n")
    for ln in result.mcp_guidance.split("\n"):
        out(f"  {ln}\n")

    out("\nNext:\n")
    out("  • Trust your editor's hover/completion — every Cendor symbol ships an @example.\n")
    out("  • Full trap sheet:  https://cendor.ai/docs/for-ai-assistants\n")
    out("  • Validate wiring:  uvx cendor-init doctor\n\n")
    return 0


def _print_finding(f: Finding) -> None:
    tag = {"error": "ERROR", "warn": "WARN ", "ok": "OK   "}.get(f.severity, "INFO ")
    out = sys.stdout.write
    out(f"  [{tag}] {f.title}\n")
    for ln in textwrap.wrap(f.detail, width=88):
        out(f"          {ln}\n")
    if f.fix:
        out(f"          fix: {f.fix}\n")
    for loc in f.locations:
        out(f"          - {loc}\n")
    out("\n")


def _cmd_doctor(_args: argparse.Namespace) -> int:
    result = run_doctor(Path.cwd())
    sys.stdout.write("\ncendor-init doctor\n\n")
    for f in sorted(result.findings, key=lambda x: SEVERITY_RANK.get(x.severity, 9)):
        _print_finding(f)
    errors = sum(1 for f in result.findings if f.severity == "error")
    warns = sum(1 for f in result.findings if f.severity == "warn")
    tail = "OK." if result.exit_code == 0 else "Fix the errors above."
    sys.stdout.write(f"{errors} error(s), {warns} warning(s). {tail}\n\n")
    return result.exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cendor-init",
        description="Wire Cendor + your AI assistant, offline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Docs: https://cendor.ai/docs/for-ai-assistants   MCP: https://cendor.ai/mcp",
    )
    parser.add_argument("-v", "--version", action="version", version=_version())
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="write assistant rules files (default command)")
    _add_init_args(p_init)
    sub.add_parser("doctor", help="validate the wiring (never writes); exit 1 on hard problems")
    return parser


def _add_init_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--all", action="store_true", help="write every assistant rules file")
    p.add_argument(
        "--assistant",
        action="append",
        metavar="LIST",
        help="comma-separated subset: copilot,cursor,agents,claude,windsurf",
    )
    p.add_argument("--mcp", action="store_true", help="also drop MCP connect config where absent")
    p.add_argument(
        "--scaffold", action="store_true", help="also write a correct instrument()+budget starter"
    )
    p.add_argument(
        "--force", action="store_true", help="overwrite an owned file (.cursor/rules/cendor.mdc)"
    )
    p.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    p.add_argument("-y", "--yes", action="store_true", help=argparse.SUPPRESS)


def _force_utf8() -> None:
    """Our output uses em-dashes / bullets. A Windows console defaults to a non-UTF-8 code page,
    which would raise UnicodeEncodeError on those — force UTF-8 (with a replace fallback)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - stream doesn't support it
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    # Default to `init` when no subcommand is given (mirrors `npx @cendor/init`).
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help", "-v", "--version")):
        argv = ["init", *argv]

    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "init"
    if command == "doctor":
        return _cmd_doctor(args)
    return _cmd_init(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
