"""preambulate — unified CLI dispatcher."""

from __future__ import annotations

import importlib
import sys

_COMMANDS = {
    "init":     "preambulate.init",
    "install":  "preambulate.install",
    "capture":  "preambulate.capture",
    "artifact": "preambulate.artifact",
    "infer":    "preambulate.infer",
    "decision": "preambulate.decision",
    "briefing": "preambulate.briefing",
    "sync":     "preambulate.sync",
    "export":   "preambulate.export",
    "mcp":      "preambulate.mcp_server",
}

_USAGE = """\
usage: preambulate <command> [args]

Commands:
  init       Initialise a new graph database
  install    Write SessionStart/PostToolUse hooks to ~/.claude/settings.json
  capture    Record a session-start Decision node (SessionStart hook)
  artifact   Record a file edit as Artifact + Decision (PostToolUse hook)
  infer      Infer DERIVES_FROM edges from Python import statements
  decision   Record a session-end Decision and/or write semantic edges
  briefing   Print the memory briefing (read-only; --focal for proximity mode)
  sync       Push or pull the graph snapshot to/from api.preambulate.dev
  export     Dump or restore the full graph to/from JSON
  mcp        Start the MCP server (stdio transport)

Run 'preambulate <command> --help' for per-command usage.
"""


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(_USAGE)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in _COMMANDS:
        print(f"preambulate: unknown command {cmd!r}\n")
        print(_USAGE)
        sys.exit(1)

    # Rewrite argv so each submodule's argparse shows the right program name.
    sys.argv = [f"preambulate {cmd}"] + sys.argv[2:]

    module = importlib.import_module(_COMMANDS[cmd])
    module.main()
