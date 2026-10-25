-- The schema as it was before numbered migrations (deployed databases are marked, not rerun).
CREATE TABLE IF NOT EXISTS events (
    client_id TEXT NOT NULL,
    id TEXT NOT NULL,
    ts TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    device_id TEXT NOT NULL,
    method TEXT NOT NULL,
    route TEXT NOT NULL,
    status INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    PRIMARY KEY (client_id, id)
);
CREATE INDEX IF NOT EXISTS events_by_user ON events (client_id, user_id, ts);
CREATE INDEX IF NOT EXISTS events_by_session ON events (client_id, session_id, ts);

CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id TEXT NOT NULL,
    id TEXT NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ip TEXT NOT NULL,
    decision TEXT NOT NULL,
    signals TEXT NOT NULL,
    rationale TEXT NOT NULL,
    normal INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_block_per_session
    ON decisions (client_id, session_id) WHERE decision = 'block';

CREATE TABLE IF NOT EXISTS decision_events (
    client_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (client_id, decision_id, event_id)
);

-- Sessions whose first rate spike was already sent to the judge: it is judged once.
CREATE TABLE IF NOT EXISTS rate_spike_judgments (
    client_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    PRIMARY KEY (client_id, session_id)
);

-- Every real call to the AI judge, by the judged event's ts (reviewed_by_ai, contract 6).
CREATE TABLE IF NOT EXISTS ai_judgments (
    client_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ai_judgments_by_ts ON ai_judgments (client_id, ts);

CREATE TABLE IF NOT EXISTS blocklist_versions (
    client_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL
);
