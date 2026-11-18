# ADR-0002 — Schéma SQL versionné comme source de vérité initiale

**Statut** : proposé · 2026-11-18

**Contexte** — Les exigences de fiabilité (relecture, attestations, données fictives, audit) doivent tenir même si un script ou une console contourne l'API.

**Décision** — Le schéma initial est écrit en SQL (`database/schema`), testé directement sur PostgreSQL, et exécuté par les révisions Alembic. En Phase 2, les modèles SQLAlchemy sont alignés et `alembic check` vérifie l'absence d'écart ; les migrations suivantes peuvent être générées puis relues.

**Conséquences** — Invariants garantis par la base et testables sans l'application. Coût : maintenir l'alignement modèles ↔ schéma, contrôlé en CI.
