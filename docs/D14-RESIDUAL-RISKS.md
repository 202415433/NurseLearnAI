# D14 — Audit des risques résiduels R1, R2, R3

> **Audit et propositions seulement : rien n'est développé ici.** Aucune de ces corrections n'est nécessaire pour que D14
> atteigne son objectif (l'API n'est plus superutilisateur ; validation Windows 23/23).
> Références : [VALIDATION-D14-POSTGRES-ROLES.md](VALIDATION-D14-POSTGRES-ROLES.md) · [PHASE2.2.3D-READINESS.md](PHASE2.2.3D-READINESS.md).

## R1 — Migrations exécutées avec des privilèges élevés

**Identifiants réellement utilisés** (Docker Compose, `infra/docker-compose.yml`) :

| Conteneur | Connexion PostgreSQL | Secrets reçus |
|---|---|---|
| `postgres` | — (serveur ; crée `POSTGRES_USER` superutilisateur à l'initialisation) | `POSTGRES_PASSWORD` |
| **`migrate`** (ponctuel, s'arrête après usage) | `POSTGRES_USER` (**superutilisateur**) | `POSTGRES_PASSWORD`, `API_DB_PASSWORD`, `MEDIA_SCANNER_DB_PASSWORD` (pour les poser) |
| `api` | `nl_api` | `API_DB_PASSWORD` seulement (dans `DATABASE_URL`) |
| `media-worker` | `nl_media_scanner` | `MEDIA_SCANNER_DB_PASSWORD` seulement |

**Vérifié** :
- La configuration Compose est contrôlée en CI : `api` ne reçoit ni `POSTGRES_PASSWORD`, ni `API_DB_PASSWORD` seul, ni `MEDIA_SCANNER_DB_PASSWORD`.
- Sous Windows, le contrôle `api_connectee_en_nl_api_sans_secret_admin` réussit ; en base, seules des sessions `nl_api` et `nl_media_scanner` existent.
- Le conteneur `migrate` termine avec le code 0 et ne tourne plus ensuite. Le secret reste toutefois lisible dans sa configuration (`docker inspect`) : c'est le cas de tout conteneur.
- En CI, les migrations utilisent le compte `postgres` du service de test.

**Pourquoi le superutilisateur reste nécessaire.**
- `CREATE EXTENSION vector`, `pg_trgm` et `pgcrypto` (0001, 0002) : extensions non « trusted », réservées au superutilisateur.
- `CREATE ROLE` et `ALTER ROLE … NOSUPERUSER` (0008, 0009) : demandent au moins `CREATEROLE`, et le superutilisateur pour retirer `SUPERUSER`.
- `nl_enforce_ownership()` : `ALTER … OWNER TO nl_owner` exige d'être membre de `nl_owner` ou superutilisateur.

**Proposition : rôle de migration non superutilisateur `nl_migrator`.**
1. Amorçage par l'administration, **une seule fois** par cluster (procédure documentée, hors Alembic) :
   - extensions ;
   - rôles `nl_owner`, `nl_api`, `nl_media_scanner` ;
   - `CREATE ROLE nl_migrator LOGIN NOINHERIT` ;
   - `GRANT nl_owner TO nl_migrator WITH INHERIT FALSE, SET TRUE` (PostgreSQL 16) : le migrateur peut faire `SET ROLE nl_owner`, sans hériter de ses droits par défaut.
2. Alembic se connecte en `nl_migrator` et fait `SET ROLE nl_owner` : tout objet créé appartient d'emblée à `nl_owner`, et `nl_enforce_ownership()` devient un simple contrôle.
3. Les migrations qui touchent aux rôles (attributs, mots de passe) passent par l'amorçage administrateur, pas par Alembic.
4. Le conteneur `migrate` reçoit le secret de `nl_migrator`, jamais celui du superutilisateur. Celui-ci ne sert plus qu'à l'amorçage, avec un secret conservé hors ligne.
5. Tests :
   - `nl_migrator` ne peut ni `CREATE ROLE`, ni `ALTER SYSTEM`, ni `COPY … PROGRAM`, ni `SET ROLE` vers un superutilisateur ;
   - Alembic upgrade, downgrade et upgrade fonctionne sous `nl_migrator` ;
   - les objets créés appartiennent à `nl_owner`.

## R2 — Lecture des tables par l'API

**Matrice actuelle.** `nl_api` a `SELECT` sur **toutes** les tables du schéma (75) et sur la vue `media_usage_totals`. L'API n'en interroge réellement qu'une partie, relevée dans le code :

| Groupe | Tables lues par l'API (code actuel) | Données sensibles |
|---|---|---|
| Identité | `users`, `profiles`, `auth_sessions`, `one_time_tokens`, `invitations`, `user_roles`, `roles`, `role_permissions`, `organizations` | `users.email`, `users.password_hash`, `auth_sessions.refresh_token_hash`, `ip`, `user_agent`, `one_time_tokens.token_hash`, `invitations.email`, `token_hash` |
| Contenus | `programs`, `program_releases`, `program_release_modules`, `program_release_prerequisites`, `modules`, `module_versions`, `module_version_lessons`, `module_version_lesson_versions`, `lessons`, `lesson_versions`, `lesson_version_references`, `medical_references`, `editorial_decisions`, `content_issue_reports`, `professions` et référentiels | Faibles (contenus éditoriaux, empreintes) |
| Médias | `media_assets` (+ quotas via la vue) | `quarantine_key`, `clean_key` (non exposées par l'API) |
| Apprentissage | `learning_events`, `student_progress` | Données d'apprentissage personnelles |
| **Non lues aujourd'hui** | `audit_logs`, `media_scan_events`, `ai_*`, `answers`, `attempt_*`, `payments`, `subscriptions`, `plans`, `badges`, `learning_resources`… | `audit_logs.ip`, `user_agent` ; `ai_messages.content` ; `payments` et `subscriptions` |

**Isolation entre établissements.** Elle est appliquée par l'API : `scope_params` et `can_read`, avec une réponse 404 hors portée. Elle est vérifiée par les tests d'isolation de 2.1, 2.2.2-b et 2.2.3-b, tous réussis sous `nl_api`. PostgreSQL ne l'impose pas : pas de RLS. Une API compromise peut donc lire les données des autres établissements, mais pas les modifier hors des gardes.

**Réduction progressive proposée.**
1. **Étape 1, sans risque fonctionnel** : retirer `SELECT` sur les tables non lues (`audit_logs`, `media_scan_events`, `ai_*`, `payments`, `subscriptions`, `attempt_*`…). Chaque phase future accordera ses lectures dans sa propre migration. Test de non-régression : la suite API complète sous `nl_api`.
2. **Étape 2, colonnes** :
   - `users.password_hash` lisible seulement via une fonction `SECURITY DEFINER` `nl_verify_password_hash(email)` qui renvoie l'empreinte de l'utilisateur demandé, ou via des privilèges de colonne ;
   - `auth_sessions.refresh_token_hash` et `one_time_tokens.token_hash` interrogés par égalité dans des fonctions dédiées, plutôt que par `SELECT` libre ;
   - `media_assets.quarantine_key` et `clean_key` non lisibles par l'API : elles ne servent qu'au worker. L'API garde l'écriture de `quarantine_key`.
3. **Étape 3, RLS** sur les tables multi-établissements (`organizations`, `user_roles`, `media_assets`, contenus d'établissement), avec une politique fondée sur l'identité vérifiée. Elle suppose de traiter d'abord le risque « acteur déclaré » (jeton signé vérifié en base), sinon la RLS ne ferait que répéter la confiance accordée à `nl.actor_id`.

Chaque étape doit être accompagnée de tests de refus sous `nl_api`, de la suite complète et de la validation Windows.

## R3 — Clé S3 partagée entre l'API et le worker

> **Statut : implémenté** sur la branche `security-r3-s3-identity-separation` (trois identités RustFS, conteneur `s3-init`, clé racine réservée au provisionnement, tests adversariaux sur RustFS réel). Détails, matrice définitive et risques restants R3-1 à R3-4 : [VALIDATION-R3-S3-IDENTITIES.md](VALIDATION-R3-S3-IDENTITIES.md). La proposition ci-dessous est conservée pour l'historique ; écarts retenus : `HeadObject` relève de `s3:GetObject` en IAM, l'API n'a pas `HeadBucket` (403 toléré), et l'identité de lecture n'est remise à aucun service avant 2.2.3-d.

**Opérations S3 nécessaires** (code actuel : `app/domains/media/storage.py`, worker) :

| Opération | API | Worker | Diffusion (2.2.3-d, à venir) |
|---|---|---|---|
| `s3:CreateBucket` / `HeadBucket` (création privée) | quarantaine | compartiment sain | — |
| `s3:PutObject` | quarantaine | compartiment sain | — |
| `s3:GetObject` | — | quarantaine | compartiment sain |
| `s3:HeadObject` | — | compartiment sain (contrôle de l'empreinte) | compartiment sain |
| `s3:DeleteObject` | quarantaine (objet qu'elle vient d'écrire, si l'écriture en base échoue) | compartiment sain (sa propre copie, si le verdict n'est pas enregistré) | — |
| `s3:ListBucket` | — | les deux (réconciliation) | — |

**Constat.** Une seule clé (`S3_ACCESS_KEY`, qui est la clé racine RustFS en développement) sert à l'API et au worker. Une API compromise pourrait écrire dans le compartiment sain ou lire la quarantaine.

**Compatibilité RustFS.** RustFS intègre un système IAM calqué sur le langage de politiques AWS : utilisateurs, groupes, politiques et comptes de service. Les identifiants racine (`RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`) contournent l'évaluation des politiques ; ils sont destinés à l'amorçage et à l'administration d'urgence. Les utilisateurs se créent par l'API d'administration (`PUT /rustfs/admin/v3/add-user`) ou par l'outil `rc` (`rc admin user add`, `rc admin policy create`, `rc admin policy attach`). Sources : [RustFS — IAM](https://docs.rustfs.com/en/security-compliance/iam), [RustFS — utilisateurs, groupes et politiques](https://docs.rustfs.com/administration/iam/policies).

**Proposition.**
- **Trois identités RustFS** (et non plus la clé racine) :
  - `nl-api-uploader` : `s3:PutObject` et `s3:DeleteObject` sur `nl-media-quarantine/*`, plus `HeadBucket` ;
  - `nl-media-scanner` : `GetObject` et `ListBucket` sur la quarantaine ; `PutObject`, `HeadObject`, `DeleteObject` et `ListBucket` sur `nl-media-clean` ;
  - `nl-media-reader` (2.2.3-d) : `GetObject` et `HeadObject` sur `nl-media-clean/*` seulement.
- **Compartiments créés par l'amorçage** avec la clé racine, au lieu d'être créés à la volée : on retire `CreateBucket` aux applications.
- **Amorçage** par un conteneur ponctuel `s3-init`, sur le modèle de `migrate` : création des compartiments, politiques et utilisateurs, avec des secrets `S3_API_*`, `S3_SCANNER_*` et `S3_READER_*` dans `.env`. La clé racine n'est fournie qu'à ce conteneur.
- **Tests de refus préparés**, à écrire avec le lot :

  | Identité | Opération | Attendu |
  |---|---|---|
  | `nl-api-uploader` | `GetObject` en quarantaine, `PutObject` dans le compartiment sain, `ListBucket` | 403 |
  | `nl-media-scanner` | `PutObject` en quarantaine, `DeleteObject` en quarantaine | 403 |
  | `nl-media-reader` | toute écriture, toute lecture de la quarantaine | 403 |
  | Anonyme | tout accès | 403 (déjà testé) |
  | Toutes | opérations légitimes de téléversement, d'analyse et de réconciliation | réussies (suites existantes) |

- **Prérequis de 2.2.3-d** : la diffusion ne doit jamais s'appuyer sur une clé capable d'écrire.
