"""
Preambulate — database health check.

Detects WAL corruption and other Kuzu failure modes. Prints actionable
recovery steps if problems are found. Exits 0 if healthy, 1 if not.

Usage:
    preambulate doctor
    preambulate doctor --db ./memory.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from preambulate import get_db_path


_RECOVERY = """\
  Recovery:
    preambulate export dump --output backup.json
    preambulate init --reset
    preambulate export restore --input backup.json

  If the dump also fails (severe corruption), skip the dump step and
  run 'preambulate init --reset' to start fresh.
"""


def check(db_path: Path) -> bool:
    """Run health checks on db_path. Returns True if healthy, False if not."""

    print(f"preambulate doctor: checking {db_path}")

    # 1. Existence
    if not db_path.exists():
        print("  [MISS] memory.db not found — run 'preambulate capture' to initialise")
        return False

    # 2. Stale WAL files (Kuzu stores the db as a directory; WAL lives inside)
    wal_files: list[Path] = []
    if db_path.is_dir():
        wal_files = list(db_path.glob("*.wal"))
    if wal_files:
        names = ", ".join(w.name for w in wal_files)
        print(f"  [WARN] stale WAL file(s) detected: {names}")
        print("         This may indicate an improper shutdown — proceeding to open check.")

    # 3. Open and probe
    try:
        from preambulate.graph import open_graph
        conn = open_graph(db_path)
        conn.execute("MATCH (n:Concept) RETURN n.label LIMIT 1")
    except Exception as exc:
        print(f"  [FAIL] could not open or query database: {exc}")
        if wal_files:
            print("         Likely cause: corrupted WAL file from improper shutdown.")
        print(_RECOVERY)
        return False

    if wal_files:
        print("  [OK] WAL files present but database opened successfully (Kuzu recovered them)")
    else:
        print("  [OK] memory.db is healthy")

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check memory.db health and print recovery steps if needed."
    )
    parser.add_argument("--db", type=Path, default=get_db_path())
    args = parser.parse_args()

    healthy = check(db_path=args.db)
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
