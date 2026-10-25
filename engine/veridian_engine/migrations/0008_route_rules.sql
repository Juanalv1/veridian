-- U7: route classification per project. routes.yaml becomes the template a project is seeded
-- with (at startup for existing projects, at creation for new ones); PUT replaces the rules.
-- project.routes_seeded keeps an emptied list empty: only unseeded projects get the template.

CREATE TABLE route_rules (
    project_id TEXT NOT NULL REFERENCES project (id),
    method TEXT NOT NULL,
    route TEXT NOT NULL,
    class TEXT NOT NULL CHECK (class IN ('critical', 'sensitive', 'normal')),
    PRIMARY KEY (project_id, method, route)
);

ALTER TABLE project ADD COLUMN routes_seeded INTEGER NOT NULL DEFAULT 0 CHECK (routes_seeded IN (0, 1));
