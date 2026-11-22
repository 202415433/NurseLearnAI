-- =============================================================================
-- Migration 0007 — Fondations des médias protégés (étape 2.2.3-a, ADR-0011, D7, D15–D24)
-- ADDITIVE : aucune table, fonction ni donnée existante n'est modifiée ; `learning_resources`
-- (0001) est conservée intacte. Aucun point d'accès, aucun worker : uniquement le schéma et
-- ses invariants. En 2.2.3-a, AUCUN média ne peut devenir `clean` : la permission `media:scan`,
-- seule capable d'enregistrer un verdict antivirus, n'est attribuée à aucun rôle (2.2.3-c).
--
-- Tables : media_assets, media_scan_events (ajout seul), lesson_version_media, media_usage.
-- Erreurs : SQLSTATE stable + HINT 'nl:<code>' (traduites par l'API, convention 0006).
-- Unités BINAIRES : octets ; 1 Mio = 1 048 576 octets ; 1 Gio = 1 073 741 824 octets.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Permission d'enregistrement d'un verdict antivirus (attribuée à AUCUN rôle ici)
-- -----------------------------------------------------------------------------
INSERT INTO permissions (code, description)
VALUES ('media:scan', 'Enregistrer un verdict antivirus (identité de service du worker, étape 2.2.3-c)')
ON CONFLICT (code) DO NOTHING;

-- media:scan ne peut être détenue QUE par le rôle de service dédié `media_scanner` (créé en
-- 2.2.3-c) : ni l'administrateur, ni un relecteur, ni aucun autre rôle (S14).
CREATE OR REPLACE FUNCTION nl_guard_media_scan_permission() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.permission_code = 'media:scan'
     AND (SELECT code FROM roles WHERE id = NEW.role_id) IS DISTINCT FROM 'media_scanner' THEN
    RAISE EXCEPTION 'La permission media:scan est réservée au rôle de service media_scanner'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  RETURN NEW;
END $$;

-- Installations existantes : si une permission media:scan existait déjà (créée hors migration),
-- toute attribution à un autre rôle que media_scanner est retirée AVANT la pose de la garde.
DELETE FROM role_permissions rp USING roles r
 WHERE rp.role_id = r.id AND rp.permission_code = 'media:scan' AND r.code <> 'media_scanner';

CREATE TRIGGER trg_role_permissions_media_scan BEFORE INSERT OR UPDATE ON role_permissions
  FOR EACH ROW EXECUTE FUNCTION nl_guard_media_scan_permission();

-- -----------------------------------------------------------------------------
-- 2. Paramètres (D15, D17) : fonctions immuables, sans table de réglage modifiable
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_media_max_bytes(p_kind text) RETURNS bigint LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_kind
    WHEN 'image'    THEN 5::bigint   * 1048576   -- 5 Mio
    WHEN 'document' THEN 20::bigint  * 1048576   -- 20 Mio (PDF)
    WHEN 'captions' THEN 1::bigint   * 1048576   -- 1 Mio (WebVTT)
    WHEN 'video'    THEN 200::bigint * 1048576   -- 200 Mio (développement ; production à fixer)
  END
$$;

CREATE OR REPLACE FUNCTION nl_media_default_quota(p_scope text) RETURNS bigint LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE p_scope
    WHEN 'user'         THEN 1::bigint  * 1073741824   -- 1 Gio
    WHEN 'organization' THEN 5::bigint  * 1073741824   -- 5 Gio
    WHEN 'platform'     THEN 20::bigint * 1073741824   -- 20 Gio
  END
$$;

-- -----------------------------------------------------------------------------
-- 3. Tables
-- -----------------------------------------------------------------------------
CREATE TABLE media_usage (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scope_kind      text NOT NULL CHECK (scope_kind IN ('user', 'organization', 'platform')),
  user_id         uuid REFERENCES users(id) ON DELETE RESTRICT,
  organization_id uuid REFERENCES organizations(id) ON DELETE RESTRICT,
  -- Seul le QUOTA est stocké : l'occupation et les réservations sont DÉRIVÉES de media_assets
  -- (nl_media_scope_usage). Aucun compteur modifiable, donc aucun compteur falsifiable ni négatif.
  quota_bytes     bigint NOT NULL CHECK (quota_bytes >= 0),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_media_usage_scope CHECK (
    (scope_kind = 'user'         AND user_id IS NOT NULL AND organization_id IS NULL) OR
    (scope_kind = 'organization' AND organization_id IS NOT NULL AND user_id IS NULL) OR
    (scope_kind = 'platform'     AND user_id IS NULL AND organization_id IS NULL))
);
CREATE UNIQUE INDEX ux_media_usage_user ON media_usage (user_id) WHERE scope_kind = 'user';
CREATE UNIQUE INDEX ux_media_usage_org ON media_usage (organization_id) WHERE scope_kind = 'organization';
CREATE UNIQUE INDEX ux_media_usage_platform ON media_usage (scope_kind) WHERE scope_kind = 'platform';

CREATE TABLE media_assets (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id        uuid REFERENCES organizations(id) ON DELETE RESTRICT,   -- NULL = contenu public
  uploaded_by            uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  kind                   text NOT NULL CHECK (kind IN ('image', 'video', 'captions', 'document')),
  mime_type              text NOT NULL,
  declared_bytes         bigint NOT NULL CHECK (declared_bytes > 0),
  byte_size              bigint CHECK (byte_size > 0),
  sha256                 char(64) CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  state                  text NOT NULL DEFAULT 'pending_upload' CHECK (state IN
                           ('pending_upload', 'quarantined', 'scanning', 'clean', 'infected', 'scan_failed', 'revoked')),
  scan_attempt           integer NOT NULL DEFAULT 0 CHECK (scan_attempt >= 0),
  quarantine_key         text CHECK (quarantine_key ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}/[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'),
  clean_key              text CHECK (clean_key ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}/[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'),
  clean_at               timestamptz,
  access_epoch           integer NOT NULL DEFAULT 0 CHECK (access_epoch >= 0),
  revoked_reason         text CHECK (revoked_reason IS NULL OR length(revoked_reason) BETWEEN 5 AND 1000),
  legal_hold             boolean NOT NULL DEFAULT false,
  legal_hold_reason      text CHECK (legal_hold_reason IS NULL OR length(legal_hold_reason) BETWEEN 5 AND 1000),
  display_name           text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 200 AND display_name !~ '[[:cntrl:]/\\]'),
  license                text NOT NULL CHECK (length(license) BETWEEN 2 AND 200),
  attribution            text CHECK (attribution IS NULL OR length(attribution) <= 500),
  alt_text_i18n          jsonb,
  captions_for           uuid REFERENCES media_assets(id) ON DELETE RESTRICT,
  reservation_expires_at timestamptz,
  deleted_at             timestamptz,
  deleted_reason         text CHECK (deleted_reason IS NULL OR deleted_reason IN ('user_request', 'reservation_expired', 'upload_aborted')),
  purged_at              timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_media_assets_mime CHECK (
    (kind = 'image'    AND mime_type IN ('image/jpeg', 'image/png', 'image/webp')) OR
    (kind = 'video'    AND mime_type = 'video/mp4') OR
    (kind = 'captions' AND mime_type = 'text/vtt') OR
    (kind = 'document' AND mime_type = 'application/pdf')),
  CONSTRAINT ck_media_assets_max_size CHECK (declared_bytes <= nl_media_max_bytes(kind)),
  CONSTRAINT ck_media_assets_size CHECK (byte_size IS NULL OR byte_size <= declared_bytes),
  CONSTRAINT ck_media_assets_alt CHECK (kind <> 'image' OR nl_has_base_locale(alt_text_i18n)),
  CONSTRAINT ck_media_assets_captions CHECK ((kind = 'captions') = (captions_for IS NOT NULL)),
  CONSTRAINT ck_media_assets_pending CHECK (state <> 'pending_upload' OR
    (byte_size IS NULL AND sha256 IS NULL AND quarantine_key IS NULL AND clean_key IS NULL AND reservation_expires_at IS NOT NULL)),
  CONSTRAINT ck_media_assets_received CHECK (state = 'pending_upload' OR
    (byte_size IS NOT NULL AND sha256 IS NOT NULL AND quarantine_key IS NOT NULL)),
  CONSTRAINT ck_media_assets_clean CHECK (state <> 'clean' OR (clean_key IS NOT NULL AND clean_at IS NOT NULL)),
  CONSTRAINT ck_media_assets_revoked CHECK ((state = 'revoked') = (revoked_reason IS NOT NULL)),
  CONSTRAINT ck_media_assets_hold CHECK (legal_hold = (legal_hold_reason IS NOT NULL)),
  CONSTRAINT ck_media_assets_deleted CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL)),
  CONSTRAINT ck_media_assets_purged CHECK (purged_at IS NULL OR (state <> 'pending_upload'
    AND (deleted_at IS NOT NULL OR state IN ('infected', 'scan_failed', 'revoked'))))
);
CREATE INDEX ix_media_assets_org ON media_assets (organization_id);
CREATE INDEX ix_media_assets_uploader ON media_assets (uploaded_by);
CREATE INDEX ix_media_assets_state ON media_assets (state) WHERE deleted_at IS NULL;
CREATE INDEX ix_media_assets_reservations ON media_assets (reservation_expires_at)
  WHERE state = 'pending_upload' AND deleted_at IS NULL;
CREATE INDEX ix_media_assets_captions_for ON media_assets (captions_for) WHERE captions_for IS NOT NULL;

-- Journal des analyses : ajout seul, jamais modifié ni supprimé.
CREATE TABLE media_scan_events (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  media_id                uuid NOT NULL REFERENCES media_assets(id) ON DELETE RESTRICT,
  attempt                 integer NOT NULL CHECK (attempt >= 1),
  result                  text NOT NULL CHECK (result IN ('clean', 'infected', 'error', 'timeout')),
  engine                  text NOT NULL CHECK (engine = 'clamav'),
  engine_version          text CHECK (engine_version IS NULL OR length(engine_version) <= 100),
  signature_version       text CHECK (signature_version IS NULL OR length(signature_version) <= 100),
  signatures_published_at timestamptz,
  threat_name             text CHECK (threat_name IS NULL OR length(threat_name) <= 200),
  sha256                  char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  duration_ms             integer CHECK (duration_ms IS NULL OR duration_ms >= 0),
  scanned_by              uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  scanned_at              timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_media_scan_threat CHECK ((result = 'infected') = (threat_name IS NOT NULL)),
  -- Verdict « sain » : moteur et signatures identifiés, signatures de moins de 48 h (S9).
  CONSTRAINT ck_media_scan_clean_traceable CHECK (result <> 'clean' OR (
    engine_version IS NOT NULL AND signature_version IS NOT NULL AND signatures_published_at IS NOT NULL
    AND signatures_published_at >= scanned_at - interval '48 hours'
    AND signatures_published_at <= scanned_at + interval '5 minutes'))
);
CREATE INDEX ix_media_scan_events_media ON media_scan_events (media_id, scanned_at);

-- Rattachement d'un média à une version de leçon (brouillon seulement ; figé ensuite).
CREATE TABLE lesson_version_media (
  lesson_version_id uuid NOT NULL REFERENCES lesson_versions(id) ON DELETE CASCADE,  -- brouillon seulement (garde)
  media_id          uuid NOT NULL REFERENCES media_assets(id) ON DELETE RESTRICT,
  attached_by       uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  attached_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (lesson_version_id, media_id)
);
CREATE INDEX ix_lesson_version_media_media ON lesson_version_media (media_id);

-- -----------------------------------------------------------------------------
-- 4. Quotas (S10) : occupation DÉRIVÉE, réservation atomique sous verrou, sans dépassement
-- -----------------------------------------------------------------------------
-- Aucune autorisation ne repose sur pg_trigger_depth() ni sur un drapeau de session : il n'existe
-- pas de compteur stocké à protéger. L'occupation d'une portée est calculée à partir des médias,
-- dont chaque changement est lui-même contrôlé (acteur déclaré, permissions, transitions).

-- Ligne de quota d'une portée (créée à la première utilisation, au quota par défaut).
CREATE OR REPLACE FUNCTION nl_media_usage_row(p_scope text, p_user uuid, p_org uuid) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO media_usage (scope_kind, user_id, organization_id, quota_bytes)
  VALUES (p_scope, CASE WHEN p_scope = 'user' THEN p_user END,
          CASE WHEN p_scope = 'organization' THEN p_org END, nl_media_default_quota(p_scope))
  ON CONFLICT DO NOTHING;
  SELECT id INTO v_id FROM media_usage
   WHERE scope_kind = p_scope
     AND user_id IS NOT DISTINCT FROM CASE WHEN p_scope = 'user' THEN p_user END
     AND organization_id IS NOT DISTINCT FROM CASE WHEN p_scope = 'organization' THEN p_org END;
  RETURN v_id;
END $$;

-- Occupation et réservations d'une portée, en octets.
--   occupé  = tailles reçues des médias non purgés (supprimés logiquement compris, jusqu'à la purge) ;
--   réservé = tailles déclarées des réservations en cours (non supprimées, non échues).
CREATE OR REPLACE FUNCTION nl_media_scope_usage(p_scope text, p_user uuid, p_org uuid,
                                                OUT used_bytes bigint, OUT reserved_bytes bigint)
LANGUAGE sql VOLATILE AS $$
  SELECT coalesce(sum(byte_size) FILTER (WHERE state <> 'pending_upload' AND purged_at IS NULL), 0)::bigint,
         coalesce(sum(declared_bytes) FILTER (WHERE state = 'pending_upload' AND deleted_at IS NULL
                                                AND reservation_expires_at > now()), 0)::bigint
    FROM media_assets
   WHERE (p_scope = 'user' AND uploaded_by = p_user)
      OR (p_scope = 'organization' AND organization_id = p_org)
      OR (p_scope = 'platform' AND organization_id IS NULL)
$$;

-- Réserve p_bytes dans une portée : verrou de ligne sur le quota (sérialise les demandes de la
-- portée), PUIS calcul de l'occupation (nouvel instantané pris après le verrou, READ COMMITTED) :
-- deux réservations concurrentes ne peuvent pas se fonder sur le même état.
CREATE OR REPLACE FUNCTION nl_media_reserve(p_scope text, p_user uuid, p_org uuid, p_bytes bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE v_row uuid; v_quota bigint; v_used bigint; v_reserved bigint;
BEGIN
  v_row := nl_media_usage_row(p_scope, p_user, p_org);
  SELECT quota_bytes INTO v_quota FROM media_usage WHERE id = v_row FOR UPDATE;
  IF v_quota IS NULL THEN
    RAISE EXCEPTION 'Ligne de quota introuvable (portée %)', p_scope USING ERRCODE = 'check_violation', HINT = 'nl:quota_exceeded';
  END IF;
  SELECT u.used_bytes, u.reserved_bytes INTO v_used, v_reserved FROM nl_media_scope_usage(p_scope, p_user, p_org) u;
  IF v_used + v_reserved + p_bytes > v_quota THEN
    RAISE EXCEPTION 'Quota de stockage dépassé (portée %) : % octets occupés ou réservés + % demandés > quota de % octets',
      p_scope, v_used + v_reserved, p_bytes, v_quota
      USING ERRCODE = 'check_violation', HINT = 'nl:quota_exceeded';
  END IF;
END $$;

-- Garde des lignes de quota : création au quota par défaut seulement ; quota modifiable par
-- l'administration de la plateforme (user:manage global), jamais sous l'occupation, toujours audité.
CREATE OR REPLACE FUNCTION nl_media_usage_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_actor uuid; v_used bigint; v_reserved bigint;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Suppression d''une ligne de quota interdite' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.quota_bytes <> nl_media_default_quota(NEW.scope_kind) THEN
      RAISE EXCEPTION 'Une ligne de quota naît au quota par défaut (1, 5 ou 20 Gio)'
        USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
    END IF;
    NEW.created_at := now(); NEW.updated_at := now();
    RETURN NEW;
  END IF;
  IF NEW.id <> OLD.id OR NEW.scope_kind IS DISTINCT FROM OLD.scope_kind OR NEW.user_id IS DISTINCT FROM OLD.user_id
     OR NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'Portée d''un quota immuable' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:immutable';
  END IF;
  IF NEW.quota_bytes <> OLD.quota_bytes THEN
    v_actor := nl_actor();
    IF NOT nl_user_has_permission(v_actor, 'user:manage', NULL) THEN
      RAISE EXCEPTION 'Modification de quota réservée à l''administration de la plateforme'
        USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
    END IF;
    -- La ligne est verrouillée par cet UPDATE : aucune réservation de la portée ne peut s'intercaler.
    SELECT u.used_bytes, u.reserved_bytes INTO v_used, v_reserved
      FROM nl_media_scope_usage(NEW.scope_kind, NEW.user_id, NEW.organization_id) u;
    IF NEW.quota_bytes < v_used + v_reserved THEN
      RAISE EXCEPTION 'Quota inférieur à l''occupation actuelle (% octets occupés ou réservés)', v_used + v_reserved
        USING ERRCODE = 'check_violation', HINT = 'nl:quota_below_usage';
    END IF;
    INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, organization_id, metadata)
    VALUES (v_actor, 'media.quota_changed', 'media_usage', NEW.id::text, NEW.organization_id,
            jsonb_build_object('scope', NEW.scope_kind, 'old_quota_bytes', OLD.quota_bytes,
                               'new_quota_bytes', NEW.quota_bytes));
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_media_usage_guard BEFORE INSERT OR UPDATE OR DELETE ON media_usage
  FOR EACH ROW EXECUTE FUNCTION nl_media_usage_guard();
CREATE TRIGGER trg_media_usage_updated_at BEFORE UPDATE ON media_usage
  FOR EACH ROW EXECUTE FUNCTION nl_set_updated_at();
CREATE TRIGGER trg_media_usage_no_truncate BEFORE TRUNCATE ON media_usage
  FOR EACH STATEMENT EXECUTE FUNCTION nl_forbid_modification();

-- Lecture des quotas (octets) : quota, occupé, réservé, disponible, par portée.
CREATE VIEW media_usage_totals AS
SELECT q.id, q.scope_kind, q.user_id, q.organization_id, q.quota_bytes, u.used_bytes, u.reserved_bytes,
       q.quota_bytes - u.used_bytes - u.reserved_bytes AS available_bytes
  FROM media_usage q
  CROSS JOIN LATERAL nl_media_scope_usage(q.scope_kind, q.user_id, q.organization_id) u;

-- -----------------------------------------------------------------------------
-- 5. Protections d'un média (références, signalements, conservation légale)
-- -----------------------------------------------------------------------------
-- Rattaché à une version de leçon (quelle qu'elle soit) ?
CREATE OR REPLACE FUNCTION nl_media_is_attached(p_media uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM lesson_version_media WHERE media_id = p_media)
$$;

-- Rattaché à une version PROTÉGÉE (soumise, approuvée, publiée ou archivée) ?
CREATE OR REPLACE FUNCTION nl_media_is_protected(p_media uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM lesson_version_media x JOIN lesson_versions lv ON lv.id = x.lesson_version_id
                  WHERE x.media_id = p_media AND lv.status <> 'draft')
$$;

-- Lié à un signalement de contenu (sur une version de leçon qui le cite) ?
CREATE OR REPLACE FUNCTION nl_media_is_reported(p_media uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM lesson_version_media x JOIN content_issue_reports r ON r.lesson_version_id = x.lesson_version_id
                  WHERE x.media_id = p_media)
$$;

-- -----------------------------------------------------------------------------
-- 6. Cycle de vie d'un média : création (réservation), transitions, suppression, purge
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_media_assets_before() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_actor uuid;
  v_last  media_scan_events%ROWTYPE;
  v_target media_assets%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Suppression physique d''un média interdite : suppression logique puis purge auditée'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:history';
  END IF;

  -- ---------------------------------------------------------------- création = réservation
  IF TG_OP = 'INSERT' THEN
    v_actor := nl_actor();
    IF NEW.uploaded_by IS DISTINCT FROM v_actor THEN
      RAISE EXCEPTION 'Le déposant d''un média est l''acteur de la session' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
    END IF;
    IF NOT nl_user_has_permission(v_actor, 'content:create', NEW.organization_id) THEN
      RAISE EXCEPTION 'Dépôt de média refusé : content:create requis dans cette portée'
        USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
    END IF;
    IF NEW.state <> 'pending_upload' OR NEW.scan_attempt <> 0 OR NEW.access_epoch <> 0 OR NEW.legal_hold
       OR NEW.clean_at IS NOT NULL OR NEW.deleted_at IS NOT NULL OR NEW.purged_at IS NOT NULL THEN
      RAISE EXCEPTION 'Un média naît en attente de téléversement' USING ERRCODE = 'check_violation', HINT = 'nl:born_pending';
    END IF;
    IF NEW.reservation_expires_at <= now() OR NEW.reservation_expires_at > now() + interval '24 hours' THEN
      RAISE EXCEPTION 'Échéance de réservation invalide (future, 24 h au plus)' USING ERRCODE = 'check_violation', HINT = 'nl:invalid_reservation';
    END IF;
    IF NEW.captions_for IS NOT NULL THEN
      SELECT * INTO v_target FROM media_assets WHERE id = NEW.captions_for;
      IF v_target.kind <> 'video' OR v_target.organization_id IS DISTINCT FROM NEW.organization_id
         OR v_target.deleted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Sous-titres : vidéo cible incohérente (type, établissement ou supprimée)'
          USING ERRCODE = 'check_violation', HINT = 'nl:incoherent_reference';
      END IF;
    END IF;
    -- Réservation : utilisateur, puis établissement ou plateforme (ordre de verrouillage constant).
    PERFORM nl_media_reserve('user', NEW.uploaded_by, NULL, NEW.declared_bytes);
    IF NEW.organization_id IS NULL THEN
      PERFORM nl_media_reserve('platform', NULL, NULL, NEW.declared_bytes);
    ELSE
      PERFORM nl_media_reserve('organization', NULL, NEW.organization_id, NEW.declared_bytes);
    END IF;
    NEW.created_at := now(); NEW.updated_at := now();
    RETURN NEW;
  END IF;

  -- ---------------------------------------------------------------- mise à jour
  IF OLD.purged_at IS NOT NULL THEN
    RAISE EXCEPTION 'Média purgé : plus aucune modification' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF NEW.id <> OLD.id OR NEW.organization_id IS DISTINCT FROM OLD.organization_id OR NEW.uploaded_by <> OLD.uploaded_by
     OR NEW.kind <> OLD.kind OR NEW.mime_type <> OLD.mime_type OR NEW.declared_bytes <> OLD.declared_bytes
     OR NEW.captions_for IS DISTINCT FROM OLD.captions_for OR NEW.created_at <> OLD.created_at
     OR NEW.display_name <> OLD.display_name OR NEW.license <> OLD.license
     OR NEW.attribution IS DISTINCT FROM OLD.attribution OR NEW.alt_text_i18n IS DISTINCT FROM OLD.alt_text_i18n
     OR NEW.reservation_expires_at IS DISTINCT FROM OLD.reservation_expires_at THEN
    RAISE EXCEPTION 'Identité, portée et métadonnées d''un média immuables' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF OLD.state <> 'pending_upload' AND (NEW.byte_size IS DISTINCT FROM OLD.byte_size OR NEW.sha256 IS DISTINCT FROM OLD.sha256
     OR NEW.quarantine_key IS DISTINCT FROM OLD.quarantine_key) THEN
    RAISE EXCEPTION 'Contenu reçu d''un média immuable' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF NEW.clean_key IS DISTINCT FROM OLD.clean_key AND NOT (OLD.state = 'scanning' AND NEW.state = 'clean' AND OLD.clean_key IS NULL) THEN
    RAISE EXCEPTION 'Clé saine posée uniquement au passage à « clean »' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF NEW.access_epoch < OLD.access_epoch THEN
    RAISE EXCEPTION 'Époque d''accès décroissante interdite' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF NEW.scan_attempt <> OLD.scan_attempt AND NOT (NEW.state = 'scanning' AND OLD.state <> 'scanning'
                                                   AND NEW.scan_attempt = OLD.scan_attempt + 1) THEN
    RAISE EXCEPTION 'Numéro de tentative d''analyse incrémenté seulement à l''entrée en analyse'
      USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF OLD.deleted_at IS NOT NULL AND (NEW.deleted_at IS DISTINCT FROM OLD.deleted_at OR NEW.deleted_reason IS DISTINCT FROM OLD.deleted_reason) THEN
    RAISE EXCEPTION 'Suppression logique irréversible' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;

  -- ---------------------------------------------------------------- conservation légale (S13)
  IF NEW.legal_hold IS DISTINCT FROM OLD.legal_hold OR NEW.legal_hold_reason IS DISTINCT FROM OLD.legal_hold_reason THEN
    v_actor := nl_actor();
    IF NOT nl_user_has_permission(v_actor, 'user:manage', NULL) THEN
      RAISE EXCEPTION 'Conservation légale réservée à l''administration de la plateforme'
        USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
    END IF;
    INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, organization_id, metadata)
    VALUES (v_actor, CASE WHEN NEW.legal_hold THEN 'media.legal_hold_set' ELSE 'media.legal_hold_released' END,
            'media_asset', NEW.id::text, NEW.organization_id, jsonb_build_object('legal_hold', NEW.legal_hold));
  END IF;

  -- ---------------------------------------------------------------- suppression logique
  IF NEW.deleted_at IS NOT NULL AND OLD.deleted_at IS NULL THEN
    IF NEW.deleted_reason = 'reservation_expired' THEN
      IF OLD.state <> 'pending_upload' OR OLD.reservation_expires_at > now() THEN
        RAISE EXCEPTION 'Expiration d''une réservation non échue refusée' USING ERRCODE = 'check_violation', HINT = 'nl:invalid_reservation';
      END IF;
    ELSE
      v_actor := nl_actor();
      IF v_actor <> OLD.uploaded_by AND NOT nl_user_has_permission(v_actor, 'content:create', OLD.organization_id) THEN
        RAISE EXCEPTION 'Suppression d''un média refusée hors de sa portée' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
      END IF;
      IF NEW.deleted_reason = 'upload_aborted' AND OLD.state <> 'pending_upload' THEN
        RAISE EXCEPTION 'Abandon de téléversement possible seulement avant réception' USING ERRCODE = 'check_violation', HINT = 'nl:transition_forbidden';
      END IF;
    END IF;
    IF OLD.legal_hold OR NEW.legal_hold THEN
      RAISE EXCEPTION 'Média sous conservation légale : suppression interdite' USING ERRCODE = 'check_violation', HINT = 'nl:legal_hold';
    END IF;
    IF nl_media_is_reported(OLD.id) THEN
      RAISE EXCEPTION 'Média lié à un signalement de contenu : suppression interdite' USING ERRCODE = 'check_violation', HINT = 'nl:reported';
    END IF;
    IF nl_media_is_attached(OLD.id) THEN
      RAISE EXCEPTION 'Média rattaché à une version de leçon : suppression interdite (le détacher d''abord, brouillon seulement)'
        USING ERRCODE = 'check_violation', HINT = 'nl:in_use';
    END IF;
    IF EXISTS (SELECT 1 FROM media_assets c WHERE c.captions_for = OLD.id AND c.deleted_at IS NULL) THEN
      RAISE EXCEPTION 'Vidéo dont les sous-titres existent encore : suppression interdite' USING ERRCODE = 'check_violation', HINT = 'nl:in_use';
    END IF;
    NEW.access_epoch := OLD.access_epoch + 1;   -- tout jeton existant devient invalide
  END IF;

  -- ---------------------------------------------------------------- purge (objet effacé, ligne conservée)
  IF NEW.purged_at IS NOT NULL AND OLD.purged_at IS NULL THEN
    v_actor := nl_actor();
    IF NOT (nl_user_has_permission(v_actor, 'media:scan', NULL) OR nl_user_has_permission(v_actor, 'user:manage', NULL)) THEN
      RAISE EXCEPTION 'Purge réservée au service des médias ou à l''administration' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
    END IF;
    IF OLD.legal_hold OR NEW.legal_hold THEN
      RAISE EXCEPTION 'Média sous conservation légale : purge interdite' USING ERRCODE = 'check_violation', HINT = 'nl:legal_hold';
    END IF;
    IF nl_media_is_reported(OLD.id) THEN
      RAISE EXCEPTION 'Média lié à un signalement de contenu : purge interdite' USING ERRCODE = 'check_violation', HINT = 'nl:reported';
    END IF;
    IF nl_media_is_attached(OLD.id) THEN
      RAISE EXCEPTION 'Média rattaché à une version de leçon : purge interdite' USING ERRCODE = 'check_violation', HINT = 'nl:in_use';
    END IF;
    NEW.access_epoch := greatest(NEW.access_epoch, OLD.access_epoch + 1);
  END IF;

  -- ---------------------------------------------------------------- transitions d'état
  IF NEW.state <> OLD.state THEN
    IF OLD.deleted_at IS NOT NULL AND NOT (NEW.state = 'revoked') THEN
      RAISE EXCEPTION 'Média supprimé : aucune transition (hors révocation)' USING ERRCODE = 'check_violation', HINT = 'nl:transition_forbidden';
    END IF;
    v_actor := nl_actor();
    IF OLD.state = 'pending_upload' AND NEW.state = 'quarantined' THEN
      -- Réception du fichier par le déposant (route de téléversement, 2.2.3-b).
      IF v_actor <> OLD.uploaded_by OR NOT nl_user_has_permission(v_actor, 'content:create', OLD.organization_id) THEN
        RAISE EXCEPTION 'Réception d''un média réservée à son déposant' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
      END IF;
      IF OLD.reservation_expires_at <= now() THEN
        RAISE EXCEPTION 'Réservation expirée : téléversement à recommencer' USING ERRCODE = 'check_violation', HINT = 'nl:invalid_reservation';
      END IF;
    ELSIF (OLD.state, NEW.state) IN (('quarantined', 'scanning'), ('scan_failed', 'scanning'), ('infected', 'scanning'),
                                     ('scanning', 'quarantined'), ('scanning', 'clean'), ('scanning', 'infected'),
                                     ('scanning', 'scan_failed'), ('clean', 'infected')) THEN
      -- Analyse : identité de service détenant media:scan (attribuée à aucun rôle avant 2.2.3-c).
      IF NOT nl_user_has_permission(v_actor, 'media:scan', NULL) THEN
        RAISE EXCEPTION 'Transition d''analyse réservée au service antivirus (media:scan)'
          USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
      END IF;
      IF NEW.state = 'scanning' AND NEW.scan_attempt <> OLD.scan_attempt + 1 THEN
        RAISE EXCEPTION 'Entrée en analyse : tentative suivante obligatoire' USING ERRCODE = 'check_violation', HINT = 'nl:transition_forbidden';
      END IF;
      IF NEW.state IN ('clean', 'infected', 'scan_failed') THEN
        SELECT * INTO v_last FROM media_scan_events WHERE media_id = OLD.id ORDER BY scanned_at DESC, id DESC LIMIT 1;
        IF v_last.id IS NULL OR v_last.attempt <> OLD.scan_attempt
           OR (NEW.state = 'clean' AND v_last.result <> 'clean')
           OR (NEW.state = 'infected' AND v_last.result <> 'infected')
           OR (NEW.state = 'scan_failed' AND v_last.result NOT IN ('error', 'timeout')) THEN
          RAISE EXCEPTION 'Verdict sans analyse traçable correspondante (media_scan_events)'
            USING ERRCODE = 'check_violation', HINT = 'nl:untraceable_verdict';
        END IF;
        IF NEW.state = 'clean' AND (v_last.sha256 <> OLD.sha256 OR v_last.signatures_published_at < now() - interval '48 hours') THEN
          RAISE EXCEPTION 'Verdict « sain » refusé : empreinte différente ou signatures périmées'
            USING ERRCODE = 'check_violation', HINT = 'nl:untraceable_verdict';
        END IF;
      END IF;
      IF NEW.state = 'clean' THEN
        NEW.clean_at := now();
      ELSIF OLD.state = 'clean' THEN
        NEW.access_epoch := greatest(NEW.access_epoch, OLD.access_epoch + 1);   -- détection a posteriori
      END IF;
    ELSIF NEW.state = 'revoked' AND OLD.state <> 'pending_upload' THEN
      IF NOT (nl_user_has_permission(v_actor, 'content:review', NULL) OR nl_user_has_permission(v_actor, 'user:manage', NULL)) THEN
        RAISE EXCEPTION 'Révocation réservée aux relecteurs plateforme et à l''administration'
          USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
      END IF;
      NEW.access_epoch := greatest(NEW.access_epoch, OLD.access_epoch + 1);
    ELSE
      RAISE EXCEPTION 'Transition de média interdite : % → %', OLD.state, NEW.state
        USING ERRCODE = 'check_violation', HINT = 'nl:transition_forbidden';
    END IF;
    IF OLD.state = 'clean' AND NEW.state NOT IN ('infected', 'revoked') THEN
      RAISE EXCEPTION 'Un média sain ne quitte cet état que par détection ou révocation'
        USING ERRCODE = 'check_violation', HINT = 'nl:transition_forbidden';
    END IF;
  ELSIF NEW.clean_at IS DISTINCT FROM OLD.clean_at THEN
    RAISE EXCEPTION 'Date de validation antivirus non modifiable' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF NEW.revoked_reason IS DISTINCT FROM OLD.revoked_reason AND NOT (NEW.state = 'revoked' AND OLD.state <> 'revoked') THEN
    RAISE EXCEPTION 'Motif de révocation immuable' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;

  NEW.updated_at := now();
  RETURN NEW;
END $$;

CREATE TRIGGER trg_media_assets_10_guard BEFORE INSERT OR UPDATE OR DELETE ON media_assets
  FOR EACH ROW EXECUTE FUNCTION nl_media_assets_before();
CREATE TRIGGER trg_media_assets_updated_at BEFORE UPDATE ON media_assets
  FOR EACH ROW EXECUTE FUNCTION nl_set_updated_at();
CREATE TRIGGER trg_media_assets_no_truncate BEFORE TRUNCATE ON media_assets
  FOR EACH STATEMENT EXECUTE FUNCTION nl_forbid_modification();

-- Expiration explicite des réservations échues (appelée par une tâche planifiée en 2.2.3-c).
CREATE OR REPLACE FUNCTION nl_media_expire_reservations() RETURNS integer LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
  UPDATE media_assets SET deleted_at = now(), deleted_reason = 'reservation_expired'
   WHERE state = 'pending_upload' AND deleted_at IS NULL AND reservation_expires_at <= now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- -----------------------------------------------------------------------------
-- 7. Journal des analyses : ajout seul, enregistré par le service antivirus uniquement
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_media_scan_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_actor uuid; v_media media_assets%ROWTYPE;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'Journal des analyses en ajout seul' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:history';
  END IF;
  v_actor := nl_actor();
  IF NOT nl_user_has_permission(v_actor, 'media:scan', NULL) THEN
    RAISE EXCEPTION 'Verdict antivirus réservé au service d''analyse (media:scan)'
      USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  SELECT * INTO v_media FROM media_assets WHERE id = NEW.media_id FOR UPDATE;
  IF v_media.state NOT IN ('scanning', 'clean') OR v_media.purged_at IS NOT NULL THEN
    RAISE EXCEPTION 'Verdict hors d''une analyse en cours ou d''une nouvelle analyse' USING ERRCODE = 'check_violation', HINT = 'nl:transition_forbidden';
  END IF;
  IF NEW.attempt <> v_media.scan_attempt THEN
    RAISE EXCEPTION 'Verdict d''une autre tentative' USING ERRCODE = 'check_violation', HINT = 'nl:untraceable_verdict';
  END IF;
  NEW.scanned_by := v_actor;
  NEW.scanned_at := now();
  RETURN NEW;
END $$;

CREATE TRIGGER trg_media_scan_events_guard BEFORE INSERT OR UPDATE OR DELETE ON media_scan_events
  FOR EACH ROW EXECUTE FUNCTION nl_media_scan_event_guard();
CREATE TRIGGER trg_media_scan_events_no_truncate BEFORE TRUNCATE ON media_scan_events
  FOR EACH STATEMENT EXECUTE FUNCTION nl_forbid_modification();

-- -----------------------------------------------------------------------------
-- 8. Rattachements : brouillon seulement, même portée, figés ensuite
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_lesson_version_media_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_actor  uuid;
  v_status text;
  v_org    uuid;
  v_media  media_assets%ROWTYPE;
  v_lv     uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.lesson_version_id ELSE NEW.lesson_version_id END;
BEGIN
  SELECT lv.status, nl_lesson_org(lv.lesson_id) INTO v_status, v_org FROM lesson_versions lv WHERE lv.id = v_lv;
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'Rattachement immuable : le retirer puis l''ajouter' USING ERRCODE = 'check_violation', HINT = 'nl:immutable';
  END IF;
  IF TG_OP = 'DELETE' THEN
    -- Suppression en cascade d'un brouillon (la version elle-même n'est supprimable qu'en brouillon).
    IF v_status IS NOT NULL AND v_status <> 'draft' THEN
      RAISE EXCEPTION 'Rattachements d''une version protégée immuables' USING ERRCODE = 'check_violation', HINT = 'nl:not_draft';
    END IF;
    IF v_status IS NOT NULL THEN
      v_actor := nl_actor();
      IF NOT nl_user_has_permission(v_actor, 'content:create', v_org) THEN
        RAISE EXCEPTION 'Détachement refusé : content:create requis dans cette portée' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
      END IF;
    END IF;
    RETURN OLD;
  END IF;
  IF v_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'Rattachement possible uniquement à un brouillon' USING ERRCODE = 'check_violation', HINT = 'nl:not_draft';
  END IF;
  v_actor := nl_actor();
  IF NOT nl_user_has_permission(v_actor, 'content:create', v_org) THEN
    RAISE EXCEPTION 'Rattachement refusé : content:create requis dans cette portée' USING ERRCODE = 'insufficient_privilege', HINT = 'nl:forbidden';
  END IF;
  SELECT * INTO v_media FROM media_assets WHERE id = NEW.media_id FOR SHARE;
  -- Isolation : média public (NULL) ou du même établissement que la leçon.
  IF v_media.organization_id IS NOT NULL AND v_media.organization_id IS DISTINCT FROM v_org THEN
    RAISE EXCEPTION 'Média d''un autre établissement' USING ERRCODE = 'check_violation', HINT = 'nl:organization_scope';
  END IF;
  IF v_media.deleted_at IS NOT NULL OR v_media.purged_at IS NOT NULL
     OR v_media.state IN ('pending_upload', 'infected', 'revoked') THEN
    RAISE EXCEPTION 'Média non rattachable (supprimé, non reçu, infecté ou révoqué)' USING ERRCODE = 'check_violation', HINT = 'nl:media_not_clean';
  END IF;
  NEW.attached_by := v_actor;
  NEW.attached_at := now();
  RETURN NEW;
END $$;

CREATE TRIGGER trg_lesson_version_media_guard BEFORE INSERT OR UPDATE OR DELETE ON lesson_version_media
  FOR EACH ROW EXECUTE FUNCTION nl_lesson_version_media_guard();

-- Soumission, approbation et publication : tout média rattaché est sain, non supprimé ;
-- toute vidéo rattachée a des sous-titres sains (WCAG 1.2.2). Nouveau déclencheur : aucune
-- fonction de 0005/0006 n'est réécrite.
CREATE OR REPLACE FUNCTION nl_lesson_version_media_gate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status AND NEW.status IN ('medical_review', 'approved', 'published') THEN
    IF EXISTS (SELECT 1 FROM lesson_version_media x JOIN media_assets m ON m.id = x.media_id
                WHERE x.lesson_version_id = NEW.id
                  AND (m.state <> 'clean' OR m.deleted_at IS NOT NULL OR m.purged_at IS NOT NULL)) THEN
      RAISE EXCEPTION 'Média non validé par l''antivirus (ou supprimé) rattaché à la version'
        USING ERRCODE = 'check_violation', HINT = 'nl:media_not_clean';
    END IF;
    IF EXISTS (SELECT 1 FROM lesson_version_media x JOIN media_assets v ON v.id = x.media_id
                WHERE x.lesson_version_id = NEW.id AND v.kind = 'video'
                  AND NOT EXISTS (SELECT 1 FROM media_assets c WHERE c.captions_for = v.id AND c.state = 'clean'
                                    AND c.deleted_at IS NULL)) THEN
      RAISE EXCEPTION 'Vidéo sans sous-titres sains (WCAG 1.2.2)' USING ERRCODE = 'check_violation', HINT = 'nl:missing_captions';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_lesson_versions_30_media BEFORE UPDATE OF status ON lesson_versions
  FOR EACH ROW EXECUTE FUNCTION nl_lesson_version_media_gate();
