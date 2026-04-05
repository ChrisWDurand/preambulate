// ============================================================
// Preambulate — Graph Memory Schema
// Storage: Kuzu (embedded, local-first)
// Version: 0.1.0
// ============================================================
// Run via Python init.py or directly through a Kuzu connection.
// All enums are stored as STRING; validation is application-layer.
// Undirected edges (RESONATES_WITH, OPPOSES) are modeled as
// directed but queried with undirected Cypher syntax: -[r:TYPE]-
// ============================================================


// ------------------------------------------------------------
// NODE TABLES
// ------------------------------------------------------------

CREATE NODE TABLE Seed (
    id          STRING,
    phrase      STRING,
    created_at  TIMESTAMP,
    PRIMARY KEY (id)
);

CREATE NODE TABLE Concept (
    id          STRING,
    label       STRING,
    definition  STRING,
    depth       INT64,
    PRIMARY KEY (id)
);

CREATE NODE TABLE Artifact (
    id      STRING,
    label   STRING,
    path    STRING,
    kind    STRING,
    PRIMARY KEY (id)
);

// kind must be one of: file | module | document | output | external

CREATE NODE TABLE Cluster (
    id               STRING,
    label            STRING,
    algorithm        STRING,
    phase            STRING,
    created_at       TIMESTAMP,
    membership_count INT64,
    PRIMARY KEY (id)
);

// phase must be one of: A (file-level) | B (symbol-level)

CREATE NODE TABLE Context (
    id      STRING,
    label   STRING,
    active  BOOLEAN,
    PRIMARY KEY (id)
);

CREATE NODE TABLE Observation (
    id          STRING,
    label       STRING,
    source      STRING,
    confidence  DOUBLE,
    PRIMARY KEY (id)
);

CREATE NODE TABLE Decision (
    id               STRING,
    label            STRING,
    rationale        STRING,
    timestamp        TIMESTAMP,
    session_id       STRING,
    author           STRING,
    machine_id       STRING,
    decision_type    STRING,
    rationale_source STRING,
    outcome          STRING,
    contract_id      STRING,
    PRIMARY KEY (id)
);

// decision_type    — who initiated this decision.
//                    Values: user | claude_inferred | claude_autonomous | blocked
// rationale_source — how the rationale was produced.
//                    Values: user_stated | claude_inferred | system_blocked
// outcome          — terminal state of this decision's work.
//                    Values: resolved | escalated | abandoned
//                    Required for flag severity learning; frozen weights if absent.
// contract_id      — content-addressed identity of the contract under which this
//                    decision was made. H(schema ∥ context ∥ t₀). Used for ghost
//                    detection: stale if ∄ match at query time, schema_drift if
//                    schema has migrated, orphan if ∄ in any Decision.context.


// ------------------------------------------------------------
// RELATIONSHIP TABLES
// Base properties on all edges: weight, traversal_cost,
// created_at, rationale. Additional properties per type follow.
// ------------------------------------------------------------

CREATE REL TABLE GROUP DEFINES (
    FROM Concept  TO Concept,
    FROM Context  TO Concept,
    weight          DOUBLE,
    traversal_cost  DOUBLE,
    created_at      TIMESTAMP,
    rationale       STRING
);

CREATE REL TABLE GROUP DERIVES_FROM (
    FROM Concept     TO Concept,
    FROM Concept     TO Artifact,
    FROM Artifact    TO Artifact,
    FROM Artifact    TO Decision,
    FROM Observation TO Concept,
    FROM Observation TO Decision,
    weight          DOUBLE,
    traversal_cost  DOUBLE,
    created_at      TIMESTAMP,
    rationale       STRING
);

CREATE REL TABLE GROUP CONSTRAINS (
    FROM Concept  TO Concept,
    FROM Concept  TO Artifact,
    FROM Concept  TO Context,
    FROM Context  TO Concept,
    FROM Context  TO Artifact,
    FROM Context  TO Context,
    FROM Decision TO Concept,
    FROM Decision TO Artifact,
    FROM Decision TO Context,
    weight          DOUBLE,
    traversal_cost  DOUBLE,
    created_at      TIMESTAMP,
    rationale       STRING
);

CREATE REL TABLE GROUP GOVERNS (
    FROM Seed     TO Concept,
    FROM Concept  TO Concept,
    FROM Concept  TO Artifact,
    FROM Artifact TO Artifact,
    FROM Cluster  TO Artifact,
    FROM Context  TO Concept,
    FROM Context  TO Artifact,
    weight          DOUBLE,
    traversal_cost  DOUBLE,
    created_at      TIMESTAMP,
    rationale       STRING
);

// Undirected by convention — insert both directions, query with -[r:RESONATES_WITH]-
CREATE REL TABLE GROUP RESONATES_WITH (
    FROM Concept     TO Concept,
    FROM Concept     TO Artifact,
    FROM Concept     TO Context,
    FROM Concept     TO Observation,
    FROM Concept     TO Decision,
    FROM Artifact    TO Artifact,
    FROM Artifact    TO Context,
    FROM Artifact    TO Observation,
    FROM Artifact    TO Decision,
    FROM Context     TO Context,
    FROM Context     TO Observation,
    FROM Context     TO Decision,
    FROM Observation TO Observation,
    FROM Observation TO Decision,
    FROM Decision    TO Decision,
    weight            DOUBLE,
    traversal_cost    DOUBLE,
    created_at        TIMESTAMP,
    rationale         STRING,
    resonance_basis   STRING
);

// Undirected by convention — same pattern as RESONATES_WITH
CREATE REL TABLE GROUP OPPOSES (
    FROM Concept     TO Concept,
    FROM Concept     TO Artifact,
    FROM Concept     TO Context,
    FROM Concept     TO Observation,
    FROM Concept     TO Decision,
    FROM Artifact    TO Artifact,
    FROM Artifact    TO Context,
    FROM Artifact    TO Observation,
    FROM Artifact    TO Decision,
    FROM Context     TO Context,
    FROM Context     TO Observation,
    FROM Context     TO Decision,
    FROM Observation TO Observation,
    FROM Observation TO Decision,
    FROM Decision    TO Decision,
    weight               DOUBLE,
    traversal_cost       DOUBLE,
    created_at           TIMESTAMP,
    rationale            STRING,
    tension_description  STRING
);

CREATE REL TABLE GROUP INSTANTIATES (
    FROM Artifact TO Concept,
    FROM Decision TO Concept,
    weight          DOUBLE,
    traversal_cost  DOUBLE,
    created_at      TIMESTAMP,
    rationale       STRING
);

CREATE REL TABLE GROUP SUPERSEDES (
    FROM Concept     TO Concept,
    FROM Artifact    TO Artifact,
    FROM Cluster     TO Cluster,
    FROM Context     TO Context,
    FROM Observation TO Observation,
    FROM Decision    TO Decision,
    weight          DOUBLE,
    traversal_cost  DOUBLE,
    created_at      TIMESTAMP,
    rationale       STRING,
    reason          STRING
);

CREATE REL TABLE GROUP ANCHORS (
    FROM Decision TO Concept,
    FROM Decision TO Artifact,
    FROM Decision TO Context,
    FROM Decision TO Observation,
    weight          DOUBLE,
    traversal_cost  DOUBLE,
    created_at      TIMESTAMP,
    rationale       STRING,
    anchor_type     STRING
);

// anchor_type must be one of: created | modified | discussed | rejected
