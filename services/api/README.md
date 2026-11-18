# services/api — API NurseLearn AI (FastAPI)

**Phase 1.2** : API publique en lecture seule — santé, référentiels, catalogue publié. Référence complète : [`docs/API-REFERENCE.md`](../../docs/API-REFERENCE.md).

| Route | Rôle |
|---|---|
| `GET /health` · `GET /ready` | Vivacité ; PostgreSQL et Redis joignables (503 problem+json sinon) |
| `GET /api/v1/about` | Mention « formation, ni diplôme, ni licence, ni autorisation d'exercer » |
| `GET /api/v1/countries` | Pays ayant des juridictions actives |
| `GET /api/v1/jurisdictions?country=&scope=` | Juridictions (provinces, pays, référentiels transnationaux) |
| `GET /api/v1/professions?jurisdiction=&category=` | Métiers d'une juridiction + disponibilité (compteurs publiés / en préparation) |
| `GET /api/v1/specialties?group=&jurisdiction=&profession=` | 27 spécialités (groupes A à H) |
| `GET /api/v1/programs?jurisdiction=&profession=&specialty=&level=` | Programmes **publiés et publics** uniquement, paginés |
| `GET /api/v1/programs/{id}` | Détail publié (modules, leçons ayant une version publiée) |

Structure : `app/core` (configuration, erreurs RFC 9457, journaux JSON, i18n, pagination, limite de débit), `app/db` (moteur asynchrone, projection SQLAlchemy en lecture), `app/domains/{reference,catalog}` (routes et schémas).

## Lancer

```powershell
# Tout en Docker (recommandé), depuis la racine du dépôt
docker compose up --build -d
# API : http://localhost:18000/docs   ·   Web : http://localhost:3000/fr
```

```bash
# Sans Docker pour l'API (Python 3.12)
cd services/api && pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://nurselearn:…@localhost:55432/nurselearn REDIS_URL=redis://:…@localhost:6379/0
alembic upgrade head && python -m app.seed
uvicorn app.main:create_app --factory --reload
python -m pytest -q tests/unit tests/api        # tests/api : base migrée et alimentée requise
```

`python -m app.seed` applique `database/seeds/*.sql` (idempotent), dont la **structure** du parcours pilote PAB Québec, entièrement en brouillon.
Variables : `DATABASE_URL` (obligatoire), `REDIS_URL`, `CORS_ORIGINS` (liste JSON), `RATE_LIMIT_PER_MINUTE`, `LOG_LEVEL`.
