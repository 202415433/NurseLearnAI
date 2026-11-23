-- =============================================================================
-- Retour arrière de la migration 0008 (rôle de service media_scanner, étape 2.2.3-c).
-- Restaure exactement le schéma 0007 lorsque c'est sûr ; sinon s'ARRÊTE : un verdict antivirus
-- est un historique de sécurité, jamais effacé silencieusement.
-- Le rôle de connexion nl_media_scanner (objet de CLUSTER, possiblement utilisé par d'autres
-- bases) n'est ni supprimé ni modifié : il perd seulement ses privilèges sur CETTE base.
-- =============================================================================
DO $$
DECLARE
  n_scan  bigint := (SELECT count(*) FROM media_scan_events);
  n_state bigint := (SELECT count(*) FROM media_assets WHERE state IN ('scanning', 'clean', 'infected', 'scan_failed') OR scan_attempt > 0);
BEGIN
  IF n_scan + n_state > 0 THEN
    RAISE EXCEPTION 'Retour arrière 0008 refusé : analyses déjà enregistrées (verdicts=%, médias analysés=%). Aucune donnée n''a été modifiée.',
      n_scan, n_state USING ERRCODE = 'object_in_use', HINT = 'nl:downgrade_unsafe';
  END IF;
END $$;

DROP TRIGGER IF EXISTS trg_users_05_media_scanner ON users;
DROP TRIGGER IF EXISTS trg_auth_sessions_05_media_scanner ON auth_sessions;
DROP TRIGGER IF EXISTS trg_invitations_05_media_scanner ON invitations;
DROP TRIGGER IF EXISTS trg_role_permissions_05_media_scanner ON role_permissions;
DROP TRIGGER IF EXISTS trg_user_roles_05_media_scanner ON user_roles;
DROP TRIGGER IF EXISTS trg_media_scan_events_05_scanner ON media_scan_events;
DROP TRIGGER IF EXISTS trg_media_assets_05_scanner ON media_assets;

-- Compte de service, son rôle et sa permission (aucun verdict ne les référence : vérifié ci-dessus).
DELETE FROM user_roles WHERE user_id = nl_media_scanner_account();
DELETE FROM users WHERE id = nl_media_scanner_account();
DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE code = 'media_scanner');
DELETE FROM roles WHERE code = 'media_scanner';

-- Privilèges de la connexion de service sur cette base.
REVOKE ALL ON media_assets, media_scan_events, users, user_roles, role_permissions FROM nl_media_scanner;
REVOKE ALL ON SCHEMA public FROM nl_media_scanner;
DO $$ BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM nl_media_scanner', current_database());
END $$;

DROP FUNCTION IF EXISTS nl_media_scanner_audit(text, uuid, jsonb);
DROP FUNCTION IF EXISTS nl_media_scanner_guard_account();
DROP FUNCTION IF EXISTS nl_media_scanner_guard_invitation();
DROP FUNCTION IF EXISTS nl_media_scanner_guard_permissions();
DROP FUNCTION IF EXISTS nl_media_scanner_guard_roles();
DROP FUNCTION IF EXISTS nl_media_scanner_guard_events();
DROP FUNCTION IF EXISTS nl_media_scanner_guard_assets();
DROP FUNCTION IF EXISTS nl_media_scanner_identity();
DROP FUNCTION IF EXISTS nl_is_media_scanner_session();
DROP FUNCTION IF EXISTS nl_media_scanner_provision();
DROP FUNCTION IF EXISTS nl_media_scanner_account();
