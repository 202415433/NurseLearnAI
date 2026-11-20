-- Retour arrière de la migration 0004 (identité et accès).
-- Supprime uniquement ce que 0004 a ajouté ; les données de ces colonnes et tables sont perdues.
DROP TRIGGER IF EXISTS trg_invitations_check ON invitations;
DROP TRIGGER IF EXISTS trg_user_roles_guard ON user_roles;
DROP FUNCTION IF EXISTS nl_check_invitation(), nl_guard_role_grant(), nl_role_grant_allowed(uuid, uuid, text, uuid);
DROP TABLE IF EXISTS invitations;
DROP TABLE IF EXISTS one_time_tokens;
COMMENT ON COLUMN auth_sessions.refresh_token_hash IS NULL;
DROP INDEX IF EXISTS ix_auth_sessions_family;
ALTER TABLE auth_sessions
  DROP COLUMN IF EXISTS revoked_reason,
  DROP COLUMN IF EXISTS last_authenticated_at,
  DROP COLUMN IF EXISTS last_seen_at,
  DROP COLUMN IF EXISTS rotated_at,
  DROP COLUMN IF EXISTS replaced_by,
  DROP COLUMN IF EXISTS family_id;
ALTER TABLE users
  DROP COLUMN IF EXISTS locked_until,
  DROP COLUMN IF EXISTS failed_login_count,
  DROP COLUMN IF EXISTS password_changed_at,
  DROP COLUMN IF EXISTS privacy_accepted_at,
  DROP COLUMN IF EXISTS privacy_policy_version,
  DROP COLUMN IF EXISTS terms_version,
  DROP COLUMN IF EXISTS adult_attested_at;
