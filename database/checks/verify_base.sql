-- Vérifie qu'une base est revenue à vide après « alembic downgrade base ».
DO $$
DECLARE n int;
BEGIN
  SELECT count(*) INTO n FROM information_schema.tables
   WHERE table_schema = 'public' AND table_type = 'BASE TABLE' AND table_name <> 'alembic_version';
  IF n <> 0 THEN RAISE EXCEPTION 'Après downgrade base : % table(s) restante(s)', n; END IF;
  IF to_regprocedure('nl_user_has_permission(uuid,text,uuid)') IS NOT NULL
     OR to_regprocedure('nl_set_updated_at()') IS NOT NULL THEN
    RAISE EXCEPTION 'Après downgrade base : fonctions restantes'; END IF;
  RAISE NOTICE 'OK : base vide après retour arrière';
END $$;
