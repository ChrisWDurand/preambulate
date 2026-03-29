"""
Preambulate — global hook installer.

Writes SessionStart, PostToolUse, and Stop hooks to ~/.claude/settings.json
so preambulate works in every repo without per-project setup.

Hook lifecycle:
  SessionStart  — capture session, then pull remote graph (merge)
  PostToolUse   — record artifact + infer edges on file edits
  Stop          — push incremental graph changes to remote

Usage:
    preambulate install          # merge hooks into ~/.claude/settings.json
    preambulate install --dry-run
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


GITIGNORE_ENTRIES = [
    "# preambulate — graph memory (local only, never commit)",
    "memory.db/",
    "graph_export.json",
    ".preambulate_id",
    ".preambulate_sync_state.json",
]

HOOKS_TO_INSTALL = {
    "SessionStart": [
        {
            "matcher": "startup",
            "hooks": [
                {
                    "type": "command",
                    "command": "preambulate capture",
                    "statusMessage": "Capturing session...",
                },
                {
                    "type": "command",
                    "command": "preambulate sync pull",
                    "statusMessage": "Pulling remote graph...",
                },
            ],
        }
    ],
    "PostToolUse": [
        {
            "matcher": "Write|Edit",
            "hooks": [
                {"type": "command", "command": "preambulate artifact"},
                {"type": "command", "command": "preambulate infer"},
            ],
        }
    ],
    "Stop": [
        {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": "preambulate sync push",
                }
            ],
        }
    ],
}


def _commands_in_group(group: dict) -> set[str]:
    return {h["command"] for h in group.get("hooks", []) if "command" in h}


def _merge_hooks(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Merge incoming hook groups into existing, skipping any command already present.
    Returns (merged list, list of skipped command strings).
    """
    result = list(existing)
    skipped: list[str] = []

    existing_commands: set[str] = set()
    for group in existing:
        existing_commands |= _commands_in_group(group)

    for group in incoming:
        new_hooks = [
            h for h in group.get("hooks", [])
            if h.get("command") not in existing_commands
        ]
        new_skipped = [
            h["command"] for h in group.get("hooks", [])
            if h.get("command") in existing_commands
        ]
        skipped.extend(new_skipped)

        if new_hooks:
            # Look for an existing group with the same matcher to extend
            matcher = group.get("matcher")
            for eg in result:
                if eg.get("matcher") == matcher:
                    eg.setdefault("hooks", []).extend(new_hooks)
                    break
            else:
                result.append({**group, "hooks": new_hooks})

    return result, skipped


def ensure_gitignore(project_root: Path, dry_run: bool = False) -> None:
    """Append missing preambulate entries to .gitignore in project_root."""
    gitignore = project_root / ".gitignore"
    existing  = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    to_add    = [e for e in GITIGNORE_ENTRIES if e not in existing]

    if not to_add:
        return

    if dry_run:
        print(f"  .gitignore would add: {[e for e in to_add if not e.startswith('#')]}")
        return

    separator = "\n" if existing and not existing.endswith("\n") else ""
    gitignore.write_text(existing + separator + "\n".join(to_add) + "\n", encoding="utf-8")
    added = [e for e in to_add if not e.startswith("#")]
    print(f"preambulate: .gitignore updated — added: {', '.join(added)}")


def install(settings_path: Path, dry_run: bool = False) -> None:
    if settings_path.exists():
        config = json.loads(settings_path.read_text(encoding="utf-8"))
    else:
        config = {}

    config.setdefault("hooks", {})
    all_skipped: list[str] = []

    for event, incoming in HOOKS_TO_INSTALL.items():
        existing = config["hooks"].get(event, [])
        merged, skipped = _merge_hooks(existing, incoming)
        config["hooks"][event] = merged
        all_skipped.extend(skipped)

    if dry_run:
        print("preambulate install --dry-run")
        print(f"  target: {settings_path}")
        print(json.dumps(config, indent=2))
        return

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"preambulate: hooks written to {settings_path}")

    if all_skipped:
        for cmd in all_skipped:
            print(f"  skipped (already present): {cmd}")

    ensure_gitignore(Path.cwd(), dry_run=dry_run)


def main() -> None:
    default_path = Path.home() / ".claude" / "settings.json"
    parser = argparse.ArgumentParser(description="Install preambulate hooks globally.")
    parser.add_argument(
        "--settings",
        type=Path,
        default=default_path,
        metavar="PATH",
        help=f"Path to settings.json (default: {default_path})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print result without writing")
    args = parser.parse_args()
    install(settings_path=args.settings, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
