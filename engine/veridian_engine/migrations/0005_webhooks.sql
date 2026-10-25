-- Signed webhook for block decisions (docs/DISENO-PRODUCTO.md, "Notificaciones de bloqueo").
-- One endpoint per project. The signing secret is stored encrypted with VERIDIAN_SECRET_KEY
-- (the engine needs it to sign), never in clear. The unused project.webhook_url and
-- project.webhook_secret_enc columns from 0003 are left as they are.
-- No foreign keys, like the data tables: isolation is the project_id filter on every query.

CREATE TABLE webhook_endpoint (
    project_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    secret_enc TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL
);

-- Outbox: a row is inserted in the same transaction as the block decision, and a background
-- worker in the same process sends it. Timestamps are ISO UTC text, so text order is time order.
CREATE TABLE notifications (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('webhook', 'email')),
    decision_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX notifications_due ON notifications (status, next_attempt_at);
CREATE INDEX notifications_by_project ON notifications (project_id, created_at);
