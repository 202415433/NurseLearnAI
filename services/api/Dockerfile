# API NurseLearn AI — image de développement et de recette.
# Contexte de construction : racine du dépôt (voir infra/docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NL_SCHEMA_DIR=/srv/database/schema \
    NL_SEEDS_DIR=/srv/database/seeds \
    PYTHONPATH=/srv/api

WORKDIR /srv/api

# Dépendances d'abord (cache Docker), puis le code.
COPY services/api/pyproject.toml ./
RUN python -c "import tomllib; print('\\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/api/app ./app
COPY services/api/alembic ./alembic
COPY services/api/alembic.ini ./
COPY database/schema /srv/database/schema
COPY database/seeds /srv/database/seeds

RUN useradd --system --uid 10001 --home /srv nurselearn && chown -R nurselearn /srv
USER nurselearn

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=12 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

# Développement : migrations + données de référence au démarrage, puis l'API.
# En production, les migrations seront lancées par une tâche dédiée (Phase 7).
# Jusqu'à 10 tentatives de migration (base pas encore joignable au tout premier démarrage), puis arrêt clair.
CMD ["sh", "-c", "n=0; until alembic upgrade head; do n=$((n+1)); if [ $n -ge 10 ]; then echo 'migrations impossibles : base injoignable' >&2; exit 1; fi; sleep 3; done && python -m app.seed && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers"]
