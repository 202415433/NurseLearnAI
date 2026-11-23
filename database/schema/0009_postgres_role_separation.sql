-- =============================================================================
-- Migration 0009 — D14 : séparation des rôles PostgreSQL (propriétaire, API, worker antivirus)
-- ADDITIVE : aucune table ni colonne créée ou modifiée. Les migrations 0001 à 0008 restent
-- intactes : seuls des PROPRIÉTAIRES, des PRIVILÈGES, deux attributs de fonctions de quota et
-- quatre gardes supplémentaires sont posés.
--
-- Rôles (objets de CLUSTER : créés s'ils manquent, jamais supprimés ici) :
--   nl_owner          NOLOGIN — propriétaire de tous les objets du schéma public ; personne ne s'y
--                     connecte ; seuls les superutilisateurs (migration contrôlée) agissent en son nom.
--   nl_api            connexion de l'API : ni superutilisateur, ni propriétaire, ni membre d'aucun
--                     rôle ; privilèges table par table (voir § 5).
--   nl_media_scanner  connexion du worker antivirus (0008), inchangée.
-- La MIGRATION s'exécute avec le compte d'administration du cluster (conteneur ponctuel
-- `migrate`), jamais avec la connexion de l'API ni celle du worker.
--
-- Effet principal : l'API ne peut plus désactiver un déclencheur, modifier une fonction, changer
-- d'identité, créer un objet (ni table temporaire, ni fonction, ni déclencheur), modifier un
-- verdict, un quota ou les permissions. Les invariants des migrations 0003 à 0008 deviennent
-- opposables à une API compromise, et plus seulement à ses erreurs.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Rôles de cluster
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nl_owner') THEN
    CREATE ROLE nl_owner NOLOGIN NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nl_api') THEN
    -- NOLOGIN : la connexion n'est ouverte que lorsque l'exploitation pose un mot de passe.
    CREATE ROLE nl_api NOLOGIN NOINHERIT CONNECTION LIMIT 200;
  END IF;
END $$;
ALTER ROLE nl_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE nl_api NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;

-- Aucune appartenance : ni nl_api ni nl_owner ne sont membres d'un rôle, et PERSONNE n'est membre
-- de nl_owner (aucun SET ROLE possible vers le propriétaire, hors superutilisateur).
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT g.rolname AS grp, u.rolname AS member
             FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member
            WHERE u.rolname IN ('nl_api', 'nl_owner', 'nl_media_scanner') OR g.rolname = 'nl_owner' LOOP
    EXECUTE format('REVOKE %I FROM %I', r.grp, r.member);
  END LOOP;
END $$;

-- -----------------------------------------------------------------------------
-- 2. Propriété : tout objet du schéma public appartient à nl_owner
-- -----------------------------------------------------------------------------
-- Appelée par cette migration puis à la fin de CHAQUE exécution d'Alembic (alembic/env.py) : les
-- objets créés par une migration future reviennent aussitôt à nl_owner. Exige un superutilisateur.
-- Les objets d'extension (pgvector, pgcrypto…) gardent leur propriétaire.
CREATE OR REPLACE FUNCTION nl_enforce_ownership(p_target name DEFAULT 'nl_owner') RETURNS integer
LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE r record; n integer := 0; v_target oid := (SELECT oid FROM pg_roles WHERE rolname = p_target);
BEGIN
  IF v_target IS NULL THEN RAISE EXCEPTION 'Rôle propriétaire % absent', p_target; END IF;
  IF (SELECT nspowner FROM pg_namespace WHERE nspname = 'public') <> v_target THEN
    EXECUTE format('ALTER SCHEMA public OWNER TO %I', p_target); n := n + 1;
  END IF;
  -- Tables, vues, vues matérialisées, séquences autonomes (une séquence liée à une colonne suit sa table).
  FOR r IN SELECT c.oid, c.relkind, format('%I.%I', n2.nspname, c.relname) AS qname
             FROM pg_class c JOIN pg_namespace n2 ON n2.oid = c.relnamespace
            WHERE n2.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') AND c.relowner <> v_target
              AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
              AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                                                     AND d.objid = c.oid AND d.deptype IN ('a', 'i'))) LOOP
    EXECUTE format('ALTER %s %s OWNER TO %I',
                   CASE r.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'S' THEN 'SEQUENCE'
                                  WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END, r.qname, p_target);
    n := n + 1;
  END LOOP;
  -- Fonctions et procédures.
  FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p JOIN pg_namespace n2 ON n2.oid = p.pronamespace
            WHERE n2.nspname = 'public' AND p.proowner <> v_target
              AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e') LOOP
    EXECUTE format('ALTER ROUTINE %s OWNER TO %I', r.sig, p_target); n := n + 1;
  END LOOP;
  -- Types autonomes (domaines, énumérations, types composites hors lignes de table).
  FOR r IN SELECT format('%I.%I', n2.nspname, t.typname) AS qname FROM pg_type t JOIN pg_namespace n2 ON n2.oid = t.typnamespace
            WHERE n2.nspname = 'public' AND t.typtype IN ('d', 'e', 'c', 'r', 'm') AND t.typowner <> v_target
              AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.reltype = t.oid)
              AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype IN ('e', 'i')) LOOP
    EXECUTE format('ALTER TYPE %s OWNER TO %I', r.qname, p_target); n := n + 1;
  END LOOP;
  RETURN n;
END $$;
REVOKE ALL ON FUNCTION nl_enforce_ownership(name) FROM PUBLIC;

SELECT nl_enforce_ownership();

-- -----------------------------------------------------------------------------
-- 3. Base et schéma : aucun objet créé par les connexions applicatives
-- -----------------------------------------------------------------------------
-- Ni CREATE, ni tables TEMPORAIRES (donc ni fonction, ni déclencheur propres) : une connexion
-- applicative ne peut ni créer un objet, ni fabriquer un contexte de déclencheur.
DO $$ BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO nl_api, nl_media_scanner', current_database());
END $$;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO nl_api, nl_media_scanner;

-- -----------------------------------------------------------------------------
-- 4. Fonctions : seules celles qui l'exigent s'exécutent avec les droits du propriétaire
-- -----------------------------------------------------------------------------
-- Réservation de quota (appelée par le déclencheur de media_assets dans la session de l'API) :
-- verrou FOR UPDATE et création de la ligne au quota par défaut. En SECURITY DEFINER, l'API n'a
-- plus AUCUN droit d'écriture sur media_usage : un quota ne se modifie que par l'administration
-- (connexion de migration), jamais par l'API. Chemin de recherche fixé ; EXECUTE restreint.
ALTER FUNCTION nl_media_reserve(text, uuid, uuid, bigint) SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION nl_media_usage_row(text, uuid, uuid) SECURITY DEFINER SET search_path = public, pg_temp;
REVOKE ALL ON FUNCTION nl_media_reserve(text, uuid, uuid, bigint) FROM PUBLIC;
REVOKE ALL ON FUNCTION nl_media_usage_row(text, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION nl_media_reserve(text, uuid, uuid, bigint) TO nl_api;
-- Fonctions de service (0008) : inchangées (EXECUTE nominatif pour nl_media_scanner seulement).

-- -----------------------------------------------------------------------------
-- 5. Privilèges de l'API (inventaire des écritures réelles de l'API, vérifié par toute sa suite
--    de tests exécutée sous nl_api). Jamais : TRUNCATE, REFERENCES, TRIGGER.
-- -----------------------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM nl_api;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM nl_api;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nl_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nl_api;

-- Comptes, sessions, établissements.
GRANT INSERT, UPDATE ON users, profiles, auth_sessions, one_time_tokens, invitations, organizations TO nl_api;
GRANT INSERT, DELETE ON user_roles TO nl_api;
-- Contenus pédagogiques (le workflow et l'immutabilité des éditions restent imposés par les déclencheurs).
GRANT INSERT, UPDATE ON programs, modules, lessons, lesson_versions, module_versions, program_releases,
                        medical_references, lesson_version_references, module_version_lessons,
                        module_version_lesson_versions, program_release_modules, program_release_prerequisites
  TO nl_api;
GRANT DELETE ON lesson_versions, module_versions, lesson_version_references, module_version_lessons,
                module_version_lesson_versions, program_release_modules, program_release_prerequisites
  TO nl_api;
-- Journaux en ajout seul : insertion seulement (décisions éditoriales : par les transitions, voir § 6).
GRANT INSERT ON audit_logs, editorial_decisions TO nl_api;
-- Médias : réservation et réception ; jamais les colonnes d'analyse, de conservation ni de purge.
GRANT INSERT ON media_assets TO nl_api;
GRANT UPDATE (state, byte_size, sha256, quarantine_key, revoked_reason, display_name, license, attribution,
              alt_text_i18n, captions_for, reservation_expires_at, deleted_at, deleted_reason)
  ON media_assets TO nl_api;
-- Lecture seule pour l'API : roles, permissions, role_permissions, référentiels, media_usage (quotas),
-- media_scan_events (verdicts), tables des phases futures (aucune écriture tant qu'elles ne sont pas
-- livrées : chaque phase accordera explicitement ses droits dans sa propre migration).

-- -----------------------------------------------------------------------------
-- 6. Gardes : aucune opération privilégiée autorisée par une simple variable de session
-- -----------------------------------------------------------------------------
-- Vrai seulement pour un superutilisateur ou pour le propriétaire nl_owner (fonctions SECURITY
-- DEFINER). current_user n'est pas déclarable par un client : il résulte de l'authentification et
-- des seules fonctions SECURITY DEFINER du propriétaire.
CREATE OR REPLACE FUNCTION nl_is_privileged_session() RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT current_user = 'nl_owner' OR coalesce((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false)
$$;

-- nl.role_grant_bypass (attribution de rôles plateforme, compte de service) : variable ignorée hors
-- session privilégiée — l'API ne peut plus s'attribuer un rôle d'administration.
CREATE OR REPLACE FUNCTION nl_guard_privileged_bypass() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF current_setting('nl.role_grant_bypass', true) = 'on' AND NOT nl_is_privileged_session() THEN
    RAISE EXCEPTION 'nl.role_grant_bypass réservé à l''administration (connexion de migration)'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_user_roles_04_privileged_bypass BEFORE INSERT OR UPDATE ON user_roles
  FOR EACH ROW EXECUTE FUNCTION nl_guard_privileged_bypass();
CREATE TRIGGER trg_invitations_04_privileged_bypass BEFORE INSERT OR UPDATE ON invitations
  FOR EACH ROW EXECUTE FUNCTION nl_guard_privileged_bypass();

-- Décisions éditoriales : écrites par le déclencheur de transition (profondeur ≥ 2), jamais par une
-- insertion directe, même avec nl.editorial_log = 'on'. Les connexions applicatives ne pouvant créer
-- ni table temporaire, ni fonction, ni déclencheur, elles ne peuvent pas fabriquer cette profondeur.
CREATE OR REPLACE FUNCTION nl_guard_editorial_decision_origin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF pg_trigger_depth() < 2 AND NOT nl_is_privileged_session() THEN
    RAISE EXCEPTION 'editorial_decisions est alimenté uniquement par les transitions éditoriales'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_editorial_decisions_04_origin BEFORE INSERT ON editorial_decisions
  FOR EACH ROW EXECUTE FUNCTION nl_guard_editorial_decision_origin();

-- Les fonctions et déclencheurs créés ci-dessus reviennent au propriétaire.
SELECT nl_enforce_ownership();
