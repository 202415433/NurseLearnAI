# Rapport de validation — Phase 1.1

> 18 novembre 2026 · branche `phase-1.1` (non poussée) · base : commit `79ef00f` (Phase 1)

## 1. Décisions du porteur de projet prises en compte

| Décision | Prise en compte |
|---|---|
| Architecture et roadmap : accord de principe | Aucun choix d'architecture modifié ; ajouts uniquement (migration 0003, contrôles, script de validation) |
| Pilote : PAB Québec, module « Hygiène des mains et prévention des infections » | Inscrit dans le lot d'intégration (§9) ; métier `pab` / `CA-QC` déjà présent dans les données de référence |
| Aucun relecteur médical confirmé : contenus en DRAFT | La base l'impose déjà : pas d'`approved`/`published` sans relecteur habilité (renforcé en 0003). Test `test_drafts_never_need_a_reviewer` |
| Rôles MEDICAL_REVIEWER et ORGANIZATION_ADMIN approuvés | Présents depuis la Phase 1 (`medical_reviewer`, `org_admin`) ; matrice de permissions désormais testée |

## 2. Environnements utilisés

| Environnement | Rôle | Limites |
|---|---|---|
| **Espace de travail Claude (Linux)** | PostgreSQL 16.15, Python 3.13, pytest 9, Node.js 22 | Pas de Docker, pas de pgvector, aucun paquet installable (accès réseau aux registres refusé ; la compilation de PostgreSQL+pgvector et l'usage d'Alembic depuis des sources clonées ont été refusés par la politique de l'espace de travail) |
| **Votre PC Windows** (`C:\NurseLearnAI-git`) | Docker Desktop, images `pgvector/pgvector:pg16`, `python:3.12-slim` | Exécution par `scripts/validate-phase1.1.ps1` — voir §4 |
| **GitHub Actions** | Exécution réelle de la CI | Seul le commit `79ef00f` y a tourné ; la branche `phase-1.1` n'est pas poussée (autorisation requise) |

## 3. Commandes exécutées dans l'espace de travail et résultats

| Commande | Résultat |
|---|---|
| `pytest database/tests services/api/tests` (état initial, avant correction) | 32 réussis, 1 ignoré (pgvector) |
| `npm test` (design-tokens, état initial) | 58/58 |
| `pytest database/tests services/api/tests` (après corrections) | **44 réussis, 1 ignoré (pgvector absent de cet environnement)** |
| `npm test` (design-tokens, final) | **58/58** |
| `psql -f 0001 -f 0003 -f checks/verify_head.sql` | échoue volontairement sur « Extension pgvector absente » → le contrôle détecte bien l'absence |
| `psql -f 0003_down -f 0001_down -f checks/verify_base.sql` | OK : base vide après retour arrière |
| Analyse YAML + structure du workflow ; existence des actions `checkout@v5`, `setup-python@v6`, `setup-node@v5` (`git ls-remote`) | OK |

## 4. Validation sur votre PC (Docker, Alembic réel, pgvector)

<!-- DOCKER-RESULTS -->
### Exécution n° 1 — 18 novembre 2026, 14 h 34 (journal `docs/validation-logs/phase1.1-20261118-143400.log`)

Poste `KARAMBAGASSAMA`, Windows PowerShell 5.1, Docker 29.8.1 (client et serveur), Docker Compose v5.5.1, branche `main` @ `79ef00f`.

| Contrôle | Résultat |
|---|---|
| Docker Desktop actif, Docker Compose disponible | ✅ |
| `.env` créé avec secrets aléatoires ; 8/8 variables requises ; aucune valeur `change-me` | ✅ |
| Paiements désactivés, aucune clé IA payante | ✅ |
| `docker compose config` | ✅ |
| Design system sur votre poste (Node.js) | ✅ 58/58 |
| **`docker compose up -d`** | ❌ **`pull access denied for minio/minio`** — l'image n'existe plus sur Docker Hub ; le téléchargement des autres images a été interrompu (anomalie A9) |
| Tous les contrôles suivants (santé, PostgreSQL, persistance, Alembic, pgvector, pytest) | ❌ non exécutables, conséquence directe de l'échec précédent |
| « Aucun secret dans les journaux » | ✅ affiché, mais **sans valeur** : 0 Ko de journaux analysés. Le script a été corrigé pour échouer dans ce cas |

### Exécution n° 2 — après correction (RustFS)

**Non encore exécutée.** Docker (démarrage, santé, persistance), Alembic réel et pgvector restent **non vérifiés**.

## 5. État par composant

| Composant | État | Preuve |
|---|---|---|
| Schéma (60 tables, contraintes, FK, index) | ✅ vérifié sur PostgreSQL 16 | 44 tests |
| Migration 0003 (règles métier) | ✅ vérifiée (SQL) | 12 nouveaux tests |
| Alembic **réel** | <!-- ALEMBIC -->⏳ non vérifié — testé uniquement avec un substitut qui exécute le SQL de chaque révision | — |
| pgvector (0002) | <!-- PGVECTOR -->⏳ non vérifié | — |
| Docker Compose | <!-- DOCKER -->❌ puis corrigé : démarrage impossible avec MinIO (exécution n° 1) ; correction RustFS non encore exécutée | journal n° 1 |
| CI GitHub | ✅ commit `79ef00f` : 3 jobs réussis sur GitHub · ⏳ nouvelle CI (4 jobs, dont Alembic réel) validée localement uniquement | page Actions, run #1 |
| Design system | ✅ | 58 tests |

## 6. Anomalies détectées

| # | Anomalie | Gravité | Statut |
|---|---|---|---|
| A1 | **Les migrations Alembic auraient échoué en réel** : `op.execute(texte)` passe par `sqlalchemy.text()` et le protocole étendu de psycopg 3, qui refuse plusieurs instructions par requête et réinterprète les `%` (présents dans `format()` et `RAISE`). Le substitut de test ne pouvait pas le voir. | Bloquante | Corrigée (`exec_driver_sql`) — **à confirmer par une exécution réelle** |
| A2 | Un utilisateur **sans aucun rôle** pouvait être enregistré comme relecteur d'un contenu, validateur d'un métier, approbateur d'une source IA. | Élevée | Corrigée (0003) |
| A3 | N'importe quel utilisateur (même un relecteur ou un formateur d'un autre établissement) pouvait valider une compétence « en pratique ». | Élevée | Corrigée (0003) |
| A4 | Aucune isolation des contenus d'établissement : un module ou une question privés pouvaient être inclus dans un programme public ou d'un autre établissement ; une cohorte pouvait avoir un formateur d'un autre établissement. | Élevée | Corrigée (0003) |
| A5 | Avertissements GitHub : actions en Node.js 20, migration du libellé `ubuntu-latest`. | Faible | Corrigée (actions v5/v6, `ubuntu-24.04`) |
| A6 | `C:\NurseLearnAI` est une copie **hors Git**, figée à la Phase 1 ; le dépôt de travail est `C:\NurseLearnAI-git`. | Moyenne (confusion) | Signalée — à archiver par vous |
| A7 | Le dossier `.github` ne peut pas être écrit à distance sur votre PC : le nouveau `ci.yml` est dans la branche Git `phase-1.1`, pas encore dans `C:\NurseLearnAI-git`. | Faible | Ouverte (résolue par le push) |
| A9 | **L'image `minio/minio` n'existe plus sur Docker Hub** (« pull access denied ») : `docker compose up` échouait sur tout poste neuf. Constaté au premier lancement du script sur votre PC. | Bloquante | Corrigée : remplacement par RustFS `rustfs/rustfs:1.0.0` (compatible S3, Apache 2.0), health check ajouté, volume `s3data` |
| A8 | Isolation par établissement **à l'exécution** (un utilisateur ne lit que les données de son établissement) : impossible à vérifier, aucune API n'existe. | Élevée pour la Phase 2 | Ouverte — exigence du lot d'intégration |

## 7. Corrections apportées

- `database/schema/0003_business_rules.sql` (+ retour arrière) et révision Alembic `0003_business_rules` — **additives**, aucune table ni colonne modifiée, compatibilité du schéma préservée.
- Révisions Alembic 0001–0003 : exécution par `exec_driver_sql`.
- `database/checks/verify_head.sql` et `verify_base.sql` : contrôles réutilisés par la CI et par le script Windows.
- `database/tests/test_business_rules.py` : 12 nouveaux tests (RBAC, habilitations, validation pratique, isolation) ; tests existants adaptés (le relecteur doit désormais être habilité).
- `.github/workflows/ci.yml` : nouveau job **Alembic réel** (upgrade → contrôles → upgrade idempotent → données de référence ×2 → downgrade base → contrôle → upgrade → contrôles), test pgvector exécuté, actions à jour.
- `infra/docker-compose.yml` : MinIO remplacé par RustFS (service `s3`, health check `/health/ready`).
- `scripts/validate-phase1.1.ps1` (le contrôle des secrets échoue désormais si les journaux sont vides) ; `db-apply.ps1/.sh` incluent 0003 ; documentation mise à jour.

## 8. Fonctionnalités réellement disponibles (étape 6)

| Élément attendu | Existe ? | Ce qui existe réellement |
|---|---|---|
| Backend FastAPI exécutable | ❌ | `pyproject.toml` et migrations Alembic uniquement ; aucun code `app/` |
| Authentification | ❌ | Tables `users`, `auth_sessions`, rôles, permissions |
| API du catalogue | ❌ | Tables et données de référence |
| Frontend Next.js | ❌ | `apps/web/README.md` ; jetons de design et preset Tailwind prêts |
| Tableau de bord étudiant | ❌ | Tables de progression et de compétences |
| Sélection du pays et du métier | ❌ (données ✅) | 9 juridictions, 12 métiers en base ; aucun écran ni point d'API |
| Consultation des cours | ❌ | Aucun cours rédigé (module pilote prévu en DRAFT) |

**Conclusion : la Phase 1 est livrée sur le plan de la conception (analyse, architecture, données, design system) mais n'est pas fonctionnellement terminée : aucune application n'est exécutable.** Le cahier des charges initial plaçait ces éléments en Phase 2 ; le lot d'intégration proposé dans `PHASE2-READINESS.md` comble l'écart sans entamer les fonctionnalités avancées.

## 9. Risques restants

1. Alembic réel et pgvector non prouvés tant que le script Windows ou la CI de `phase-1.1` n'ont pas tourné.
2. Isolation par établissement à l'exécution : dépendra de l'API (filtrage systématique + tests d'accès croisé) ; la sécurité au niveau des lignes (RLS) PostgreSQL est à décider en Phase 2.
3. Aucun relecteur médical : aucun contenu ne pourra être publié ; le pilote restera en DRAFT.
4. Données réglementaires (12 métiers) toujours `pending_expert_review`.
5. Deux dossiers locaux (`C:\NurseLearnAI` et `C:\NurseLearnAI-git`) : risque de modifier la mauvaise copie.

## 10. Critères de passage en Phase 2

Voir [`PHASE2-READINESS.md`](PHASE2-READINESS.md).
