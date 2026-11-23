-- =============================================================================
-- Retour arrière de la migration 0009 (D14). Restaure exactement les propriétaires et privilèges de
-- 0008 : objets rendus au compte qui exécute le retour arrière (administration du cluster),
-- schéma public rendu à pg_database_owner avec USAGE pour PUBLIC, base ouverte à PUBLIC.
-- Les rôles de cluster nl_owner et nl_api ne sont ni supprimés ni modifiés (ils peuvent servir à
-- d'autres bases) : ils perdent seulement leurs droits sur CETTE base. Aucune donnée touchée.
-- =============================================================================
DROP TRIGGER IF EXISTS trg_editorial_decisions_04_origin ON editorial_decisions;
DROP TRIGGER IF EXISTS trg_invitations_04_privileged_bypass ON invitations;
DROP TRIGGER IF EXISTS trg_user_roles_04_privileged_bypass ON user_roles;
DROP FUNCTION IF EXISTS nl_guard_editorial_decision_origin();
DROP FUNCTION IF EXISTS nl_guard_privileged_bypass();
DROP FUNCTION IF EXISTS nl_is_privileged_session();

ALTER FUNCTION nl_media_reserve(text, uuid, uuid, bigint) SECURITY INVOKER RESET search_path;
ALTER FUNCTION nl_media_usage_row(text, uuid, uuid) SECURITY INVOKER RESET search_path;
REVOKE ALL ON FUNCTION nl_media_reserve(text, uuid, uuid, bigint) FROM nl_api;
GRANT EXECUTE ON FUNCTION nl_media_reserve(text, uuid, uuid, bigint) TO PUBLIC;
GRANT EXECUTE ON FUNCTION nl_media_usage_row(text, uuid, uuid) TO PUBLIC;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nl_api;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM nl_api;
REVOKE ALL ON SCHEMA public FROM nl_api;
DO $$ BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM nl_api', current_database());
  EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO PUBLIC', current_database());
END $$;

-- Propriété rendue à l'exécutant (comme avant 0009), puis schéma public à pg_database_owner.
SELECT nl_enforce_ownership(current_user);
DROP FUNCTION nl_enforce_ownership(name);
ALTER SCHEMA public OWNER TO pg_database_owner;
GRANT USAGE ON SCHEMA public TO PUBLIC;
