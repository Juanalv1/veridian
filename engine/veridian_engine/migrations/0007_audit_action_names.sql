-- Contract 15: audit actions use the past-tense names the panel shows (docs/DISENO-PRODUCTO.md,
-- "Formas de respuesta de 11, 12 y 15"). Rename the rows written before the change.

UPDATE audit_log SET action = CASE action
    WHEN 'member.role' THEN 'member.role_changed'
    WHEN 'member.remove' THEN 'member.removed'
    WHEN 'invitation.create' THEN 'invitation.created'
    WHEN 'invitation.revoke' THEN 'invitation.revoked'
    WHEN 'api_key.create' THEN 'api_key.created'
    WHEN 'api_key.revoke' THEN 'api_key.revoked'
    WHEN 'decision.mark_normal' THEN 'decision.marked_normal'
    ELSE action
END
WHERE action IN ('member.role', 'member.remove', 'invitation.create', 'invitation.revoke', 'api_key.create',
                 'api_key.revoke', 'decision.mark_normal');
