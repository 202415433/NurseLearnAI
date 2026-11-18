# ADR-0006 — Libellés traduisibles en JSONB, contenu long versionné par langue

**Statut** : proposé · 2026-11-18

**Décision** — Les libellés courts (titres, noms, énoncés de questions) sont stockés en `jsonb` `{"fr","en",…}` avec au moins une langue de base. Le contenu long (corps de leçon) est une ligne `lesson_versions` par langue, relue séparément.

**Conséquences** — Ajouter une langue ne demande aucune migration. Une traduction de leçon est validée comme un contenu à part entière (une erreur de traduction peut être une erreur médicale).
