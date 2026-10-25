-- Block alert email (U17) through the same outbox as the webhook (0005).
-- One `kind = 'email'` row per recipient, so a failing address is retried alone and the
-- others are not mailed twice. NULL for webhook rows.

ALTER TABLE notifications ADD COLUMN recipient TEXT;
