-- =============================================================================
-- Retour arrière de la migration 0007 (fondations des médias protégés, étape 2.2.3-a).
-- Restaure exactement le schéma 0006 lorsque c'est sûr ; sinon s'ARRÊTE avec un message
-- explicite : aucune donnée n'est jamais supprimée silencieusement (refus d'un retour destructeur).
-- Situations refusées : médias, verdicts d'analyse, rattachements, lignes de quota, ou
-- permission media:scan attribuée à un rôle.
-- =============================================================================
DO $$
DECLARE
  n_media bigint := (SELECT count(*) FROM media_assets);
  n_scan  bigint := (SELECT count(*) FROM media_scan_events);
  n_link  bigint := (SELECT count(*) FROM lesson_version_media);
  n_usage bigint := (SELECT count(*) FROM media_usage);
  n_perm  bigint := (SELECT count(*) FROM role_permissions WHERE permission_code = 'media:scan');
BEGIN
  IF n_media + n_scan + n_link + n_usage + n_perm > 0 THEN
    RAISE EXCEPTION 'Retour arrière 0007 refusé : données propres à 0007 présentes (médias=%, verdicts=%, rattachements=%, quotas=%, rôles media:scan=%). Aucune donnée n''a été modifiée.',
      n_media, n_scan, n_link, n_usage, n_perm
      USING ERRCODE = 'object_in_use', HINT = 'nl:downgrade_unsafe';
  END IF;
END $$;

DROP TRIGGER IF EXISTS trg_lesson_versions_30_media ON lesson_versions;
DROP FUNCTION IF EXISTS nl_lesson_version_media_gate();

DROP VIEW media_usage_totals;
DROP TABLE lesson_version_media;
DROP TABLE media_scan_events;
DROP TABLE media_assets;
DROP TABLE media_usage;

DROP FUNCTION IF EXISTS nl_lesson_version_media_guard();
DROP FUNCTION IF EXISTS nl_media_scan_event_guard();
DROP FUNCTION IF EXISTS nl_media_expire_reservations();
DROP FUNCTION IF EXISTS nl_media_assets_before();
DROP FUNCTION IF EXISTS nl_media_is_reported(uuid);
DROP FUNCTION IF EXISTS nl_media_is_protected(uuid);
DROP FUNCTION IF EXISTS nl_media_is_attached(uuid);
DROP FUNCTION IF EXISTS nl_media_usage_guard();
DROP FUNCTION IF EXISTS nl_media_reserve(text, uuid, uuid, bigint);
DROP FUNCTION IF EXISTS nl_media_scope_usage(text, uuid, uuid);
DROP FUNCTION IF EXISTS nl_media_usage_row(text, uuid, uuid);
DROP FUNCTION IF EXISTS nl_media_default_quota(text);
DROP FUNCTION IF EXISTS nl_media_max_bytes(text);

DROP TRIGGER IF EXISTS trg_role_permissions_media_scan ON role_permissions;
DROP FUNCTION IF EXISTS nl_guard_media_scan_permission();

DELETE FROM permissions WHERE code = 'media:scan';
