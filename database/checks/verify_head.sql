-- Vérifie l'état d'une base migrée à la tête (alembic upgrade head).
-- Échoue (exception) au premier écart ; affiche un résumé sinon.
-- Usage : psql -v ON_ERROR_STOP=1 -d <base> -f database/checks/verify_head.sql
DO $$
DECLARE
  n_tables int; n_fk int; n_fk_noaction int; n_idx int; n_trg int; v_vector text; n_missing int;
BEGIN
  SELECT count(*) INTO n_tables FROM information_schema.tables
   WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version';
  IF n_tables <> 62 THEN RAISE EXCEPTION 'Attendu 62 tables (60 + invitations, one_time_tokens de 0004), trouvé %', n_tables; END IF;

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
  SELECT count(*) INTO n_trg FROM pg_trigger WHERE NOT tgisinternal;
  SELECT count(*) INTO n_idx FROM pg_indexes WHERE schemaname = 'public';

  RAISE NOTICE 'OK : % tables, % clés étrangères, % index, % déclencheurs, pgvector %', n_tables, n_fk, n_idx, n_trg, v_vector;
END $$;
