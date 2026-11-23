-- =============================================================================
-- Migration 0008 — Rôle de service media_scanner (étape 2.2.3-c, ADR-0011, S5, S7, S9, S14)
-- ADDITIVE : aucune table ni fonction existante n'est modifiée ; 0007 reste intacte.
--
-- DEUX notions distinctes :
--   1. Le rôle MÉTIER `media_scanner` (table roles) : seul détenteur de `media:scan`, attribué à un
--      unique compte de service (sans mot de passe ni session), sans aucune autre permission.
--   2. Le rôle de CONNEXION PostgreSQL `nl_media_scanner` : identité authentifiée par PostgreSQL
--      (mot de passe posé hors dépôt par l'exploitation), non superutilisateur, aux privilèges
--      minimaux sur cette base.
-- Un verdict antivirus exige LES DEUX : la session doit être ouverte par `nl_media_scanner`
-- (session_user, établi par l'authentification PostgreSQL, non falsifiable par un client ordinaire)
-- ET l'acteur déclaré doit être le compte de service. Un nom de rôle ou un acteur déclarés par un
-- client ne suffisent jamais.
--
-- Limite assumée (documentée, VALIDATION-PHASE2.2.3C §7) : un superutilisateur PostgreSQL peut
-- tout contourner (SET SESSION AUTHORIZATION, désactivation des déclencheurs). L'API se connecte
-- encore en superutilisateur en développement : le passage à un compte applicatif ordinaire est
-- un prérequis de production.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Rôle de connexion PostgreSQL (objet de CLUSTER : créé s'il manque, jamais supprimé ici)
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nl_media_scanner') THEN
    -- NOLOGIN : la connexion n'est ouverte que lorsque l'exploitation pose un mot de passe.
    CREATE ROLE nl_media_scanner NOLOGIN NOINHERIT CONNECTION LIMIT 20;
  END IF;
END $$;
-- Attributs dangereux retirés même si le rôle préexistait.
ALTER ROLE nl_media_scanner NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

-- Aucun héritage d'un autre rôle : nl_media_scanner n'est membre d'aucun rôle.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
            JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = 'nl_media_scanner' LOOP
    EXECUTE format('REVOKE %I FROM nl_media_scanner', r.rolname);
  END LOOP;
END $$;

-- -----------------------------------------------------------------------------
-- 2. Rôle métier et compte de service
-- -----------------------------------------------------------------------------
INSERT INTO roles (id, code, name_i18n)
SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM roles WHERE id = 6) THEN 6
            ELSE (SELECT max(id) + 1 FROM roles) END,
       'media_scanner', '{"fr":"Service d''analyse antivirus","en":"Antivirus scanning service"}'
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE code = 'media_scanner');

-- Permission unique du rôle (la garde 0007 n'autorise media:scan qu'à ce rôle).
INSERT INTO role_permissions (role_id, permission_code)
SELECT id, 'media:scan' FROM roles WHERE code = 'media_scanner'
ON CONFLICT DO NOTHING;

-- Identifiant FIXE du compte de service (référencé par le worker et les gardes).
CREATE OR REPLACE FUNCTION nl_media_scanner_account() RETURNS uuid LANGUAGE sql IMMUTABLE AS $$
  SELECT '00000000-0000-4000-a000-00000000c1a5'::uuid
$$;

-- Création idempotente du compte de service : sans mot de passe (aucune connexion à l'API),
-- e-mail non routable, rôle media_scanner global. Exécutable seulement par le propriétaire du
-- schéma (migration, données de référence) ou via nl_media_scanner_identity() ci-dessous.
CREATE OR REPLACE FUNCTION nl_media_scanner_provision() RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_id uuid := nl_media_scanner_account();
BEGIN
  IF NOT EXISTS (SELECT 1 FROM locales WHERE code = 'fr') THEN
    RETURN NULL;   -- base vide : les données de référence ne sont pas encore chargées
  END IF;
  INSERT INTO users (id, email, password_hash, status, email_verified_at, locale)
  VALUES (v_id, 'media-scanner@service.nurselearn.invalid', NULL, 'active', now(), 'fr')
  ON CONFLICT (id) DO NOTHING;
  IF NOT EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                  WHERE ur.user_id = v_id AND r.code = 'media_scanner') THEN
    PERFORM set_config('nl.role_grant_bypass', 'on', true);
    INSERT INTO user_roles (user_id, role_id, organization_id, granted_by)
    SELECT v_id, id, NULL, NULL FROM roles WHERE code = 'media_scanner';
    PERFORM set_config('nl.role_grant_bypass', '', true);
  END IF;
  RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION nl_media_scanner_provision() FROM PUBLIC;

SELECT nl_media_scanner_provision();

-- -----------------------------------------------------------------------------
-- 3. Identité de service : vérification côté base (session_user + acteur)
-- -----------------------------------------------------------------------------
-- Vrai seulement si la session est AUTHENTIFIÉE par PostgreSQL comme nl_media_scanner et que
-- l'acteur déclaré est le compte de service.
CREATE OR REPLACE FUNCTION nl_is_media_scanner_session() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT session_user = 'nl_media_scanner'
     AND nullif(current_setting('nl.actor_id', true), '') IS NOT NULL
     AND nullif(current_setting('nl.actor_id', true), '')::uuid = nl_media_scanner_account()
$$;

-- Point d'entrée du worker : vérifie l'identité, provisionne le compte s'il manque, le renvoie.
CREATE OR REPLACE FUNCTION nl_media_scanner_identity() RETURNS uuid LANGUAGE plpgsql
SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_id uuid;
BEGIN
  IF session_user <> 'nl_media_scanner' THEN
    RAISE EXCEPTION 'Identité de service réservée à la connexion nl_media_scanner'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  v_id := nl_media_scanner_provision();
  IF v_id IS NULL OR NOT nl_user_has_permission(v_id, 'media:scan', NULL) THEN
    RAISE EXCEPTION 'Compte de service d''analyse absent ou sans media:scan'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN v_id;
END $$;
REVOKE ALL ON FUNCTION nl_media_scanner_identity() FROM PUBLIC;

-- -----------------------------------------------------------------------------
-- 4. Gardes : verdicts et transitions d'analyse seulement par la session de service
-- -----------------------------------------------------------------------------
-- Déclencheur « 05 » : s'exécute AVANT la garde 0007 (« 10 »), qui reste entièrement appliquée.
CREATE OR REPLACE FUNCTION nl_media_scanner_guard_assets() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_scan boolean;
BEGIN
  v_scan := (NEW.state <> OLD.state AND (OLD.state, NEW.state) IN (
               ('quarantined', 'scanning'), ('scan_failed', 'scanning'), ('infected', 'scanning'),
               ('scanning', 'quarantined'), ('scanning', 'clean'), ('scanning', 'infected'),
               ('scanning', 'scan_failed'), ('clean', 'infected')))
            OR NEW.clean_key IS DISTINCT FROM OLD.clean_key
            OR NEW.scan_attempt <> OLD.scan_attempt;
  IF v_scan AND NOT nl_is_media_scanner_session() THEN
    RAISE EXCEPTION 'Transition d''analyse réservée à la connexion de service nl_media_scanner'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  -- La connexion de service n'agit jamais sous une autre identité ni hors analyse.
  IF session_user = 'nl_media_scanner' AND (NOT nl_is_media_scanner_session() OR NOT v_scan) THEN
    RAISE EXCEPTION 'La connexion de service ne modifie un média que pour une transition d''analyse'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_media_assets_05_scanner BEFORE UPDATE ON media_assets
  FOR EACH ROW EXECUTE FUNCTION nl_media_scanner_guard_assets();

CREATE OR REPLACE FUNCTION nl_media_scanner_guard_events() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT nl_is_media_scanner_session() THEN
    RAISE EXCEPTION 'Verdict antivirus réservé à la connexion de service nl_media_scanner'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_media_scan_events_05_scanner BEFORE INSERT ON media_scan_events
  FOR EACH ROW EXECUTE FUNCTION nl_media_scanner_guard_events();

-- Le rôle métier media_scanner : uniquement le compte de service, global, et rien d'autre.
CREATE OR REPLACE FUNCTION nl_media_scanner_guard_roles() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_code text;
BEGIN
  SELECT code INTO v_code FROM roles WHERE id = NEW.role_id;
  IF v_code = 'media_scanner' AND (NEW.user_id <> nl_media_scanner_account() OR NEW.organization_id IS NOT NULL) THEN
    RAISE EXCEPTION 'Le rôle media_scanner est réservé au compte de service, sans établissement'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  IF v_code IS DISTINCT FROM 'media_scanner' AND NEW.user_id = nl_media_scanner_account() THEN
    RAISE EXCEPTION 'Le compte de service d''analyse ne détient aucun autre rôle'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_user_roles_05_media_scanner BEFORE INSERT OR UPDATE ON user_roles
  FOR EACH ROW EXECUTE FUNCTION nl_media_scanner_guard_roles();

CREATE OR REPLACE FUNCTION nl_media_scanner_guard_permissions() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT code FROM roles WHERE id = NEW.role_id) = 'media_scanner' AND NEW.permission_code <> 'media:scan' THEN
    RAISE EXCEPTION 'Le rôle media_scanner ne détient que media:scan'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_role_permissions_05_media_scanner BEFORE INSERT OR UPDATE ON role_permissions
  FOR EACH ROW EXECUTE FUNCTION nl_media_scanner_guard_permissions();

CREATE OR REPLACE FUNCTION nl_media_scanner_guard_invitation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (SELECT code FROM roles WHERE id = NEW.role_id) = 'media_scanner' THEN
    RAISE EXCEPTION 'Le rôle media_scanner ne s''obtient pas par invitation'
      USING ERRCODE = 'check_violation', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_invitations_05_media_scanner BEFORE INSERT OR UPDATE ON invitations
  FOR EACH ROW EXECUTE FUNCTION nl_media_scanner_guard_invitation();

-- Le compte de service n'a ni mot de passe, ni session, ni changement d'identité.
CREATE OR REPLACE FUNCTION nl_media_scanner_guard_account() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME = 'auth_sessions' THEN
    IF NEW.user_id = nl_media_scanner_account() THEN
      RAISE EXCEPTION 'Aucune session pour le compte de service d''analyse'
        USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
    END IF;
  ELSIF NEW.id = nl_media_scanner_account() AND (NEW.password_hash IS NOT NULL OR NEW.email IS DISTINCT FROM OLD.email) THEN
    RAISE EXCEPTION 'Compte de service d''analyse : ni mot de passe ni changement d''adresse'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_auth_sessions_05_media_scanner BEFORE INSERT ON auth_sessions
  FOR EACH ROW EXECUTE FUNCTION nl_media_scanner_guard_account();
CREATE TRIGGER trg_users_05_media_scanner BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION nl_media_scanner_guard_account();

-- -----------------------------------------------------------------------------
-- 5. Audit de la connexion de service : par fonction, sans accès à la table d'audit
-- -----------------------------------------------------------------------------
-- Le worker n'a ni lecture ni écriture directe sur audit_logs. Actions limitées aux familles
-- media.scan_* et media.reconcile_* ; métadonnées plates, bornées, sans contenu de fichier.
CREATE OR REPLACE FUNCTION nl_media_scanner_audit(p_action text, p_media uuid, p_metadata jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE v_org uuid;
BEGIN
  IF NOT nl_is_media_scanner_session() THEN
    RAISE EXCEPTION 'Audit réservé à la connexion de service nl_media_scanner'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  IF p_action !~ '^media\.(scan|reconcile)_[a-z_]+$' OR jsonb_typeof(coalesce(p_metadata, '{}'::jsonb)) <> 'object'
     OR length(coalesce(p_metadata, '{}'::jsonb)::text) > 2000 THEN
    RAISE EXCEPTION 'Événement d''audit de service invalide' USING ERRCODE = 'check_violation', HINT = 'nl:invalid';
  END IF;
  IF p_media IS NOT NULL THEN
    SELECT organization_id INTO v_org FROM media_assets WHERE id = p_media;
  END IF;
  INSERT INTO audit_logs (actor_user_id, actor_role, action, entity_type, entity_id, organization_id, metadata)
  VALUES (nl_media_scanner_account(), 'media_scanner', p_action, 'media_asset', p_media::text, v_org,
          coalesce(p_metadata, '{}'::jsonb));
END $$;
REVOKE ALL ON FUNCTION nl_media_scanner_audit(text, uuid, jsonb) FROM PUBLIC;

-- -----------------------------------------------------------------------------
-- 6. Privilèges minimaux de la connexion de service sur CETTE base
-- -----------------------------------------------------------------------------
DO $$ BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO nl_media_scanner', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO nl_media_scanner;
-- Lecture des médias (la garde 0007 relit la ligne complète) et des verdicts.
GRANT SELECT ON media_assets, media_scan_events TO nl_media_scanner;
-- Transitions d'analyse seulement : ni métadonnées, ni contenu reçu, ni suppression, ni purge.
GRANT UPDATE (state, scan_attempt, clean_key) ON media_assets TO nl_media_scanner;
-- Journal des verdicts en ajout seul (la garde 0007 impose acteur et horodatage).
GRANT INSERT (media_id, attempt, result, engine, engine_version, signature_version,
              signatures_published_at, threat_name, sha256, duration_ms, scanned_by)
  ON media_scan_events TO nl_media_scanner;
-- Vérification des permissions (nl_user_has_permission) : colonnes strictement nécessaires,
-- sans e-mail ni empreinte de mot de passe.
GRANT SELECT (id, status, deleted_at) ON users TO nl_media_scanner;
GRANT SELECT (user_id, role_id, organization_id) ON user_roles TO nl_media_scanner;
GRANT SELECT ON role_permissions TO nl_media_scanner;
GRANT EXECUTE ON FUNCTION nl_media_scanner_identity() TO nl_media_scanner;
GRANT EXECUTE ON FUNCTION nl_media_scanner_audit(text, uuid, jsonb) TO nl_media_scanner;
