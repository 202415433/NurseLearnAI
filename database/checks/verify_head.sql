-- Vérifie l'état d'une base migrée à la tête (alembic upgrade head).
-- Échoue (exception) au premier écart ; affiche un résumé sinon.
-- Usage : psql -v ON_ERROR_STOP=1 -d <base> -f database/checks/verify_head.sql
DO $$
DECLARE
  n_tables int; n_fk int; n_fk_noaction int; n_idx int; n_trg int; v_vector text; n_missing int;
BEGIN
  SELECT count(*) INTO n_tables FROM information_schema.tables
   WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version';
  IF n_tables <> 75 THEN RAISE EXCEPTION 'Attendu 75 tables (60 + 2 de 0004 + 4 de 0005 + 5 de 0006 + 4 de 0007), trouvé %', n_tables; END IF;

  SELECT count(*) INTO n_missing FROM unnest(ARRAY['users','roles','profiles','countries','jurisdictions','professions',
    'specialties','programs','modules','lessons','learning_objectives','competencies','learning_resources',
    'medical_references','quizzes','questions','answers','exams','exam_attempts','clinical_simulations',
    'simulation_attempts','student_progress','study_plans','ai_conversations','ai_knowledge_sources','certificates',
    'subscriptions','payments','organizations','instructors','audit_logs']) t
   WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables x WHERE x.table_schema='public' AND x.table_name=t);
  IF n_missing > 0 THEN RAISE EXCEPTION '% entité(s) du cahier des charges manquante(s)', n_missing; END IF;

  SELECT count(*), count(*) FILTER (WHERE confdeltype = 'a') INTO n_fk, n_fk_noaction
    FROM pg_constraint WHERE contype = 'f' AND connamespace = 'public'::regnamespace;
  IF n_fk_noaction > 0 THEN RAISE EXCEPTION '% clé(s) étrangère(s) sans stratégie de suppression', n_fk_noaction; END IF;

  SELECT extversion INTO v_vector FROM pg_extension WHERE extname = 'vector';
  IF v_vector IS NULL THEN RAISE EXCEPTION 'Extension pgvector absente'; END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='ai_knowledge_chunks' AND column_name='embedding'
                 AND udt_name = 'vector') THEN RAISE EXCEPTION 'Colonne embedding vector absente'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='ix_ai_chunks_embedding_hnsw' AND indexdef LIKE '%hnsw%')
    THEN RAISE EXCEPTION 'Index HNSW absent'; END IF;

  IF to_regprocedure('nl_user_has_permission(uuid,text,uuid)') IS NULL THEN
    RAISE EXCEPTION 'Migration 0003 non appliquée (nl_user_has_permission absente)'; END IF;
  IF to_regprocedure('nl_guard_role_grant()') IS NULL OR to_regclass('public.invitations') IS NULL THEN
    RAISE EXCEPTION 'Migration 0004 non appliquée (garde des rôles ou invitations absente)'; END IF;
  IF to_regclass('public.program_releases') IS NULL OR to_regclass('public.editorial_decisions') IS NULL
     OR to_regprocedure('nl_validate_release(uuid,jsonb,text,uuid)') IS NULL
     OR NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_lesson_versions_status') THEN
    RAISE EXCEPTION 'Migration 0005 non appliquée (éditions, journal éditorial ou états absents)'; END IF;
  IF to_regclass('public.module_versions') IS NULL OR to_regclass('public.program_release_modules') IS NULL
     OR to_regprocedure('nl_release_lessons(uuid)') IS NULL
     OR to_regclass('public.ux_lesson_versions_one_published') IS NOT NULL THEN
    RAISE EXCEPTION 'Migration 0006 non appliquée (versions de modules ou indépendance des éditions absentes)'; END IF;
  IF to_regclass('public.media_assets') IS NULL OR to_regclass('public.media_scan_events') IS NULL
     OR to_regclass('public.lesson_version_media') IS NULL OR to_regclass('public.media_usage') IS NULL
     OR to_regprocedure('nl_media_expire_reservations()') IS NULL
     OR NOT EXISTS (SELECT 1 FROM permissions WHERE code = 'media:scan')
     OR to_regclass('public.learning_resources') IS NULL THEN
    RAISE EXCEPTION 'Migration 0007 non appliquée'; END IF;
  -- 0008 : media:scan détenue par le SEUL rôle de service media_scanner ; connexion PostgreSQL dédiée
  -- non privilégiée ; gardes de session de service posées.
  IF EXISTS (SELECT 1 FROM role_permissions rp JOIN roles r ON r.id = rp.role_id
              WHERE rp.permission_code = 'media:scan' AND r.code <> 'media_scanner')
     OR EXISTS (SELECT 1 FROM role_permissions rp JOIN roles r ON r.id = rp.role_id
                 WHERE r.code = 'media_scanner' AND rp.permission_code <> 'media:scan') THEN
    RAISE EXCEPTION 'Permission media:scan attribuée hors du rôle de service media_scanner'; END IF;
  IF NOT EXISTS (SELECT 1 FROM roles WHERE code = 'media_scanner')
     OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nl_media_scanner' AND NOT rolsuper AND NOT rolcreaterole
                    AND NOT rolcreatedb AND NOT rolbypassrls AND NOT rolreplication)
     OR to_regprocedure('nl_is_media_scanner_session()') IS NULL
     OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_media_assets_05_scanner')
     OR NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_media_scan_events_05_scanner') THEN
    RAISE EXCEPTION 'Migration 0008 non appliquée (rôle de service, connexion dédiée ou gardes absents)'; END IF;
  SELECT count(*) INTO n_trg FROM pg_trigger WHERE NOT tgisinternal;
  SELECT count(*) INTO n_idx FROM pg_indexes WHERE schemaname = 'public';

  RAISE NOTICE 'OK : % tables, % clés étrangères, % index, % déclencheurs, pgvector %', n_tables, n_fk, n_idx, n_trg, v_vector;
END $$;
