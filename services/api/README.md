# services/api — API NurseLearn AI (FastAPI)

**Lot 1.2** : API en lecture seule — santé, référentiels, catalogue public.

| Route | Rôle |
|---|---|
| `GET /health` | Vivacité du processus |
| `GET /ready` | PostgreSQL et Redis joignables (503 au format problem+json sinon) |
| `GET /api/v1/about` | Mention « formation, pas un diplôme » (FR/EN) |
| `GET /api/v1/jurisdictions` | Référentiels pédagogiques actifs |
| `GET /api/v1/jurisdictions/{code}/professions` | Métiers d'une juridiction, avec statut réglementaire et statut de validation |
| `GET /api/v1/specialties` | 27 spécialités (groupes A à H) |
| `GET /api/v1/programs?jurisdiction=&profession=&level=` | Programmes **publiés** et publics uniquement |
| `GET /api/v1/programs/{code}` · `GET /api/v1/modules/{code}?jurisdiction=` | Détails publiés ; une leçon n'apparaît que si elle a une version publiée |

Langue : `Accept-Language` ou `?lang=fr|en` (repli FR → EN). Erreurs : RFC 9457 (`application/problem+json`) avec `request_id`.
Journaux JSON sans données personnelles (ni IP, ni corps, ni chaîne de requête). Documentation interactive : `/docs`.

## Lancer

```powershell
# Tout en Docker (recommandé)
docker compose -f infra/docker-compose.yml --env-file .env --profile app up -d --build
# API : http://localhost:8000/docs   ·   Web : http://localhost:3000
```

```bash
# Sans Docker pour l'API (Python 3.12)
cd services/api && pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://nurselearn:…@localhost:55432/nurselearn REDIS_URL=redis://:…@localhost:6379/0
alembic upgrade head && python -m app.seed
uvicorn app.main:create_app --factory --reload
python -m pytest -q tests/api        # base migrée et alimentée requise
```

`python -m app.seed` applique `database/seeds/*.sql` (idempotent), dont la **structure** du parcours pilote PAB Québec, entièrement en brouillon.
