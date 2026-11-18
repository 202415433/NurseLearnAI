# database/

- `schema/0001_core.sql` — schéma complet (60 tables) ; `0001_core_down.sql` — retour arrière.
- `schema/0002_pgvector.sql` — index vectoriel du tuteur IA (nécessite l'extension `vector`).
- `schema/0003_business_rules.sql` — habilitations des relecteurs/formateurs et isolation des établissements (Phase 1.1).
- `checks/verify_head.sql`, `checks/verify_base.sql` — contrôles après `alembic upgrade head` / `downgrade base`.
- `seeds/0001_reference_data.sql` — langues, pays, juridictions, rôles, permissions, métiers (à valider), spécialités, plans. Idempotent.
- `tests/` — tests pytest exécutés sur un vrai PostgreSQL via `psql` :

```bash
NL_TEST_PGHOST=localhost NL_TEST_PGPORT=5432 NL_TEST_PGUSER=postgres PGPASSWORD=... python -m pytest -q database/tests
```

L'utilisateur de test doit pouvoir créer des bases (`CREATEDB`).
