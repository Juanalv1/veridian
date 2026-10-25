-- Self-unlock by email (docs/DESBLOQUEO-POR-CORREO.md).
--
-- notifications.purpose tells the email worker which message a `kind = 'email'` row is: NULL is the
-- admin alert (U17), 'self_unlock' is the branded email to the blocked end user. That row keeps
-- `recipient` NULL: the end user's address stays in user_contact only, so erasing it is enough.
-- user_contact.last_mail_at enforces at most one email per user every 15 minutes.
--
-- self_unlock_token stores only the SHA-256 of the token. A token is bound to one decision, project
-- and user, works once (used_at) and expires (expires_at). notification_id lets a retry replace the
-- unused token of the failed attempt.

ALTER TABLE notifications ADD COLUMN purpose TEXT;

ALTER TABLE user_contact ADD COLUMN last_mail_at TEXT;

CREATE TABLE self_unlock_token (
    token_hash TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    notification_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE INDEX self_unlock_token_by_user ON self_unlock_token (project_id, user_id);
CREATE INDEX self_unlock_token_by_notification ON self_unlock_token (notification_id);
