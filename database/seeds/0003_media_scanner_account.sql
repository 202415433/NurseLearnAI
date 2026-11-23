-- Compte de service d'analyse antivirus (migration 0008) : création idempotente, une fois les
-- langues de référence chargées. Sans effet sur une base antérieure à 0008.
DO $$
BEGIN
  IF to_regprocedure('nl_media_scanner_provision()') IS NOT NULL THEN
    PERFORM nl_media_scanner_provision();
  END IF;
END $$;
