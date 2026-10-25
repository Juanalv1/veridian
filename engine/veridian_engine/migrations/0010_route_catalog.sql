-- Automatic route classification (HANDOFF, Dev B item 6).
-- route_catalog: the routes the SDK reported for a project (contract 16), with the AI's last
-- answer. `fingerprint` covers method, route, summary and description: only new or changed
-- routes are classified again. Only route text, never code.
-- route_rules.source: who set the rule. The AI writes only routes without an `admin` or
-- `template` rule; existing rules come from routes.yaml or the U7 PUT, so they start as
-- `template` (protected).

CREATE TABLE route_catalog (
    project_id TEXT NOT NULL,
    method TEXT NOT NULL,
    route TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'classified', 'failed')),
    ai_class TEXT CHECK (ai_class IN ('critical', 'sensitive', 'normal')),
    ai_reason TEXT,
    last_error TEXT,
    classified_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, method, route)
);

CREATE INDEX route_catalog_pending ON route_catalog (status, project_id);

ALTER TABLE route_rules ADD COLUMN source TEXT NOT NULL DEFAULT 'template' CHECK (source IN ('template', 'ai', 'admin'));
