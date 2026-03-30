"""
Preambulate — graph backend abstraction.

All graph access goes through GraphConnection.  KuzuConnection is the
default backend.  Swapping to Neo4j or another Cypher-compatible engine
means replacing this file — no other module changes.

Usage
-----
    from preambulate.graph import open_graph

    with open_graph(db_path) as g:
        rows = g.execute("MATCH (n:Concept) RETURN n.label")
        for row in rows:
            print(row[0])

execute() always returns list[list] — an empty list for DDL or
write-only statements, a list of rows otherwise.
"""

from __future__ import annotations

from pathlib import Path


# ------------------------------------------------------------
# Abstract interface
# ------------------------------------------------------------

class GraphConnection:
    """Backend-agnostic graph connection. Extend to add new backends."""

    def execute(self, query: str, parameters: dict | None = None) -> list[list]:
        """Execute a Cypher query. Returns rows as list[list], empty for DDL."""
        raise NotImplementedError

    def close(self) -> None:
        """Release resources. Safe to call more than once."""

    def __enter__(self) -> "GraphConnection":
        return self

    def __exit__(self, *_) -> None:
        self.close()


# ------------------------------------------------------------
# KuzuDB backend
# ------------------------------------------------------------

class KuzuConnection(GraphConnection):
    """KuzuDB backend — embedded, local-first, Cypher-compatible."""

    def __init__(self, db_path: Path) -> None:
        import kuzu  # only import of kuzu in the entire codebase
        self._db   = kuzu.Database(str(db_path))
        self._conn = kuzu.Connection(self._db)

    def execute(self, query: str, parameters: dict | None = None) -> list[list]:
        result = self._conn.execute(query, parameters or {})
        rows: list[list] = []
        if result is None:
            return rows
        try:
            while result.has_next():
                rows.append(result.get_next())
        except Exception:
            pass
        return rows

    def close(self) -> None:
        pass  # KuzuDB manages its own lifecycle via the Database object


# ------------------------------------------------------------
# Factory
# ------------------------------------------------------------

def open_graph(db_path: Path) -> GraphConnection:
    """Open the graph at db_path using the default (KuzuDB) backend."""
    return KuzuConnection(db_path)
