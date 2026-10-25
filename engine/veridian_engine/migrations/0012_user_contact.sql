-- Optional email of an end user (self-unlock by email). Personal data: only the latest address
-- per (project, user) is kept, `updated_at` is the time of the event that carried it (same
-- format as events.ts, so the retention cutoff compares as text). Erased with the user's data
-- and purged by retention. Like the other data tables, no foreign key to project.

CREATE TABLE user_contact (
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    email TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);

CREATE INDEX user_contact_by_updated ON user_contact (project_id, updated_at);
