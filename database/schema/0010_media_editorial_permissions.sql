-- =============================================================================
-- Migration 0010 — 2.2.3-e (sous-lot E1) : rattachement des médias par l'API (D3-3) et conservation
-- légale contrôlée (S13). ADDITIVE et réversible : aucune fonction ni table antérieure n'est réécrite.
--
-- Principe (D14) : `nl_api` ne reçoit AUCUN droit d'écriture sur `lesson_version_media` ni sur les
-- colonnes de conservation de `media_assets`. Il reçoit seulement EXECUTE sur trois fonctions
-- SECURITY DEFINER (propriétaire nl_owner, chemin de recherche fixé), qui :
--   * exigent un acteur déclaré (nl.actor_id) ;
--   * passent par les déclencheurs existants (0007) : brouillon seulement, content:create dans la
--     portée, isolation entre établissements, média non infecté/révoqué/supprimé ; légal : user:manage ;
--   * ajoutent leurs propres contrôles (état du média, permission dédiée, justification).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Permission administrative dédiée à la conservation légale (rôle `admin` seulement)
-- -----------------------------------------------------------------------------
INSERT INTO permissions (code, description)
VALUES ('media:legal_hold', 'Poser ou lever une conservation légale sur un média (administration de la plateforme, justification obligatoire)')
ON CONFLICT (code) DO NOTHING;

CREATE OR REPLACE FUNCTION nl_guard_media_legal_hold_permission() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.permission_code = 'media:legal_hold'
     AND (SELECT code FROM roles WHERE id = NEW.role_id) IS DISTINCT FROM 'admin' THEN
    RAISE EXCEPTION 'La permission media:legal_hold est réservée au rôle admin'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;

DELETE FROM role_permissions rp USING roles r
 WHERE rp.role_id = r.id AND rp.permission_code = 'media:legal_hold' AND r.code <> 'admin';
CREATE TRIGGER trg_role_permissions_media_legal_hold BEFORE INSERT OR UPDATE ON role_permissions
  FOR EACH ROW EXECUTE FUNCTION nl_guard_media_legal_hold_permission();

INSERT INTO role_permissions (role_id, permission_code)
SELECT id, 'media:legal_hold' FROM roles WHERE code = 'admin'
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------
-- 2. Journal des conservations légales (ajout seul, justification conservée)
-- -----------------------------------------------------------------------------
-- Le motif (texte libre) n'entre jamais dans audit_logs (règle de l'audit : aucun texte libre) ;
-- il est conservé ici, en ajout seul, avec l'acteur, la portée et l'horodatage.
CREATE TABLE media_legal_hold_events (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  media_id         uuid NOT NULL REFERENCES media_assets(id) ON DELETE RESTRICT,
  action           text NOT NULL CHECK (action IN ('set', 'released')),
  reason           text NOT NULL CHECK (length(btrim(reason)) BETWEEN 5 AND 1000),
  actor_user_id    uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  organization_id  uuid REFERENCES organizations(id) ON DELETE RESTRICT,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_media_legal_hold_events_media ON media_legal_hold_events (media_id, created_at);

-- Insertion réservée à la fonction contrôlée (propriétaire) ; jamais de modification ni de suppression.
CREATE OR REPLACE FUNCTION nl_guard_media_legal_hold_event_origin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT nl_is_privileged_session() THEN
    RAISE EXCEPTION 'media_legal_hold_events est alimenté uniquement par nl_media_set_legal_hold'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_media_legal_hold_events_origin BEFORE INSERT ON media_legal_hold_events
  FOR EACH ROW EXECUTE FUNCTION nl_guard_media_legal_hold_event_origin();
CREATE TRIGGER trg_media_legal_hold_events_append_only BEFORE UPDATE OR DELETE ON media_legal_hold_events
  FOR EACH ROW EXECUTE FUNCTION nl_forbid_modification();
CREATE TRIGGER trg_media_legal_hold_events_no_truncate BEFORE TRUNCATE ON media_legal_hold_events
  FOR EACH STATEMENT EXECUTE FUNCTION nl_forbid_modification();

-- -----------------------------------------------------------------------------
-- 3. Rattachement et détachement contrôlés (D3-3)
-- -----------------------------------------------------------------------------
-- Renvoie vrai si le rattachement est créé, faux s'il existait déjà (opération idempotente).
CREATE OR REPLACE FUNCTION nl_media_attach(p_lesson_version uuid, p_media uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_actor uuid := nl_actor();
  v_state text;
BEGIN
  SELECT state INTO v_state FROM media_assets WHERE id = p_media AND deleted_at IS NULL AND purged_at IS NULL;
  IF v_state IS NULL THEN
    RAISE EXCEPTION 'Média introuvable' USING ERRCODE = 'no_data_found', HINT = 'nl:not_found';
  END IF;
  -- Seuls les médias reçus et en cours ou au terme d'une analyse réussie sont rattachables à un
  -- brouillon ; la soumission, l'approbation et la publication exigent ensuite l'état `clean` (0007).
  IF v_state NOT IN ('quarantined', 'scanning', 'clean') THEN
    RAISE EXCEPTION 'Média non rattachable (%)', v_state USING ERRCODE = 'check_violation', HINT = 'nl:media_not_clean';
  END IF;
  IF EXISTS (SELECT 1 FROM lesson_version_media WHERE lesson_version_id = p_lesson_version AND media_id = p_media) THEN
    -- Déjà rattaché : les contrôles de droits s'appliquent quand même (aucune information gratuite).
    IF NOT nl_user_has_permission(v_actor, 'content:create',
                                  (SELECT nl_lesson_org(lesson_id) FROM lesson_versions WHERE id = p_lesson_version)) THEN
      RAISE EXCEPTION 'Rattachement refusé : content:create requis dans cette portée'
        USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
    END IF;
    RETURN false;
  END IF;
  -- Le déclencheur 0007 vérifie : brouillon, content:create dans la portée, même établissement,
  -- média ni supprimé, ni infecté, ni révoqué ; il pose attached_by et attached_at.
  INSERT INTO lesson_version_media (lesson_version_id, media_id, attached_by) VALUES (p_lesson_version, p_media, v_actor);
  RETURN true;
END $$;

-- Renvoie vrai si un rattachement a été retiré ; le déclencheur 0007 refuse toute version non brouillon.
CREATE OR REPLACE FUNCTION nl_media_detach(p_lesson_version uuid, p_media uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_actor uuid := nl_actor();
  v_n     integer;
BEGIN
  IF NOT nl_user_has_permission(v_actor, 'content:create',
                                (SELECT nl_lesson_org(lesson_id) FROM lesson_versions WHERE id = p_lesson_version)) THEN
    RAISE EXCEPTION 'Détachement refusé : content:create requis dans cette portée'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  DELETE FROM lesson_version_media WHERE lesson_version_id = p_lesson_version AND media_id = p_media;
  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n > 0;
END $$;

-- -----------------------------------------------------------------------------
-- 4. Conservation légale contrôlée (S13)
-- -----------------------------------------------------------------------------
-- Pose (p_hold = true) ou levée (false), justification obligatoire dans les deux cas. Exige :
-- media:legal_hold dans la portée du média ET (déclencheur 0007) user:manage de la plateforme.
-- La conservation n'ouvre AUCUN accès : la diffusion exige toujours `clean` et un verdict valide.
CREATE OR REPLACE FUNCTION nl_media_set_legal_hold(p_media uuid, p_hold boolean, p_reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  v_actor  uuid := nl_actor();
  v_reason text := btrim(coalesce(p_reason, ''));
  v_media  media_assets%ROWTYPE;
  v_event  uuid;
BEGIN
  IF length(v_reason) NOT BETWEEN 5 AND 1000 THEN
    RAISE EXCEPTION 'Justification obligatoire (5 à 1000 caractères)' USING ERRCODE = 'check_violation', HINT = 'nl:reason_required';
  END IF;
  SELECT * INTO v_media FROM media_assets WHERE id = p_media FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Média introuvable' USING ERRCODE = 'no_data_found', HINT = 'nl:not_found';
  END IF;
  IF NOT nl_user_has_permission(v_actor, 'media:legal_hold', v_media.organization_id) THEN
    RAISE EXCEPTION 'Conservation légale : permission media:legal_hold requise dans cette portée'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  IF v_media.purged_at IS NOT NULL THEN
    RAISE EXCEPTION 'Média purgé' USING ERRCODE = 'check_violation', HINT = 'nl:purged';
  END IF;
  IF v_media.legal_hold = p_hold THEN
    RAISE EXCEPTION 'Aucun changement : conservation déjà %', CASE WHEN p_hold THEN 'posée' ELSE 'levée' END
      USING ERRCODE = 'check_violation', HINT = 'nl:no_change';
  END IF;
  -- Le déclencheur 0007 revérifie le droit (user:manage) et journalise media.legal_hold_set/released.
  UPDATE media_assets
     SET legal_hold = p_hold, legal_hold_reason = CASE WHEN p_hold THEN v_reason ELSE NULL END
   WHERE id = p_media;
  INSERT INTO media_legal_hold_events (media_id, action, reason, actor_user_id, organization_id)
  VALUES (p_media, CASE WHEN p_hold THEN 'set' ELSE 'released' END, v_reason, v_actor, v_media.organization_id)
  RETURNING id INTO v_event;
  RETURN v_event;
END $$;

-- -----------------------------------------------------------------------------
-- 5. Privilèges : EXECUTE nominatif pour l'API, lecture du journal ; aucune écriture directe
-- -----------------------------------------------------------------------------
REVOKE ALL ON FUNCTION nl_media_attach(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION nl_media_detach(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION nl_media_set_legal_hold(uuid, boolean, text) FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nl_api') THEN
    GRANT EXECUTE ON FUNCTION nl_media_attach(uuid, uuid) TO nl_api;
    GRANT EXECUTE ON FUNCTION nl_media_detach(uuid, uuid) TO nl_api;
    GRANT EXECUTE ON FUNCTION nl_media_set_legal_hold(uuid, boolean, text) TO nl_api;
    GRANT SELECT ON media_legal_hold_events TO nl_api;
  END IF;
END $$;
REVOKE ALL ON media_legal_hold_events FROM PUBLIC;

-- Les objets créés ci-dessus reviennent au propriétaire nl_owner (D14).
SELECT nl_enforce_ownership();
