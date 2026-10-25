-- Organizations above projects (docs/DISENO-PRODUCTO.md, "Revisión 2026-10-25").
-- Roles live only in the organization: one role per person and organization, valid for every
-- project of it. Membership, invitations and audit move from the project to the organization.
-- Projects that exist before this migration are the single-client demo: they form org "Demo"
-- (id 'org_demo'). Projects seeded later at startup also land there (control_plane/legacy.py).

CREATE TABLE organization (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);

INSERT INTO organization (id, name, created_at)
SELECT 'org_demo', 'Demo', (SELECT MIN(created_at) FROM project)
WHERE EXISTS (SELECT 1 FROM project);

-- Nullable only because SQLite cannot add a NOT NULL column without a default; the code
-- always sets it. Every pre-existing project gets 'org_demo' right below.
ALTER TABLE project ADD COLUMN org_id TEXT REFERENCES organization (id);

UPDATE project SET org_id = 'org_demo' WHERE org_id IS NULL;

CREATE INDEX project_by_org ON project (org_id);

-- Membership per organization. A person who had several project roles in what is now one
-- organization keeps the strongest one.
CREATE TABLE org_membership (
    org_id TEXT NOT NULL REFERENCES organization (id),
    person_id TEXT NOT NULL REFERENCES person (id),
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'viewer')),
    notify_email INTEGER NOT NULL DEFAULT 1 CHECK (notify_email IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (org_id, person_id)
);

INSERT INTO org_membership (org_id, person_id, role, notify_email, created_at)
SELECT
    p.org_id,
    m.person_id,
    CASE MAX(CASE m.role WHEN 'owner' THEN 2 WHEN 'admin' THEN 1 ELSE 0 END)
        WHEN 2 THEN 'owner' WHEN 1 THEN 'admin' ELSE 'viewer' END,
    MAX(m.notify_email),
    MIN(m.created_at)
FROM membership m JOIN project p ON p.id = m.project_id
GROUP BY p.org_id, m.person_id;

DROP TABLE membership;

ALTER TABLE org_membership RENAME TO membership;

CREATE INDEX membership_by_person ON membership (person_id);

-- Invitations are to the organization.
CREATE TABLE org_invitation (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organization (id),
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'viewer')),
    invited_by TEXT NOT NULL REFERENCES person (id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    revoked_at TEXT
);

INSERT INTO org_invitation (id, org_id, email, role, invited_by, created_at, expires_at, accepted_at, revoked_at)
SELECT i.id, p.org_id, i.email, i.role, i.invited_by, i.created_at, i.expires_at, i.accepted_at, i.revoked_at
FROM invitation i JOIN project p ON p.id = i.project_id;

DROP TABLE invitation;

ALTER TABLE org_invitation RENAME TO invitation;

CREATE INDEX invitation_by_email ON invitation (email);

-- Audit belongs to the organization; project_id names the project the action touched and is
-- null for organization actions (members, invitations). org_id is null only for rows about a
-- project without an organization, which the application never creates.
CREATE TABLE org_audit_log (
    seq INTEGER PRIMARY KEY,
    org_id TEXT REFERENCES organization (id),
    project_id TEXT REFERENCES project (id),
    actor_person_id TEXT REFERENCES person (id),
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('person', 'system')),
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    ts TEXT NOT NULL,
    CHECK (org_id IS NOT NULL OR project_id IS NOT NULL)
);

INSERT INTO org_audit_log (seq, org_id, project_id, actor_person_id, actor_kind, action, target_type, target_id, meta, ts)
SELECT a.seq, p.org_id, a.project_id, a.actor_person_id, a.actor_kind, a.action, a.target_type, a.target_id, a.meta, a.ts
FROM audit_log a LEFT JOIN project p ON p.id = a.project_id;

DROP TABLE audit_log;

ALTER TABLE org_audit_log RENAME TO audit_log;

CREATE INDEX audit_log_by_org ON audit_log (org_id, seq);

CREATE INDEX audit_log_by_project ON audit_log (project_id, seq);
