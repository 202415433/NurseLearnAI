-- =============================================================================
-- Retour arrière de la migration 0010. Retire les fonctions contrôlées, le journal des
-- conservations légales et la permission media:legal_hold. Les conservations légales en cours
-- (colonne legal_hold de media_assets, 0007) et les rattachements existants sont CONSERVÉS.
-- Refusé si le journal contient des événements (historique légal jamais effacé).
-- =============================================================================
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM media_legal_hold_events) THEN
    RAISE EXCEPTION 'Retour arrière refusé : le journal des conservations légales contient des événements';
  END IF;
END $$;

DROP FUNCTION IF EXISTS nl_media_set_legal_hold(uuid, boolean, text);
DROP FUNCTION IF EXISTS nl_media_detach(uuid, uuid);
DROP FUNCTION IF EXISTS nl_media_attach(uuid, uuid);
DROP TABLE IF EXISTS media_legal_hold_events;
DROP FUNCTION IF EXISTS nl_guard_media_legal_hold_event_origin();
DROP TRIGGER IF EXISTS trg_role_permissions_media_legal_hold ON role_permissions;
DROP FUNCTION IF EXISTS nl_guard_media_legal_hold_permission();
DELETE FROM role_permissions WHERE permission_code = 'media:legal_hold';
DELETE FROM permissions WHERE code = 'media:legal_hold';
