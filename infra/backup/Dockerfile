# Outillage de sauvegarde et de restauration des médias (2.2.3-e, D22). Image LOCALE d'exploitation :
# client PostgreSQL 16 (même version majeure que le serveur : pg_dump/pg_restore compatibles), Python
# (cryptography pour AES-256-GCM, boto3 pour S3, psycopg). Aucun service, aucun port, aucun secret.
FROM python:3.12-slim
ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends postgresql-common ca-certificates gnupg >/dev/null \
 && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y >/dev/null \
 && apt-get install -y -qq --no-install-recommends postgresql-client-16 >/dev/null \
 && rm -rf /var/lib/apt/lists/*
RUN pip install -q --root-user-action=ignore "cryptography>=44,<52" "boto3>=1.35,<2" "psycopg[binary]>=3.2,<4"
COPY scripts/media_backup.py /tools/media_backup.py
USER 65534:65534
ENTRYPOINT ["python", "-I", "/tools/media_backup.py"]
