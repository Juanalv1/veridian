-- Webhook follow-up (U12).
-- 1. notifications.enforced: whether the project enforced blocks when the block was decided.
--    Snapshot at enqueue, so every retry carries the same body. Projects in `observe` still
--    notify, with `"enforced": false`. Rows queued before this migration count as enforced.
-- 2. Drop the 0003 project.webhook_url and project.webhook_secret_enc columns: never used,
--    the endpoint lives in webhook_endpoint (0005). Needs SQLite 3.35 (DROP COLUMN).

ALTER TABLE notifications ADD COLUMN enforced INTEGER NOT NULL DEFAULT 1 CHECK (enforced IN (0, 1));

ALTER TABLE project DROP COLUMN webhook_url;

ALTER TABLE project DROP COLUMN webhook_secret_enc;
