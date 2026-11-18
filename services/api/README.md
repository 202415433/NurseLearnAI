# services/api — API NurseLearn AI (FastAPI)

**Phase 1** : seules les migrations Alembic sont présentes (`alembic/versions/0001`, `0002`), elles exécutent les fichiers de `/database/schema`.
**Phase 2** : code applicatif (`app/`), modèles SQLAlchemy, routes `/api/v1` — voir `docs/07-plan-phase-2.md`.

```bash
# avec les dépendances installées (pip install -e ".[dev]")
export DATABASE_URL=postgresql+psycopg://nurselearn:...@localhost:5432/nurselearn
alembic upgrade head
```

Si la base a été créée avec `scripts/db-apply`, marquer l'état courant avant d'utiliser Alembic : `alembic stamp head`.
