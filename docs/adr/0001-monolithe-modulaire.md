# ADR-0001 — Monolithe modulaire FastAPI

**Statut** : proposé · 2026-11-18

**Contexte** — Petite équipe, domaine riche (catalogue, évaluation, IA, facturation), besoin de livrer vite sans dette structurelle.

**Décision** — Une seule API FastAPI découpée en domaines (`app/domains/*`) aux frontières strictes : un domaine n'importe que l'interface de service d'un autre, jamais ses modèles ou repositories. Un worker séparé (arq) pour les tâches longues.

**Conséquences** — Déploiement, transactions et tests simples. Le tuteur IA, gourmand et à forte latence, pourra être extrait en service distinct si la charge l'exige. Une règle de lint (import-linter) vérifiera les frontières en CI dès la Phase 2.
