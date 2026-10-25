-- Brand of a project for the self-unlock email and page (docs/DESBLOQUEO-POR-CORREO.md).
-- All optional; validated by the engine before it writes (logo and support URLs are https,
-- the logo like the webhook URL, the color a #RRGGBB hex). Self-unlock is off until an admin
-- turns it on.

ALTER TABLE project ADD COLUMN brand_name TEXT;
ALTER TABLE project ADD COLUMN brand_logo_url TEXT;
ALTER TABLE project ADD COLUMN brand_color TEXT;
ALTER TABLE project ADD COLUMN brand_support_url TEXT;
ALTER TABLE project ADD COLUMN brand_support_email TEXT;
ALTER TABLE project ADD COLUMN self_unlock_enabled INTEGER NOT NULL DEFAULT 0 CHECK (self_unlock_enabled IN (0, 1));
