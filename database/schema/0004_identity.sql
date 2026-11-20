-- =============================================================================
-- Migration 0004 — Identité et accès (Phase 2.1, ADR-0007)
-- Additive : colonnes et tables nouvelles, déclencheurs de garde. Aucune donnée
-- existante n'est modifiée ; les migrations 0001 à 0003 restent inchangées.
-- =============================================================================

-- 1. Comptes : déclaration de majorité, consentements versionnés, verrouillage
ALTER TABLE users
  ADD COLUMN adult_attested_at      timestamptz,      -- déclaration « 18 ans et plus » (aucune pièce, aucune date de naissance)
  ADD COLUMN terms_version          text,
  ADD COLUMN privacy_policy_version text,
  ADD COLUMN privacy_accepted_at    timestamptz,
  ADD COLUMN password_changed_at    timestamptz,
  ADD COLUMN failed_login_count     integer NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
  ADD COLUMN locked_until           timestamptz;

-- 2. Sessions opaques côté serveur (rotation par famille, inactivité, révocation)
ALTER TABLE auth_sessions
  ADD COLUMN family_id             uuid NOT NULL DEFAULT gen_random_uuid(),
  ADD COLUMN replaced_by           uuid REFERENCES auth_sessions(id) ON DELETE SET NULL,
  ADD COLUMN rotated_at            timestamptz,
  ADD COLUMN last_seen_at          timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN last_authenticated_at timestamptz,      -- NULL : fraîcheur perdue pour les opérations sensibles
  ADD COLUMN revoked_reason        text CHECK (revoked_reason IN (
    'logout', 'logout_all', 'rotated', 'reuse_detected', 'password_reset', 'password_changed',
    'role_revoked', 'suspended', 'expired', 'admin'));
CREATE INDEX ix_auth_sessions_family ON auth_sessions (family_id);
COMMENT ON COLUMN auth_sessions.refresh_token_hash IS
  'Empreinte HMAC-SHA-256 de l''identifiant de session opaque ; l''identifiant n''est jamais stocké';

-- 3. Jetons à usage unique (vérification du courriel, réinitialisation du mot de passe)
CREATE TABLE one_time_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  purpose     text NOT NULL CHECK (purpose IN ('email_verification', 'password_reset')),
  token_hash  text NOT NULL UNIQUE,                 -- HMAC-SHA-256 ; jamais le jeton
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  used_at     timestamptz,
  CHECK (expires_at > created_at)
);
CREATE INDEX ix_one_time_tokens_user ON one_time_tokens (user_id, purpose) WHERE used_at IS NULL;

-- 4. Invitations nominatives (établissements et rôles de plateforme non publics)
CREATE TABLE invitations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email            citext NOT NULL CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  role_id          smallint NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
  organization_id  uuid REFERENCES organizations(id) ON DELETE CASCADE,
  invited_by       uuid REFERENCES users(id) ON DELETE SET NULL,
  token_hash       text NOT NULL UNIQUE,            -- HMAC-SHA-256 ; jamais le jeton
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL,
  accepted_at      timestamptz,
  accepted_by      uuid REFERENCES users(id) ON DELETE SET NULL,
  revoked_at       timestamptz,
  revoked_by       uuid REFERENCES users(id) ON DELETE SET NULL,
  CHECK (expires_at > created_at),
  CHECK (accepted_at IS NULL OR revoked_at IS NULL),
  CHECK ((accepted_at IS NULL) = (accepted_by IS NULL))
);
-- Une seule invitation en attente par personne, rôle et établissement.
CREATE UNIQUE INDEX ux_invitations_pending ON invitations (email, role_id, organization_id) NULLS NOT DISTINCT
  WHERE accepted_at IS NULL AND revoked_at IS NULL;
CREATE INDEX ix_invitations_org ON invitations (organization_id, created_at DESC);

-- 5. Garde d'attribution des rôles (défense en profondeur, ADR-0007 §A)
-- Seul « student » s'obtient sans émetteur. Les autres rôles exigent un émetteur
-- distinct du bénéficiaire, détenant « user:manage » (administrateur de la plateforme),
-- ou « org:manage » dans le même établissement pour un rôle « instructor » d'établissement.
-- Contournement réservé à l'exploitation (commande locale, données de test) :
--   SELECT set_config('nl.role_grant_bypass', 'on', true);   -- limité à la transaction
CREATE OR REPLACE FUNCTION nl_role_grant_allowed(p_granter uuid, p_beneficiary uuid, p_role_code text, p_org uuid)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT p_granter IS NOT NULL
     AND p_granter IS DISTINCT FROM p_beneficiary
     AND (
       nl_user_has_permission(p_granter, 'user:manage')
       OR (p_role_code = 'instructor' AND p_org IS NOT NULL
           AND nl_user_has_permission(p_granter, 'org:manage', p_org))
     )
$$;

CREATE OR REPLACE FUNCTION nl_guard_role_grant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_code text;
BEGIN
  SELECT code INTO v_code FROM roles WHERE id = NEW.role_id;
  IF v_code = 'student' OR current_setting('nl.role_grant_bypass', true) = 'on' THEN
    RETURN NEW;
  END IF;
  IF NOT nl_role_grant_allowed(NEW.granted_by, NEW.user_id, v_code, NEW.organization_id) THEN
    RAISE EXCEPTION 'Attribution du rôle « % » refusée : émetteur absent ou non habilité', v_code
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_user_roles_guard BEFORE INSERT OR UPDATE OF user_id, role_id, organization_id, granted_by
  ON user_roles FOR EACH ROW EXECUTE FUNCTION nl_guard_role_grant();

-- 6. Invitations : rôle invitable et émetteur habilité
CREATE OR REPLACE FUNCTION nl_check_invitation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_code text;
BEGIN
  SELECT code INTO v_code FROM roles WHERE id = NEW.role_id;
  IF v_code IN ('student', 'admin') THEN
    RAISE EXCEPTION 'Le rôle « % » ne s''obtient pas par invitation', v_code USING ERRCODE = 'check_violation';
  END IF;
  IF v_code = 'org_admin' AND NEW.organization_id IS NULL THEN
    RAISE EXCEPTION 'Une invitation d''administrateur d''établissement exige un établissement' USING ERRCODE = 'check_violation';
  END IF;
  IF v_code = 'medical_reviewer' AND NEW.organization_id IS NOT NULL THEN
    RAISE EXCEPTION 'Le rôle de relecteur médical est global' USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'INSERT' AND current_setting('nl.role_grant_bypass', true) IS DISTINCT FROM 'on'
     AND NOT nl_role_grant_allowed(NEW.invited_by, NULL, v_code, NEW.organization_id) THEN
    RAISE EXCEPTION 'Invitation refusée : émetteur non habilité pour le rôle « % »', v_code
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_invitations_check BEFORE INSERT OR UPDATE OF role_id, organization_id ON invitations
  FOR EACH ROW EXECUTE FUNCTION nl_check_invitation();
