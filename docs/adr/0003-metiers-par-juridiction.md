# ADR-0003 — Métiers définis par juridiction, actes permis par compétence

**Statut** : proposé · 2026-11-18

**Contexte** — Aide-soignant (France), PAB (Québec), PSW (Ontario), infirmier auxiliaire, infirmier autorisé… ne sont pas équivalents (cahier des charges §9).

**Décision** — `professions.jurisdiction_id` est obligatoire ; il n'existe aucune table d'équivalence. Les actes permis sont décrits par `competency_scopes(competency, profession, scope)` avec `to_verify` lorsque l'information n'est pas validée. Les métiers de départ sont marqués `pending_expert_review`.

**Conséquences** — Plus de saisie par juridiction, mais aucune équivalence implicite possible. Le contenu commun (anatomie, hygiène) est réutilisé via `program_modules`.
