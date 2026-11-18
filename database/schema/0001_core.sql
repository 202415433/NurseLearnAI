-- =============================================================================
-- NurseLearn AI — Schéma de base de données v1 (migration 0001)
-- Cible : PostgreSQL 16+
-- Conventions :
--   * Clés primaires UUID (gen_random_uuid) sauf tables de référence.
--   * Horodatages en timestamptz (UTC), colonnes created_at / updated_at.
--   * Textes traduisibles en JSONB {"fr": "...", "en": "..."} ; la clé "fr"
--     ou "en" doit exister (contrainte nl_has_base_locale).
--   * Énumérations en text + CHECK (plus simples à faire évoluer que ENUM).
--   * Stratégies de suppression documentées dans docs/04-modele-donnees.md.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid, digest
CREATE EXTENSION IF NOT EXISTS citext;     -- e-mails insensibles à la casse
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- recherche approximative

-- -----------------------------------------------------------------------------
-- Fonctions utilitaires
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nl_set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- Un texte i18n doit être un objet JSON contenant au moins "fr" ou "en" non vide.
CREATE OR REPLACE FUNCTION nl_has_base_locale(j jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_typeof(j) = 'object'
     AND (coalesce(length(j->>'fr'), 0) > 0 OR coalesce(length(j->>'en'), 0) > 0)
$$;

-- Journal d'audit en ajout seul : interdit UPDATE/DELETE.
CREATE OR REPLACE FUNCTION nl_forbid_modification() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Table % en ajout seul : % interdit', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

-- =============================================================================
-- 1. RÉFÉRENTIELS : langues, pays, juridictions
-- =============================================================================
CREATE TABLE locales (
  code        text PRIMARY KEY CHECK (code ~ '^[a-z]{2}(-[A-Z]{2})?$'),
  name        text NOT NULL,
  is_active   boolean NOT NULL DEFAULT true
);

CREATE TABLE countries (
  code        char(2) PRIMARY KEY CHECK (code ~ '^[A-Z]{2}$'),
  name_i18n   jsonb NOT NULL CHECK (nl_has_base_locale(name_i18n))
);

CREATE TABLE jurisdictions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code             text NOT NULL UNIQUE CHECK (code ~ '^[A-Z0-9-]{2,20}$'),
  country_code     char(2) REFERENCES countries(code) ON DELETE RESTRICT,  -- NULL = référentiel transnational
  parent_id        uuid REFERENCES jurisdictions(id) ON DELETE RESTRICT,
  kind             text NOT NULL CHECK (kind IN ('country','province','state','region','framework')),
  name_i18n        jsonb NOT NULL CHECK (nl_has_base_locale(name_i18n)),
  default_locale   text NOT NULL REFERENCES locales(code) ON DELETE RESTRICT,
  is_active        boolean NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (parent_id IS NULL OR parent_id <> id),
  CHECK (kind = 'framework' OR country_code IS NOT NULL)
);

-- =============================================================================
-- 2. IDENTITÉ, RÔLES, ORGANISATIONS
-- =============================================================================
CREATE TABLE organizations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug             text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name             text NOT NULL CHECK (length(name) BETWEEN 2 AND 200),
  kind             text NOT NULL CHECK (kind IN ('school','hospital','care_home','employer','other')),
  jurisdiction_id  uuid REFERENCES jurisdictions(id) ON DELETE RESTRICT,
  is_active        boolean NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz
);

CREATE TABLE users (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email              citext NOT NULL CHECK (email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
  password_hash      text,                         -- NULL si connexion fédérée uniquement
  status             text NOT NULL DEFAULT 'pending_verification'
                     CHECK (status IN ('pending_verification','active','suspended','deactivated','anonymized')),
  email_verified_at  timestamptz,
  mfa_enabled        boolean NOT NULL DEFAULT false,
  locale             text NOT NULL DEFAULT 'fr' REFERENCES locales(code) ON DELETE RESTRICT,
  last_login_at      timestamptz,
  terms_accepted_at  timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,
  CHECK (status <> 'active' OR email_verified_at IS NOT NULL)
);
-- Unicité de l'e-mail parmi les comptes non supprimés.
CREATE UNIQUE INDEX ux_users_email_live ON users (email) WHERE deleted_at IS NULL;

CREATE TABLE roles (
  id          smallint PRIMARY KEY,
  code        text NOT NULL UNIQUE CHECK (code ~ '^[a-z_]+$'),
  name_i18n   jsonb NOT NULL CHECK (nl_has_base_locale(name_i18n))
);

CREATE TABLE permissions (
  code        text PRIMARY KEY CHECK (code ~ '^[a-z_]+(:[a-z_]+)+$'),   -- ex. "content:publish"
  description text NOT NULL
);

CREATE TABLE role_permissions (
  role_id          smallint NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission_code  text NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_code)
);

-- Rôle global (organization_id NULL) ou propre à un établissement.
CREATE TABLE user_roles (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id          smallint NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
  organization_id  uuid REFERENCES organizations(id) ON DELETE CASCADE,
  granted_by       uuid REFERENCES users(id) ON DELETE SET NULL,
  granted_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (user_id, role_id, organization_id)
);
CREATE INDEX ix_user_roles_org ON user_roles (organization_id);

CREATE TABLE auth_sessions (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_token_hash  text NOT NULL UNIQUE,         -- SHA-256 du jeton, jamais le jeton
  created_at          timestamptz NOT NULL DEFAULT now(),
  expires_at          timestamptz NOT NULL,
  revoked_at          timestamptz,
  ip                  inet,
  user_agent          text,
  CHECK (expires_at > created_at)
);
CREATE INDEX ix_auth_sessions_user ON auth_sessions (user_id) WHERE revoked_at IS NULL;

-- =============================================================================
-- 3. MÉTIERS, SPÉCIALITÉS, PROFILS
-- =============================================================================
-- Un métier est TOUJOURS rattaché à une juridiction : aucun titre n'est
-- considéré équivalent à un autre par défaut (exigence §9).
CREATE TABLE professions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  jurisdiction_id       uuid NOT NULL REFERENCES jurisdictions(id) ON DELETE RESTRICT,
  code                  text NOT NULL CHECK (code ~ '^[a-z0-9_]+$'),
  title_i18n            jsonb NOT NULL CHECK (nl_has_base_locale(title_i18n)),
  category              text NOT NULL CHECK (category IN
                         ('care_assistant','practical_nurse','registered_nurse','other')),
  is_regulated          boolean NOT NULL,
  regulator_name        text,
  regulator_url         text CHECK (regulator_url IS NULL OR regulator_url ~ '^https://'),
  official_path_i18n    jsonb CHECK (official_path_i18n IS NULL OR nl_has_base_locale(official_path_i18n)),
  validation_status     text NOT NULL DEFAULT 'pending_expert_review'
                        CHECK (validation_status IN ('pending_expert_review','validated','needs_update')),
  validated_by          uuid REFERENCES users(id) ON DELETE RESTRICT,
  validated_at          timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (jurisdiction_id, code),
  CHECK (validation_status <> 'validated' OR (validated_by IS NOT NULL AND validated_at IS NOT NULL))
);

CREATE TABLE specialties (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code         text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]+$'),
  catalog_group char(1) NOT NULL CHECK (catalog_group BETWEEN 'A' AND 'H'),
  parent_id    uuid REFERENCES specialties(id) ON DELETE RESTRICT,
  name_i18n    jsonb NOT NULL CHECK (nl_has_base_locale(name_i18n)),
  sort_order   integer NOT NULL DEFAULT 0,
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (parent_id IS NULL OR parent_id <> id)
);

CREATE TABLE profiles (
  user_id               uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  display_name          text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
  avatar_key            text,
  jurisdiction_id       uuid REFERENCES jurisdictions(id) ON DELETE SET NULL,
  target_profession_id  uuid REFERENCES professions(id) ON DELETE SET NULL,
  timezone              text NOT NULL DEFAULT 'America/Toronto',
  experience_level      text NOT NULL DEFAULT 'none'
                        CHECK (experience_level IN ('none','student','working_in_care','licensed')),
  weekly_goal_minutes   integer NOT NULL DEFAULT 120 CHECK (weekly_goal_minutes BETWEEN 0 AND 3000),
  xp_total              integer NOT NULL DEFAULT 0 CHECK (xp_total >= 0),
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE instructors (
  user_id          uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  organization_id  uuid REFERENCES organizations(id) ON DELETE SET NULL,
  credentials      text,                 -- déclaratif, vérifié par un administrateur
  verified_by      uuid REFERENCES users(id) ON DELETE SET NULL,
  verified_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Groupes / classes d'un établissement
CREATE TABLE cohorts (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id  uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  name             text NOT NULL,
  instructor_id    uuid REFERENCES instructors(user_id) ON DELETE SET NULL,
  starts_on        date,
  ends_on          date,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on),
  UNIQUE (organization_id, name)
);

CREATE TABLE cohort_members (
  cohort_id   uuid NOT NULL REFERENCES cohorts(id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  joined_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (cohort_id, user_id)
);

-- =============================================================================
-- 4. RÉFÉRENCES MÉDICALES
-- =============================================================================
CREATE TABLE medical_references (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind             text NOT NULL CHECK (kind IN
                    ('guideline','regulation','textbook','article','official_website','standard')),
  title            text NOT NULL,
  authors          text,
  publisher        text,                          -- ex. OMS, INSPQ, HAS, ASPC
  publication_year smallint CHECK (publication_year BETWEEN 1900 AND 2100),
  url              text CHECK (url IS NULL OR url ~ '^https?://'),
  doi              text,
  isbn             text,
  jurisdiction_id  uuid REFERENCES jurisdictions(id) ON DELETE SET NULL,  -- NULL = portée générale
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','withdrawn')),
  superseded_by    uuid REFERENCES medical_references(id) ON DELETE SET NULL,
  last_verified_at date,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  -- Une référence doit être identifiable et vérifiable (§13).
  CHECK (url IS NOT NULL OR doi IS NOT NULL OR isbn IS NOT NULL),
  CHECK (status <> 'superseded' OR superseded_by IS NOT NULL)
);

-- =============================================================================
-- 5. CATALOGUE PÉDAGOGIQUE : programmes > modules (cours) > leçons
-- =============================================================================
CREATE TABLE programs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code             text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_-]+$'),
  profession_id    uuid NOT NULL REFERENCES professions(id) ON DELETE RESTRICT,
  specialty_id     uuid REFERENCES specialties(id) ON DELETE RESTRICT,
  organization_id  uuid REFERENCES organizations(id) ON DELETE CASCADE,   -- NULL = catalogue public
  title_i18n       jsonb NOT NULL CHECK (nl_has_base_locale(title_i18n)),
  summary_i18n     jsonb CHECK (summary_i18n IS NULL OR nl_has_base_locale(summary_i18n)),
  level            text NOT NULL CHECK (level IN ('beginner','fundamental','intermediate','advanced','professional_prep')),
  access_tier      text NOT NULL DEFAULT 'premium' CHECK (access_tier IN ('free','premium','organization')),
  status           text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (access_tier <> 'organization' OR organization_id IS NOT NULL)
);

-- Module = "cours" au sens du cahier des charges. Réutilisable entre programmes.
CREATE TABLE modules (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code               text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_-]+$'),
  specialty_id       uuid NOT NULL REFERENCES specialties(id) ON DELETE RESTRICT,
  organization_id    uuid REFERENCES organizations(id) ON DELETE CASCADE,
  level              text NOT NULL CHECK (level IN ('beginner','fundamental','intermediate','advanced','professional_prep')),
  title_i18n         jsonb NOT NULL CHECK (nl_has_base_locale(title_i18n)),
  summary_i18n       jsonb CHECK (summary_i18n IS NULL OR nl_has_base_locale(summary_i18n)),
  estimated_minutes  integer NOT NULL DEFAULT 30 CHECK (estimated_minutes BETWEEN 1 AND 6000),
  access_tier        text NOT NULL DEFAULT 'premium' CHECK (access_tier IN ('free','premium','organization')),
  status             text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  created_by         uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_modules_specialty ON modules (specialty_id, level) WHERE status = 'published';

CREATE TABLE program_modules (
  program_id   uuid NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  module_id    uuid NOT NULL REFERENCES modules(id) ON DELETE RESTRICT,
  position     integer NOT NULL CHECK (position >= 0),
  is_required  boolean NOT NULL DEFAULT true,
  PRIMARY KEY (program_id, module_id),
  UNIQUE (program_id, position) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE module_prerequisites (
  module_id               uuid NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  prerequisite_module_id  uuid NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  PRIMARY KEY (module_id, prerequisite_module_id),
  CHECK (module_id <> prerequisite_module_id)
);

CREATE TABLE lessons (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  module_id          uuid NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  code               text NOT NULL CHECK (code ~ '^[a-z0-9_-]+$'),
  position           integer NOT NULL CHECK (position >= 0),
  kind               text NOT NULL CHECK (kind IN
                      ('theory','video','revision_sheet','interactive','case_study','anatomy')),
  estimated_minutes  integer NOT NULL DEFAULT 10 CHECK (estimated_minutes BETWEEN 1 AND 600),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (module_id, code),
  UNIQUE (module_id, position) DEFERRABLE INITIALLY DEFERRED
);

-- Versionnement + validation par un professionnel qualifié (§13).
CREATE TABLE lesson_versions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lesson_id        uuid NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
  locale           text NOT NULL REFERENCES locales(code) ON DELETE RESTRICT,
  jurisdiction_id  uuid REFERENCES jurisdictions(id) ON DELETE RESTRICT,  -- NULL = contenu commun
  version_no       integer NOT NULL CHECK (version_no >= 1),
  title            text NOT NULL CHECK (length(title) BETWEEN 2 AND 300),
  body             jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object' AND body ? 'blocks'),
  status           text NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft','in_review','approved','published','archived')),
  change_summary   text,
  author_id        uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_by      uuid REFERENCES users(id) ON DELETE RESTRICT,
  reviewed_at      timestamptz,
  published_at     timestamptz,
  next_review_due  date,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (lesson_id, locale, jurisdiction_id, version_no),
  -- Aucun contenu approuvé/publié sans relecteur ni date de relecture.
  CHECK (status NOT IN ('approved','published') OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
  -- Principe des quatre yeux : l'auteur ne valide pas son propre contenu.
  CHECK (reviewed_by IS NULL OR author_id IS NULL OR reviewed_by <> author_id),
  CHECK (status <> 'published' OR (published_at IS NOT NULL AND next_review_due IS NOT NULL))
);
-- Une seule version publiée par leçon, langue et juridiction.
CREATE UNIQUE INDEX ux_lesson_versions_one_published
  ON lesson_versions (lesson_id, locale, jurisdiction_id) NULLS NOT DISTINCT
  WHERE status = 'published';
CREATE INDEX ix_lesson_versions_review_due ON lesson_versions (next_review_due) WHERE status = 'published';

CREATE TABLE lesson_version_references (
  lesson_version_id  uuid NOT NULL REFERENCES lesson_versions(id) ON DELETE CASCADE,
  reference_id       uuid NOT NULL REFERENCES medical_references(id) ON DELETE RESTRICT,
  locator            text,                              -- page, section, recommandation n°
  PRIMARY KEY (lesson_version_id, reference_id)
);

CREATE TABLE learning_resources (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  module_id     uuid REFERENCES modules(id) ON DELETE CASCADE,
  lesson_id     uuid REFERENCES lessons(id) ON DELETE CASCADE,
  kind          text NOT NULL CHECK (kind IN ('image','illustration','video','audio','pdf','model3d','link')),
  locale        text REFERENCES locales(code) ON DELETE RESTRICT,
  title         text NOT NULL,
  storage_key   text,                                   -- clé S3
  external_url  text CHECK (external_url IS NULL OR external_url ~ '^https://'),
  alt_text_i18n jsonb,                                  -- obligatoire pour images (WCAG)
  captions_key  text,                                   -- sous-titres obligatoires pour vidéos
  license       text NOT NULL,
  attribution   text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(module_id, lesson_id) >= 1),
  CHECK (num_nonnulls(storage_key, external_url) = 1),
  CHECK (kind NOT IN ('image','illustration') OR nl_has_base_locale(alt_text_i18n)),
  CHECK (kind <> 'video' OR captions_key IS NOT NULL)
);

-- =============================================================================
-- 6. OBJECTIFS ET COMPÉTENCES (avec périmètre d'exercice par métier)
-- =============================================================================
CREATE TABLE competencies (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code              text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_.-]+$'),
  specialty_id      uuid NOT NULL REFERENCES specialties(id) ON DELETE RESTRICT,
  title_i18n        jsonb NOT NULL CHECK (nl_has_base_locale(title_i18n)),
  description_i18n  jsonb,
  domain            text NOT NULL CHECK (domain IN ('knowledge','skill','attitude')),
  risk_level        text NOT NULL DEFAULT 'low' CHECK (risk_level IN ('low','moderate','high')),
  is_regulated_act  boolean NOT NULL DEFAULT false,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Actes permis configurables par métier ET juridiction (§9).
CREATE TABLE competency_scopes (
  competency_id   uuid NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
  profession_id   uuid NOT NULL REFERENCES professions(id) ON DELETE CASCADE,
  scope           text NOT NULL CHECK (scope IN
                   ('in_scope','under_supervision','delegated_only','out_of_scope','to_verify')),
  note_i18n       jsonb,
  reference_id    uuid REFERENCES medical_references(id) ON DELETE SET NULL,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (competency_id, profession_id)
);

CREATE TABLE learning_objectives (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  module_id    uuid NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  lesson_id    uuid REFERENCES lessons(id) ON DELETE CASCADE,
  position     integer NOT NULL DEFAULT 0,
  text_i18n    jsonb NOT NULL CHECK (nl_has_base_locale(text_i18n)),
  bloom_level  text NOT NULL CHECK (bloom_level IN ('remember','understand','apply','analyze','evaluate','create'))
);

CREATE TABLE objective_competencies (
  objective_id   uuid NOT NULL REFERENCES learning_objectives(id) ON DELETE CASCADE,
  competency_id  uuid NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
  PRIMARY KEY (objective_id, competency_id)
);

-- =============================================================================
-- 7. QUESTIONS, QUIZ, EXAMENS
-- =============================================================================
CREATE TABLE questions (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  type              text NOT NULL CHECK (type IN
                     ('single_choice','true_false','multiple_choice','short_answer',
                      'matching','ordering','anatomy_hotspot','case_study')),
  specialty_id      uuid NOT NULL REFERENCES specialties(id) ON DELETE RESTRICT,
  competency_id     uuid REFERENCES competencies(id) ON DELETE SET NULL,
  organization_id   uuid REFERENCES organizations(id) ON DELETE CASCADE,
  level             text NOT NULL CHECK (level IN ('beginner','fundamental','intermediate','advanced','professional_prep')),
  difficulty        smallint NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  stem_i18n         jsonb NOT NULL CHECK (nl_has_base_locale(stem_i18n)),
  case_context_i18n jsonb,                               -- vignette clinique fictive
  explanation_i18n  jsonb NOT NULL CHECK (nl_has_base_locale(explanation_i18n)),  -- correction détaillée obligatoire
  media_resource_id uuid REFERENCES learning_resources(id) ON DELETE SET NULL,
  hotspot_spec      jsonb,                               -- zones cliquables (identification anatomique)
  tags              text[] NOT NULL DEFAULT '{}',
  origin            text NOT NULL DEFAULT 'human' CHECK (origin IN ('human','ai_generated')),
  status            text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','in_review','approved','retired')),
  version_no        integer NOT NULL DEFAULT 1 CHECK (version_no >= 1),
  author_id         uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_by       uuid REFERENCES users(id) ON DELETE RESTRICT,
  reviewed_at       timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'approved' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
  CHECK (type <> 'anatomy_hotspot' OR hotspot_spec IS NOT NULL)
);
CREATE INDEX ix_questions_bank ON questions (specialty_id, level, difficulty) WHERE status = 'approved';
CREATE INDEX ix_questions_competency ON questions (competency_id);
CREATE INDEX ix_questions_tags ON questions USING gin (tags);

CREATE TABLE answers (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id    uuid NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  position       integer NOT NULL CHECK (position >= 0),
  text_i18n      jsonb NOT NULL CHECK (nl_has_base_locale(text_i18n)),
  is_correct     boolean NOT NULL DEFAULT false,
  match_key      text,          -- association : les réponses de même clé vont ensemble
  correct_order  integer,       -- remise en ordre
  feedback_i18n  jsonb,
  UNIQUE (question_id, position)
);

CREATE TABLE question_references (
  question_id   uuid NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  reference_id  uuid NOT NULL REFERENCES medical_references(id) ON DELETE RESTRICT,
  locator       text,
  PRIMARY KEY (question_id, reference_id)
);

CREATE TABLE quizzes (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  module_id      uuid NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
  lesson_id      uuid REFERENCES lessons(id) ON DELETE CASCADE,
  kind           text NOT NULL CHECK (kind IN ('practice','lesson_check','module_final')),
  title_i18n     jsonb NOT NULL CHECK (nl_has_base_locale(title_i18n)),
  pass_mark_pct  numeric(5,2) NOT NULL DEFAULT 70 CHECK (pass_mark_pct BETWEEN 0 AND 100),
  shuffle        boolean NOT NULL DEFAULT true,
  access_tier    text NOT NULL DEFAULT 'free' CHECK (access_tier IN ('free','premium','organization')),
  status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE quiz_questions (
  quiz_id      uuid NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
  question_id  uuid NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
  position     integer NOT NULL CHECK (position >= 0),
  points       numeric(6,2) NOT NULL DEFAULT 1 CHECK (points > 0),
  PRIMARY KEY (quiz_id, question_id)
);

CREATE TABLE exams (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code                text NOT NULL UNIQUE,
  kind                text NOT NULL CHECK (kind IN ('timed','mock','competency','osce','final')),
  program_id          uuid REFERENCES programs(id) ON DELETE CASCADE,
  module_id           uuid REFERENCES modules(id) ON DELETE CASCADE,
  organization_id     uuid REFERENCES organizations(id) ON DELETE CASCADE,
  title_i18n          jsonb NOT NULL CHECK (nl_has_base_locale(title_i18n)),
  instructions_i18n   jsonb,
  time_limit_seconds  integer CHECK (time_limit_seconds IS NULL OR time_limit_seconds BETWEEN 60 AND 28800),
  pass_mark_pct       numeric(5,2) NOT NULL DEFAULT 70 CHECK (pass_mark_pct BETWEEN 0 AND 100),
  max_attempts        smallint CHECK (max_attempts IS NULL OR max_attempts >= 1),
  blueprint           jsonb,   -- règles de tirage dans la banque (spécialité, niveau, compétence, difficulté)
  rubric              jsonb,   -- barème (obligatoire pour ECOS/OSCE)
  access_tier         text NOT NULL DEFAULT 'premium' CHECK (access_tier IN ('free','premium','organization')),
  status              text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','archived')),
  opens_at            timestamptz,
  closes_at           timestamptz,
  created_by          uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (kind <> 'timed' OR time_limit_seconds IS NOT NULL),
  CHECK (kind <> 'osce' OR rubric IS NOT NULL),
  CHECK (closes_at IS NULL OR opens_at IS NULL OR closes_at > opens_at)
);

CREATE TABLE exam_questions (
  exam_id      uuid NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
  question_id  uuid NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
  position     integer NOT NULL CHECK (position >= 0),
  points       numeric(6,2) NOT NULL DEFAULT 1 CHECK (points > 0),
  PRIMARY KEY (exam_id, question_id)
);

-- Tentatives de quiz OU d'examen (une seule cible).
CREATE TABLE exam_attempts (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  exam_id        uuid REFERENCES exams(id) ON DELETE CASCADE,
  quiz_id        uuid REFERENCES quizzes(id) ON DELETE CASCADE,
  status         text NOT NULL DEFAULT 'in_progress'
                 CHECK (status IN ('in_progress','submitted','graded','expired','voided')),
  form_snapshot  jsonb NOT NULL,   -- questions/versions servies : la correction reste reproductible
  started_at     timestamptz NOT NULL DEFAULT now(),
  expires_at     timestamptz,
  submitted_at   timestamptz,
  score_points   numeric(8,2),
  max_points     numeric(8,2),
  score_pct      numeric(5,2) CHECK (score_pct IS NULL OR score_pct BETWEEN 0 AND 100),
  passed         boolean,
  graded_by      uuid REFERENCES users(id) ON DELETE SET NULL,
  graded_at      timestamptz,
  CHECK (num_nonnulls(exam_id, quiz_id) = 1),
  CHECK (submitted_at IS NULL OR submitted_at >= started_at),
  CHECK (status NOT IN ('submitted','graded') OR submitted_at IS NOT NULL),
  CHECK (status <> 'graded' OR (score_pct IS NOT NULL AND passed IS NOT NULL)),
  CHECK (score_points IS NULL OR max_points IS NULL OR score_points BETWEEN 0 AND max_points)
);
CREATE INDEX ix_exam_attempts_user ON exam_attempts (user_id, started_at DESC);
CREATE INDEX ix_exam_attempts_exam ON exam_attempts (exam_id) WHERE exam_id IS NOT NULL;
CREATE INDEX ix_exam_attempts_quiz ON exam_attempts (quiz_id) WHERE quiz_id IS NOT NULL;

CREATE TABLE attempt_responses (
  attempt_id      uuid NOT NULL REFERENCES exam_attempts(id) ON DELETE CASCADE,
  question_id     uuid NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
  response        jsonb NOT NULL,
  is_correct      boolean,
  points_awarded  numeric(6,2) CHECK (points_awarded IS NULL OR points_awarded >= 0),
  grader_comment  text,
  answered_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (attempt_id, question_id)
);

-- =============================================================================
-- 8. SIMULATIONS CLINIQUES (patients 100 % fictifs par défaut)
-- =============================================================================
CREATE TABLE clinical_simulations (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code                     text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_-]+$'),
  specialty_id             uuid NOT NULL REFERENCES specialties(id) ON DELETE RESTRICT,
  module_id                uuid REFERENCES modules(id) ON DELETE SET NULL,
  level                    text NOT NULL CHECK (level IN ('beginner','fundamental','intermediate','advanced','professional_prep')),
  title_i18n               jsonb NOT NULL CHECK (nl_has_base_locale(title_i18n)),
  virtual_patient          jsonb NOT NULL,     -- profil fictif
  scenario                 jsonb NOT NULL CHECK (scenario ? 'start' AND scenario ? 'nodes'),  -- graphe d'états
  renderer                 text NOT NULL DEFAULT '2d' CHECK (renderer IN ('2d','3d')),
  engine_version           text NOT NULL DEFAULT '1.0',
  patient_data_origin      text NOT NULL DEFAULT 'fictional'
                           CHECK (patient_data_origin IN ('fictional','synthetic','deidentified_approved')),
  data_use_approval_ref    text,
  involves_regulated_act   boolean NOT NULL DEFAULT false,
  supervision_notice_i18n  jsonb,
  max_score                integer NOT NULL CHECK (max_score > 0),
  access_tier              text NOT NULL DEFAULT 'premium' CHECK (access_tier IN ('free','premium','organization')),
  status                   text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','in_review','approved','published','archived')),
  author_id                uuid REFERENCES users(id) ON DELETE SET NULL,
  reviewed_by              uuid REFERENCES users(id) ON DELETE RESTRICT,
  reviewed_at              timestamptz,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  -- Données dé-identifiées uniquement avec référence d'approbation explicite (§11).
  CHECK (patient_data_origin <> 'deidentified_approved' OR data_use_approval_ref IS NOT NULL),
  -- Actes réglementés : rappel de supervision obligatoire (§4).
  CHECK (NOT involves_regulated_act OR nl_has_base_locale(supervision_notice_i18n)),
  CHECK (status NOT IN ('approved','published') OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL))
);

CREATE TABLE simulation_competencies (
  simulation_id  uuid NOT NULL REFERENCES clinical_simulations(id) ON DELETE CASCADE,
  competency_id  uuid NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
  PRIMARY KEY (simulation_id, competency_id)
);

CREATE TABLE simulation_attempts (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  simulation_id    uuid NOT NULL REFERENCES clinical_simulations(id) ON DELETE CASCADE,
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  engine_version   text NOT NULL,
  decisions        jsonb NOT NULL DEFAULT '[]'::jsonb,   -- chemin parcouru, horodaté
  status           text NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress','completed','abandoned')),
  score            integer CHECK (score IS NULL OR score >= 0),
  max_score        integer NOT NULL CHECK (max_score > 0),
  critical_errors  integer NOT NULL DEFAULT 0 CHECK (critical_errors >= 0),
  started_at       timestamptz NOT NULL DEFAULT now(),
  completed_at     timestamptz,
  CHECK (score IS NULL OR score <= max_score),
  CHECK (status <> 'completed' OR (completed_at IS NOT NULL AND score IS NOT NULL))
);
CREATE INDEX ix_simulation_attempts_user ON simulation_attempts (user_id, started_at DESC);

-- =============================================================================
-- 9. PROGRESSION, COMPÉTENCES ACQUISES, RÉPÉTITION ESPACÉE, PLANS D'ÉTUDE
-- =============================================================================
CREATE TABLE student_progress (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  program_id        uuid REFERENCES programs(id) ON DELETE CASCADE,
  module_id         uuid REFERENCES modules(id) ON DELETE CASCADE,
  lesson_id         uuid REFERENCES lessons(id) ON DELETE CASCADE,
  status            text NOT NULL DEFAULT 'in_progress' CHECK (status IN ('not_started','in_progress','completed')),
  percent_complete  numeric(5,2) NOT NULL DEFAULT 0 CHECK (percent_complete BETWEEN 0 AND 100),
  time_spent_sec    integer NOT NULL DEFAULT 0 CHECK (time_spent_sec >= 0),
  last_activity_at  timestamptz NOT NULL DEFAULT now(),
  completed_at      timestamptz,
  CHECK (num_nonnulls(program_id, module_id, lesson_id) = 1),
  CHECK (status <> 'completed' OR (completed_at IS NOT NULL AND percent_complete = 100))
);
CREATE UNIQUE INDEX ux_progress_program ON student_progress (user_id, program_id) WHERE program_id IS NOT NULL;
CREATE UNIQUE INDEX ux_progress_module  ON student_progress (user_id, module_id)  WHERE module_id  IS NOT NULL;
CREATE UNIQUE INDEX ux_progress_lesson  ON student_progress (user_id, lesson_id)  WHERE lesson_id  IS NOT NULL;

-- Compétences acquises. "verified_in_practice" exige un formateur : une
-- simulation virtuelle seule ne suffit jamais (§13).
CREATE TABLE competency_assessments (
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  competency_id  uuid NOT NULL REFERENCES competencies(id) ON DELETE CASCADE,
  level          text NOT NULL CHECK (level IN
                  ('not_assessed','emerging','developing','theory_proficient','verified_in_practice')),
  evidence       jsonb NOT NULL DEFAULT '[]'::jsonb,
  verified_by    uuid REFERENCES users(id) ON DELETE RESTRICT,
  verified_at    timestamptz,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, competency_id),
  CHECK (level <> 'verified_in_practice' OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)),
  CHECK (verified_by IS NULL OR verified_by <> user_id)
);

-- Répétition espacée (algorithme SM-2 / FSRS paramétrable).
CREATE TABLE review_cards (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id    uuid NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  ease_factor    numeric(4,2) NOT NULL DEFAULT 2.5 CHECK (ease_factor BETWEEN 1.3 AND 5),
  interval_days  integer NOT NULL DEFAULT 0 CHECK (interval_days >= 0),
  repetitions    integer NOT NULL DEFAULT 0 CHECK (repetitions >= 0),
  lapses         integer NOT NULL DEFAULT 0 CHECK (lapses >= 0),
  due_at         timestamptz NOT NULL DEFAULT now(),
  last_review_at timestamptz,
  UNIQUE (user_id, question_id)
);
CREATE INDEX ix_review_cards_due ON review_cards (user_id, due_at);

CREATE TABLE study_plans (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  program_id           uuid REFERENCES programs(id) ON DELETE SET NULL,
  title                text NOT NULL,
  starts_on            date NOT NULL,
  ends_on              date NOT NULL,
  weekly_goal_minutes  integer NOT NULL CHECK (weekly_goal_minutes BETWEEN 0 AND 3000),
  generated_by         text NOT NULL CHECK (generated_by IN ('student','instructor','ai')),
  status               text NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','abandoned')),
  created_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (ends_on >= starts_on)
);

CREATE TABLE study_plan_items (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id        uuid NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
  scheduled_for  date NOT NULL,
  module_id      uuid REFERENCES modules(id) ON DELETE CASCADE,
  lesson_id      uuid REFERENCES lessons(id) ON DELETE CASCADE,
  exam_id        uuid REFERENCES exams(id) ON DELETE CASCADE,
  simulation_id  uuid REFERENCES clinical_simulations(id) ON DELETE CASCADE,
  minutes        integer NOT NULL CHECK (minutes BETWEEN 1 AND 600),
  done_at        timestamptz,
  CHECK (num_nonnulls(module_id, lesson_id, exam_id, simulation_id) = 1)
);
CREATE INDEX ix_study_plan_items_plan ON study_plan_items (plan_id, scheduled_for);

-- =============================================================================
-- 10. GAMIFICATION ET ATTESTATIONS INTERNES
-- =============================================================================
CREATE TABLE badges (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code         text NOT NULL UNIQUE,
  name_i18n    jsonb NOT NULL CHECK (nl_has_base_locale(name_i18n)),
  criteria     jsonb NOT NULL,
  icon_key     text
);

CREATE TABLE user_badges (
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  badge_id   uuid NOT NULL REFERENCES badges(id) ON DELETE CASCADE,
  awarded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, badge_id)
);

CREATE TABLE xp_events (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source      text NOT NULL CHECK (source IN ('lesson','quiz','exam','simulation','streak','challenge')),
  source_id   uuid,
  points      integer NOT NULL CHECK (points BETWEEN -1000 AND 1000),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_xp_events_user ON xp_events (user_id, created_at DESC);

-- Attestation INTERNE uniquement : le type est verrouillé et la mention
-- de non-équivalence à un diplôme est obligatoire (§1, §13).
CREATE TABLE certificates (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  program_id         uuid REFERENCES programs(id) ON DELETE RESTRICT,
  module_id          uuid REFERENCES modules(id) ON DELETE RESTRICT,
  kind               text NOT NULL DEFAULT 'internal_attestation' CHECK (kind = 'internal_attestation'),
  serial             text NOT NULL UNIQUE,
  verification_code  text NOT NULL UNIQUE DEFAULT encode(gen_random_bytes(12), 'hex'),
  title_snapshot     text NOT NULL,
  disclaimer_i18n    jsonb NOT NULL CHECK (nl_has_base_locale(disclaimer_i18n)),
  issued_at          timestamptz NOT NULL DEFAULT now(),
  revoked_at         timestamptz,
  revoked_reason     text,
  CHECK (num_nonnulls(program_id, module_id) = 1),
  CHECK (revoked_at IS NULL OR revoked_reason IS NOT NULL)
);

-- =============================================================================
-- 11. TUTEUR IA (RAG) — l'index vectoriel est ajouté par la migration 0002
-- =============================================================================
CREATE TABLE ai_knowledge_sources (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reference_id     uuid NOT NULL REFERENCES medical_references(id) ON DELETE RESTRICT,
  locale           text NOT NULL REFERENCES locales(code) ON DELETE RESTRICT,
  jurisdiction_id  uuid REFERENCES jurisdictions(id) ON DELETE SET NULL,
  storage_key      text NOT NULL,
  sha256           char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  license          text NOT NULL,
  status           text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','ingested','approved','revoked')),
  approved_by      uuid REFERENCES users(id) ON DELETE RESTRICT,
  approved_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (sha256, locale),
  -- Seules les sources validées alimentent le tuteur (§3).
  CHECK (status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);

CREATE TABLE ai_knowledge_chunks (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id    uuid NOT NULL REFERENCES ai_knowledge_sources(id) ON DELETE CASCADE,
  chunk_index  integer NOT NULL CHECK (chunk_index >= 0),
  content      text NOT NULL,
  locator      text,                 -- page/section pour la citation
  token_count  integer NOT NULL CHECK (token_count > 0),
  UNIQUE (source_id, chunk_index)
);

CREATE TABLE ai_prompt_templates (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key         text NOT NULL CHECK (key ~ '^[a-z0-9_.]+$'),
  version     integer NOT NULL CHECK (version >= 1),
  locale      text NOT NULL REFERENCES locales(code) ON DELETE RESTRICT,
  template    text NOT NULL,
  status      text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','retired')),
  created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (key, version, locale)
);
CREATE UNIQUE INDEX ux_prompt_one_active ON ai_prompt_templates (key, locale) WHERE status = 'active';

CREATE TABLE ai_conversations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode             text NOT NULL CHECK (mode IN ('tutor','patient_roleplay','quiz_generation','correction','study_plan')),
  locale           text NOT NULL REFERENCES locales(code) ON DELETE RESTRICT,
  module_id        uuid REFERENCES modules(id) ON DELETE SET NULL,
  simulation_id    uuid REFERENCES clinical_simulations(id) ON DELETE SET NULL,
  title            text,
  retention_until  timestamptz NOT NULL DEFAULT now() + interval '365 days',
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_ai_conversations_user ON ai_conversations (user_id, updated_at DESC);
CREATE INDEX ix_ai_conversations_retention ON ai_conversations (retention_until);

CREATE TABLE ai_messages (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id  uuid NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
  role             text NOT NULL CHECK (role IN ('user','assistant','system','tool')),
  content          text NOT NULL,
  citations        jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{chunk_id, source_id, locator}]
  uncertainty      text CHECK (uncertainty IS NULL OR uncertainty IN ('low','medium','high')),
  safety_flags     jsonb NOT NULL DEFAULT '{}'::jsonb,   -- ex. {"personal_clinical_advice": true}
  model            text,
  prompt_template_id uuid REFERENCES ai_prompt_templates(id) ON DELETE SET NULL,
  input_tokens     integer CHECK (input_tokens IS NULL OR input_tokens >= 0),
  output_tokens    integer CHECK (output_tokens IS NULL OR output_tokens >= 0),
  latency_ms       integer,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (role <> 'assistant' OR model IS NOT NULL)
);
CREATE INDEX ix_ai_messages_conv ON ai_messages (conversation_id, created_at);

-- =============================================================================
-- 12. ABONNEMENTS ET PAIEMENTS (Stripe désactivé par défaut)
-- =============================================================================
CREATE TABLE plans (
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code                     text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]+$'),
  tier                     text NOT NULL CHECK (tier IN ('free','premium','organization')),
  name_i18n                jsonb NOT NULL CHECK (nl_has_base_locale(name_i18n)),
  billing_interval         text CHECK (billing_interval IN ('month','year')),
  price_cents              integer NOT NULL DEFAULT 0 CHECK (price_cents >= 0),
  currency                 char(3) NOT NULL DEFAULT 'CAD' CHECK (currency ~ '^[A-Z]{3}$'),
  stripe_price_id          text UNIQUE,
  ai_daily_message_quota   integer NOT NULL CHECK (ai_daily_message_quota >= 0),
  features                 jsonb NOT NULL DEFAULT '{}'::jsonb,
  is_active                boolean NOT NULL DEFAULT true,
  CHECK (tier <> 'free' OR price_cents = 0)
);

CREATE TABLE subscriptions (
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                   uuid REFERENCES users(id) ON DELETE SET NULL,
  organization_id           uuid REFERENCES organizations(id) ON DELETE SET NULL,
  plan_id                   uuid NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
  status                    text NOT NULL CHECK (status IN
                             ('trialing','active','past_due','canceled','incomplete','expired')),
  provider                  text NOT NULL DEFAULT 'manual' CHECK (provider IN ('manual','stripe')),
  provider_customer_id      text,
  provider_subscription_id  text UNIQUE,
  seats                     integer NOT NULL DEFAULT 1 CHECK (seats >= 1),
  current_period_start      timestamptz,
  current_period_end        timestamptz,
  cancel_at                 timestamptz,
  created_at                timestamptz NOT NULL DEFAULT now(),
  updated_at                timestamptz NOT NULL DEFAULT now(),
  -- Au plus un titulaire ; les deux à NULL seulement après effacement du compte (conservation légale).
  CHECK (num_nonnulls(user_id, organization_id) <= 1),
  CHECK (num_nonnulls(user_id, organization_id) = 1 OR status IN ('canceled','expired')),
  CHECK (provider <> 'stripe' OR provider_subscription_id IS NOT NULL),
  CHECK (current_period_end IS NULL OR current_period_start IS NULL OR current_period_end > current_period_start)
);
CREATE UNIQUE INDEX ux_subscriptions_user_live ON subscriptions (user_id)
  WHERE user_id IS NOT NULL AND status IN ('trialing','active','past_due');
CREATE INDEX ix_subscriptions_org ON subscriptions (organization_id);

CREATE TABLE payments (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_id      uuid NOT NULL REFERENCES subscriptions(id) ON DELETE RESTRICT,
  amount_cents         bigint NOT NULL CHECK (amount_cents >= 0),
  currency             char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
  status               text NOT NULL CHECK (status IN ('pending','succeeded','failed','refunded','partially_refunded')),
  provider             text NOT NULL CHECK (provider IN ('manual','stripe')),
  provider_payment_id  text UNIQUE,
  livemode             boolean NOT NULL DEFAULT false,
  paid_at              timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'succeeded' OR paid_at IS NOT NULL)
);

-- =============================================================================
-- 13. QUALITÉ : signalement et correction des erreurs de contenu
-- =============================================================================
CREATE TABLE content_issue_reports (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  reporter_id        uuid REFERENCES users(id) ON DELETE SET NULL,
  lesson_version_id  uuid REFERENCES lesson_versions(id) ON DELETE CASCADE,
  question_id        uuid REFERENCES questions(id) ON DELETE CASCADE,
  simulation_id      uuid REFERENCES clinical_simulations(id) ON DELETE CASCADE,
  ai_message_id      uuid REFERENCES ai_messages(id) ON DELETE SET NULL,
  category           text NOT NULL CHECK (category IN
                      ('medical_error','outdated','translation','accessibility','typo','scope_of_practice','other')),
  severity           text NOT NULL DEFAULT 'normal' CHECK (severity IN ('low','normal','high','critical')),
  description        text NOT NULL CHECK (length(description) BETWEEN 5 AND 5000),
  status             text NOT NULL DEFAULT 'open' CHECK (status IN ('open','triaged','fixed','rejected')),
  resolved_by        uuid REFERENCES users(id) ON DELETE SET NULL,
  resolution_note    text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  resolved_at        timestamptz,
  CHECK (num_nonnulls(lesson_version_id, question_id, simulation_id, ai_message_id) >= 1),
  CHECK (status NOT IN ('fixed','rejected') OR (resolved_at IS NOT NULL AND resolution_note IS NOT NULL))
);
CREATE INDEX ix_issue_reports_open ON content_issue_reports (severity, created_at) WHERE status IN ('open','triaged');

-- =============================================================================
-- 14. JOURNAL D'AUDIT (ajout seul, chaînage par hachage)
-- =============================================================================
CREATE TABLE audit_logs (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at     timestamptz NOT NULL DEFAULT now(),
  actor_user_id   uuid,                     -- volontairement sans FK : le journal survit aux effacements
  actor_role      text,
  action          text NOT NULL CHECK (action ~ '^[a-z_]+\.[a-z_]+$'),   -- ex. "lesson.publish"
  entity_type     text NOT NULL,
  entity_id       text,
  organization_id uuid,
  ip              inet,
  user_agent      text,
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  prev_hash       char(64),
  hash            char(64) NOT NULL
);
CREATE INDEX ix_audit_logs_entity ON audit_logs (entity_type, entity_id);
CREATE INDEX ix_audit_logs_actor ON audit_logs (actor_user_id, occurred_at DESC);

-- Calcule le hachage chaîné à l'insertion (preuve d'intégrité).
CREATE OR REPLACE FUNCTION nl_audit_chain() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE last_hash char(64);
BEGIN
  PERFORM pg_advisory_xact_lock(hashtext('nl_audit_chain'));
  SELECT hash INTO last_hash FROM audit_logs ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := last_hash;
  NEW.hash := encode(digest(
      coalesce(last_hash, '') || NEW.occurred_at::text || coalesce(NEW.actor_user_id::text, '') ||
      NEW.action || NEW.entity_type || coalesce(NEW.entity_id, '') || NEW.metadata::text,
    'sha256'), 'hex');
  RETURN NEW;
END $$;

CREATE TRIGGER trg_audit_chain BEFORE INSERT ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION nl_audit_chain();
CREATE TRIGGER trg_audit_append_only BEFORE UPDATE OR DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION nl_forbid_modification();
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON audit_logs
  FOR EACH STATEMENT EXECUTE FUNCTION nl_forbid_modification();

-- =============================================================================
-- 15. Déclencheurs updated_at (appliqués à toutes les tables qui ont la colonne)
-- =============================================================================
DO $$
DECLARE t text;
BEGIN
  FOR t IN
    SELECT c.table_name FROM information_schema.columns c
    JOIN information_schema.tables tb ON tb.table_name = c.table_name AND tb.table_schema = c.table_schema
    WHERE c.table_schema = current_schema() AND c.column_name = 'updated_at' AND tb.table_type = 'BASE TABLE'
  LOOP
    EXECUTE format('CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %I
                    FOR EACH ROW EXECUTE FUNCTION nl_set_updated_at()', t, t);
  END LOOP;
END $$;

-- Index sur les clés étrangères fréquemment jointes
CREATE INDEX ix_professions_jurisdiction ON professions (jurisdiction_id);
CREATE INDEX ix_programs_profession ON programs (profession_id);
CREATE INDEX ix_lessons_module ON lessons (module_id, position);
CREATE INDEX ix_answers_question ON answers (question_id);
CREATE INDEX ix_competencies_specialty ON competencies (specialty_id);
CREATE INDEX ix_competency_scopes_profession ON competency_scopes (profession_id);
CREATE INDEX ix_learning_objectives_module ON learning_objectives (module_id);
CREATE INDEX ix_ai_chunks_source ON ai_knowledge_chunks (source_id);
CREATE INDEX ix_certificates_user ON certificates (user_id);
CREATE INDEX ix_payments_subscription ON payments (subscription_id);
