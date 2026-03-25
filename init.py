"""
Preambulate — graph memory initialization.

Creates the Kuzu database, runs the schema DDL, and inserts the
founding seed geometry: three Concept nodes for 'geometry',
'governs', and 'exploration', plus the edges between them that
make the seed phrase self-describing.

Usage:
    python init.py                  # creates ./memory.db
    python init.py --db ./path/db   # explicit path
    python init.py --reset          # drop and recreate (destructive)
"""

import argparse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import kuzu


# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------

SEED_PHRASE = "geometry governs exploration"
SCHEMA_FILE = Path(__file__).parent / "schema.cypher"
DEFAULT_DB_PATH = Path(__file__).parent / "memory.db"


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


def run_ddl(conn: kuzu.Connection, ddl: str) -> None:
    """Execute each DDL statement individually, skipping blank lines and comments."""
    # Strip comment lines first so semicolons inside comments don't split statements.
    clean_lines = [
        line for line in ddl.splitlines()
        if not line.strip().startswith("//")
    ]
    clean_ddl = "\n".join(clean_lines)
    for stmt in clean_ddl.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


# ------------------------------------------------------------
# Schema
# ------------------------------------------------------------

def create_schema(conn: kuzu.Connection) -> None:
    ddl = SCHEMA_FILE.read_text(encoding="utf-8")
    run_ddl(conn, ddl)
    print(f"  schema created from {SCHEMA_FILE.name}")


# ------------------------------------------------------------
# Seed geometry
# ------------------------------------------------------------

def insert_seed(conn: kuzu.Connection) -> dict:
    """
    Insert the Seed node and the three founding Concept nodes.
    Returns a dict of node ids keyed by role.
    """
    ts = now()

    seed_id      = new_id()
    geometry_id  = new_id()
    governs_id   = new_id()
    exploration_id = new_id()

    # Seed node
    conn.execute(
        "CREATE (s:Seed {id: $id, phrase: $phrase, created_at: $ts})",
        parameters={"id": seed_id, "phrase": SEED_PHRASE, "ts": ts},
    )

    # Three founding Concepts at depth 0 (seed-adjacent)
    for node_id, label in [
        (geometry_id,    "geometry"),
        (governs_id,     "governs"),
        (exploration_id, "exploration"),
    ]:
        conn.execute(
            """
            CREATE (c:Concept {
                id:         $id,
                label:      $label,
                definition: $definition,
                depth:      $depth
            })
            """,
            parameters={
                "id":         node_id,
                "label":      label,
                "definition": None,
                "depth":      0,
            },
        )

    print(f"  seed node inserted: '{SEED_PHRASE}'")
    print(f"  concept nodes: geometry, governs, exploration (depth=0)")

    return {
        "seed":        seed_id,
        "geometry":    geometry_id,
        "governs":     governs_id,
        "exploration": exploration_id,
    }


def insert_founding_edges(conn: kuzu.Connection, ids: dict) -> None:
    """
    Wire the founding geometry:

      Seed  -[GOVERNS]->  geometry
      Seed  -[GOVERNS]->  governs
      Seed  -[GOVERNS]->  exploration
      geometry    -[GOVERNS]->    exploration
      geometry    -[CONSTRAINS]-> exploration
      exploration -[DERIVES_FROM]-> geometry
    """
    ts = now()

    base = {
        "weight":         1.0,
        "traversal_cost": 0.0,
        "created_at":     ts,
    }

    # Seed -> three concepts
    for concept_id, label in [
        (ids["geometry"],    "geometry"),
        (ids["governs"],     "governs"),
        (ids["exploration"], "exploration"),
    ]:
        conn.execute(
            """
            MATCH (s:Seed {id: $seed_id}), (c:Concept {id: $concept_id})
            CREATE (s)-[:GOVERNS {
                weight:         $weight,
                traversal_cost: $traversal_cost,
                created_at:     $created_at,
                rationale:      $rationale
            }]->(c)
            """,
            parameters={
                **base,
                "seed_id":    ids["seed"],
                "concept_id": concept_id,
                "rationale":  f"Seed phrase introduces '{label}' as a founding concept.",
            },
        )

    # geometry -[GOVERNS]-> exploration
    conn.execute(
        """
        MATCH (g:Concept {id: $g_id}), (e:Concept {id: $e_id})
        CREATE (g)-[:GOVERNS {
            weight:         $weight,
            traversal_cost: $traversal_cost,
            created_at:     $created_at,
            rationale:      $rationale
        }]->(e)
        """,
        parameters={
            **base,
            "g_id":     ids["geometry"],
            "e_id":     ids["exploration"],
            "rationale": "The shape of the graph governs what can be explored.",
        },
    )

    # geometry -[CONSTRAINS]-> exploration
    conn.execute(
        """
        MATCH (g:Concept {id: $g_id}), (e:Concept {id: $e_id})
        CREATE (g)-[:CONSTRAINS {
            weight:         $weight,
            traversal_cost: $traversal_cost,
            created_at:     $created_at,
            rationale:      $rationale
        }]->(e)
        """,
        parameters={
            **base,
            "g_id":     ids["geometry"],
            "e_id":     ids["exploration"],
            "rationale": "Structure limits what is discoverable. Exploration is bounded by geometry.",
        },
    )

    # exploration -[DERIVES_FROM]-> geometry
    conn.execute(
        """
        MATCH (e:Concept {id: $e_id}), (g:Concept {id: $g_id})
        CREATE (e)-[:DERIVES_FROM {
            weight:         $weight,
            traversal_cost: $traversal_cost,
            created_at:     $created_at,
            rationale:      $rationale
        }]->(g)
        """,
        parameters={
            **base,
            "e_id":     ids["exploration"],
            "g_id":     ids["geometry"],
            "rationale": "Discovery is downstream of structure. Exploration exists because geometry was laid first.",
        },
    )

    print("  founding edges inserted")


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

def init(db_path: Path, reset: bool = False) -> kuzu.Database:
    if reset and db_path.exists():
        import shutil
        shutil.rmtree(db_path)
        print(f"  reset: removed existing database at {db_path}")

    if db_path.exists() and not reset:
        print(f"database already exists at {db_path}")
        print("  use --reset to drop and recreate")
        db = kuzu.Database(str(db_path))
        return db

    print(f"initializing database at {db_path}")
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)

    create_schema(conn)
    ids = insert_seed(conn)
    insert_founding_edges(conn, ids)

    print("done.")
    return db


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the preambulate graph database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to Kuzu database directory")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate the database (destructive)")
    args = parser.parse_args()

    init(db_path=args.db, reset=args.reset)


if __name__ == "__main__":
    main()
