-- A customer app is a project: client_id becomes project_id everywhere, same values.
ALTER TABLE events RENAME COLUMN client_id TO project_id;
ALTER TABLE decision_events RENAME COLUMN client_id TO project_id;
ALTER TABLE rate_spike_judgments RENAME COLUMN client_id TO project_id;
ALTER TABLE ai_judgments RENAME COLUMN client_id TO project_id;
ALTER TABLE blocklist_versions RENAME COLUMN client_id TO project_id;

-- Decision ids become unique per project, not globally. SQLite cannot drop a constraint,
-- so the table is rebuilt with the same columns, in the same order, and copied.
CREATE TABLE decisions_new (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    id TEXT NOT NULL,
    ts TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    decision TEXT NOT NULL,
    signals TEXT NOT NULL,
    rationale TEXT NOT NULL,
    normal INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project_id, id)
);
INSERT INTO decisions_new (seq, project_id, id, ts, user_id, session_id, ip, decision, signals, rationale, normal)
    SELECT seq, client_id, id, ts, user_id, session_id, ip, decision, signals, rationale, normal FROM decisions;
-- Keep the AUTOINCREMENT high-water mark: ids d_<seq> deleted by a reset are never reissued.
DELETE FROM sqlite_sequence WHERE name = 'decisions_new';
INSERT INTO sqlite_sequence (name, seq) SELECT 'decisions_new', seq FROM sqlite_sequence WHERE name = 'decisions';
DROP TABLE decisions;
ALTER TABLE decisions_new RENAME TO decisions;
CREATE UNIQUE INDEX one_block_per_session
    ON decisions (project_id, session_id) WHERE decision = 'block';
