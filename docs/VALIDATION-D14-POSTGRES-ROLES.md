# Validation — D14 : séparation des rôles PostgreSQL (migration 0009)

**Branche** : `security-d14-postgres-role-separation`, à partir de `main` = `2d2ee41` (fusion de la PR #17, 2.2.3-c).
**Références** : [proposition D14](PROPOSAL-D14-POSTGRES-ROLES.md) · [préparation 2.2.3-d](PHASE2.2.3D-READINESS.md) ·
[validation 2.2.3-c](VALIDATION-PHASE2.2.3C.md) · [ADR-0011](adr/0011-medias-antivirus.md).

**Périmètre.** Correctif D14 uniquement.
- Aucune fonctionnalité 2.2.3-d ou 2.2.3-e, aucune route.
- Aucune migration historique modifiée ; une migration **additive** `0009_postgres_role_separation`.
- Aucune donnée ni aucun volume supprimé ; aucune publication médicale ; aucun service payant.

## 1. Audit préalable (état de `main` avant D14)

| Point | Constat | Risque |
|---|---|---|
| Comptes utilisés | L'API, Alembic, les données de référence, la CLI d'exploitation et les tests utilisent **le même** `DATABASE_URL` : le compte créé par l'image Postgres (`nurselearn` sous Docker, `postgres` en CI), **superutilisateur**. Seul le worker antivirus (`nl_media_scanner`, 0008) est distinct | **Élevé** |
| Droits de l'API | Superutilisateur : désactiver les déclencheurs, `session_replication_role = replica`, `SET SESSION AUTHORIZATION`, remplacer ou supprimer les fonctions de garde, lire les fichiers du serveur, `COPY … TO PROGRAM` | **Élevé** |
| Droits du worker | Conformes à 0008 : non superutilisateur, privilèges listés, verdicts liés à `session_user` | Faible |
| Propriétaires | Toutes les tables, séquences, vues et fonctions appartiennent au compte de migration (superutilisateur). Le schéma `public` appartient à `pg_database_owner`. Extensions : superutilisateur | Élevé (lié au précédent) |
| `CREATE`, `TEMP`, `TRIGGER`, `GRANT`, `SET ROLE` | `PUBLIC` : `CONNECT` et `TEMPORARY` sur la base, `USAGE` sur `public` (sans `CREATE`, PostgreSQL ≥ 15). Le superutilisateur peut tout | — |
| `SECURITY DEFINER` | 3 fonctions (0008), correctes : `search_path` fixé, `EXECUTE` nominatif | Faible |
| Variables de session décisives | `nl.actor_id` (identité déclarée par l'API) ; `nl.role_grant_bypass` (attribution de rôles plateforme, **activable par l'API**) ; `nl.editorial_log` (insertion directe de décisions éditoriales, **activable par l'API**) ; `nl.decision_comment` (motif, sans enjeu) | **Élevé** pour les deux contournements |
| `pg_trigger_depth()` | Avec le droit `TEMPORARY`, une connexion peut créer une table temporaire munie d'un déclencheur et fabriquer une profondeur de déclencheur | Moyen |

**Écarts par rapport à la proposition D14 initiale.**
1. **Pas de rôle `nl_migrator` distinct du superutilisateur.** `CREATE EXTENSION` et `CREATE ROLE` (0002, 0008, 0009) exigent l'administration du cluster. Les migrations s'exécutent donc avec le compte d'administration, **dans le seul conteneur ponctuel `migrate`**, jamais dans l'API ni dans le worker. Un migrateur non superutilisateur reste une amélioration de production (§ 8).
2. **La propriété est rétablie après chaque exécution d'Alembic** par `nl_enforce_ownership()`, plutôt que par un `SET ROLE` pendant les migrations, que les migrations créant des rôles ne permettent pas.
3. **Deux contournements par variable de session découverts et corrigés** : `nl.role_grant_bypass` et `nl.editorial_log`. Le droit `TEMPORARY` est retiré pour supprimer le contournement de `pg_trigger_depth()`.

## 2. Architecture des rôles

| Rôle | Connexion | Privilèges | Utilisé par |
|---|---|---|---|
| Administration du cluster (`POSTGRES_USER`) | Oui | Superutilisateur | Conteneur `postgres` (initialisation), conteneur **`migrate`** (Alembic, données de référence, mots de passe applicatifs), CLI d'exploitation (`docker compose run --rm migrate python -m app.cli …`) |
| `nl_owner` | **Non** (NOLOGIN) | Propriétaire de **tout** le schéma `public` ; aucun membre, donc aucun `SET ROLE` possible | Propriété ; exécution des 5 fonctions `SECURITY DEFINER` |
| `nl_api` | Oui | Ni superutilisateur, ni `CREATEDB`, ni `CREATEROLE`, ni `BYPASSRLS`, ni héritage ; propriétaire de rien, membre de rien ; privilèges par table et par colonne | API FastAPI |
| `nl_media_scanner` | Oui | Inchangé (0008) | Worker antivirus |

**Privilèges de `nl_api`.** Ils sont tirés de l'inventaire des écritures de l'API, puis vérifiés par toute sa suite de tests exécutée sous `nl_api`.
- `SELECT` sur les tables ; `USAGE` et `SELECT` sur les séquences.
- `INSERT` et `UPDATE` sur les comptes, sessions, jetons, invitations et établissements.
- `INSERT` et `DELETE` sur `user_roles`.
- `INSERT` et `UPDATE` sur les contenus éditoriaux ; `DELETE` sur les brouillons et liaisons. Le workflow et l'immutabilité restent imposés par les déclencheurs.
- `INSERT` seulement sur `audit_logs` et `editorial_decisions`.
- `media_assets` : `INSERT`, et `UPDATE` sur les colonnes de réservation, réception, métadonnées et suppression logique. **Jamais** `scan_attempt`, `clean_key`, `clean_at`, `access_epoch`, `purged_at`, `legal_hold` ni `legal_hold_reason`.
- **Aucune écriture** sur `roles`, `permissions`, `role_permissions`, les référentiels, `media_usage` (quotas), `media_scan_events` (verdicts) et les tables des phases futures.
- **Jamais** `TRUNCATE`, `REFERENCES` ni `TRIGGER` ; ni `CREATE` sur le schéma, ni `CREATE` ou `TEMPORARY` sur la base.

## 3. Modifications SQL (migration 0009, additive)

1. **Rôles** : création de `nl_owner` (NOLOGIN) et de `nl_api` (NOLOGIN tant que l'exploitation ne pose pas de mot de passe). Attributs dangereux retirés ; toute appartenance révoquée.
2. **Propriété** : `nl_enforce_ownership()` transfère schéma, tables, vues, séquences, fonctions et types à `nl_owner`, hors objets d'extension. Elle est rappelée par `alembic/env.py` à la fin de **chaque** migration ; `EXECUTE` retiré à `PUBLIC`.
3. **Base et schéma** : `REVOKE ALL` à `PUBLIC` ; `CONNECT` nominatif ; `USAGE` sur `public` seulement.
4. **Fonctions** : `nl_media_reserve` et `nl_media_usage_row` passent en `SECURITY DEFINER`.
   - Justification : verrou `FOR UPDATE` et création de la ligne de quota par défaut sans aucun droit d'écriture de l'API sur `media_usage`.
   - `search_path = public, pg_temp`.
   - `EXECUTE` limité à `nl_api` (réservation) et à personne (ligne de quota).
5. **Gardes** :
   - `nl.role_grant_bypass` n'est accepté que d'une session privilégiée : superutilisateur, ou `nl_owner` via `SECURITY DEFINER`. `current_user` ne se déclare pas.
   - Une décision éditoriale n'est écrite que depuis le déclencheur de transition (`pg_trigger_depth() ≥ 2`), profondeur que `nl_api` ne peut plus fabriquer.
6. **Retour arrière exact** : propriétés rendues au compte d'administration, schéma à `pg_database_owner`, droits `PUBLIC` d'origine, fonctions de quota en `SECURITY INVOKER`. Les rôles de cluster sont conservés.

**Déploiement (Docker Compose).**
- Nouveau service ponctuel `migrate` : Alembic, puis données de référence, puis mots de passe de `nl_api` et `nl_media_scanner`, puis arrêt.
- L'API démarre après sa réussite, avec `DATABASE_URL = nl_api` ; elle **ne reçoit plus** `POSTGRES_PASSWORD`.
- `up --wait` échoue quand un conteneur ponctuel se termine : la CI et les scripts attendent la santé explicitement.
- La CLI `grant-platform-admin` s'exécute via `docker compose run --rm migrate`.

## 4. Gestion des secrets

| Secret (`.env`, jamais versionné) | Conteneurs |
|---|---|
| `POSTGRES_PASSWORD` (administration) | `postgres`, `migrate` |
| `API_DB_PASSWORD` (`nl_api`, **obligatoire**) | `api` (dans son `DATABASE_URL`), `migrate` (qui le pose) |
| `MEDIA_SCANNER_DB_PASSWORD` | `media-worker`, `migrate` |

- Le script Windows génère les valeurs manquantes (48 caractères hexadécimaux), sans les afficher.
- Les journaux sont masqués, et un contrôle vérifie qu'aucun secret n'y figure.
- En développement, si `TOKEN_HASH_KEY` est vide, la clé est dérivée de `DATABASE_URL` : changer de connexion invalide les sessions et jetons en cours, et il faut se reconnecter.
- En production, `TOKEN_HASH_KEY` est obligatoire et les secrets passent par un gestionnaire.

## 5. Tests

**Tests adversariaux** (`database/tests/test_postgres_roles.py`, 15 tests) sur des **connexions réelles** `nl_api`. L'API ne peut pas :

| # | Opération | Résultat |
|---|---|---|
| 1 | Désactiver un déclencheur (`DISABLE TRIGGER`, `session_replication_role`) | Refusé (`must be owner`, `permission denied`) |
| 2 | Modifier, remplacer ou supprimer une fonction protégée | Refusé |
| 3 | Modifier, supprimer ou insérer un verdict antivirus | Refusé |
| 4 | Forcer `clean` (`clean_key`, `state`, `scan_attempt`) | Refusé (privilège de colonne ; garde de session de service) |
| 5 | Modifier ou supprimer un quota, créer une ligne de quota | Refusé |
| 6 | Effacer, vider ou modifier l'audit | Refusé |
| 7 | S'attribuer `media:scan`, `admin` ou `medical_reviewer` (même avec `nl.role_grant_bypass`) | Refusé |
| 8 | Modifier une édition figée ; forger une décision éditoriale (`nl.editorial_log`) | Refusé |
| 9 | Devenir propriétaire d'une table ou du schéma | Refusé |
| 10 | `SET ROLE` ou `SET SESSION AUTHORIZATION` vers `nl_owner`, `nl_media_scanner` ou le superutilisateur | Refusé |
| 11 | Créer table, table temporaire, fonction `pg_temp` ou schéma | Refusé |
| 12 | Contourner par déclencheur ou fonction (`CREATE TRIGGER`, fonctions propriétaires et de service, `COPY … PROGRAM`, `pg_read_file`) | Refusé |

S'y ajoutent l'inventaire des rôles, des propriétaires, des 5 fonctions `SECURITY DEFINER` et des privilèges.

**Opérations légitimes.**
- **API** : transition éditoriale avec décision écrite par le déclencheur, réservation de média (quota par la fonction propriétaire), suppression logique, audit, comptes.
- **Worker** : cycle d'analyse jusqu'à `clean`, sans accès à l'audit.

**Installations.**
- Neuve : 0008 → 0009 → retour → 0009, avec un schéma identique à l'octet près.
- Existante (0008 avec données) : migration et retour sans perte.

**Résultats locaux** (PostgreSQL 16 réel, Redis réel ; S3 et clamd simulés localement) :

| Suite | Résultat |
|---|---|
| Base de données (dont 15 D14) + chaîne de migrations | **176 réussis** |
| **API complète, application connectée en `nl_api`**, fixtures en administration | **187 réussis**, 1 ignoré (Mailpit) |
| Démarrage d'uvicorn en `nl_api` | `/ready` : base `ok` ; catalogue servi ; seules des sessions `nl_api` en base |
| Alembic réel : upgrade, `verify_head` (contrôles D14 inclus), downgrade base, `verify_base`, upgrade | OK ; `alembic_version` appartient à `nl_owner` |
| Sonde `scripts/d14_api_probe.py` | **20/20 refusées** en `nl_api` (13 sur 20 acceptées en superutilisateur, ce qui confirme que la sonde discrimine) |
| Ruff, format, mypy | OK |

**CI**.
- L'API s'exécute en `nl_api` dans le job API.
- Le job Docker Compose vérifie : `migrate` terminé avec le code 0, `verify_head`, sessions `nl_api`, déclencheur non désactivable depuis le conteneur API, aucun identifiant d'administration dans l'API.

**Windows** : `scripts/validate-d14-postgres-roles.ps1`.

**Premier passage** (22/11/2026, commit `11215d2`) : **21/23**. Les deux échecs relèvent du test et du script ; aucune protection n'est en cause.

Résultats sur une installation **existante** :
- `0008` → `0009`, avec `nurselearn` superutilisateur et propriétaire de tout avant la migration.
- Sauvegarde de 461 950 octets (76 tables de données).
- Conteneur `migrate` terminé avec le code 0 ; `verify_head` réussi avec les contrôles D14.
- Rôles sans privilège ; aucune appartenance ; tout le schéma appartient à `nl_owner` ; 5 fonctions `SECURITY DEFINER` verrouillées.
- API en `nl_api` sans identifiant d'administration ; sessions en base : `nl_api` et `nl_media_scanner` seulement.
- **Sonde dans le conteneur API : 20/20 refusées.**
- **Aucune donnée perdue** (compteurs avant 31/31/1/1/8/0/0/0/0/191, après 34/34/1/1/8/0/0/0/0/210 : nouvelles données des scripts).
- **ClamAV réel et EICAR : 11 + 62 tests réussis.**
- Pytest complet sous `nl_api` : 344 réussis, 1 échec.

Les deux échecs, et leur correction :
1. **Échec pytest** : le test visait le rôle `postgres`, qui n'existe pas sous Docker (le superutilisateur y est `nurselearn`). Le test vise désormais le superutilisateur réel du cluster.
2. **Contrôle des privilèges** : la requête par nom de table était évaluée sur des tables du catalogue système (`public.pg_statistic` inexistante). Elle passe désormais par OID, sans résolution de nom.

**Second passage** (23/11/2026, commit `9fddea9`) : **23/23 contrôles D14 réussis, 0 échec**, après une non-régression complète sans échec (2.2.3-c 30/30 → 1.2).

- **Pytest complet, application connectée en `nl_api`** : **345 réussis**, 0 échec. Les 19 tests ignorés sont ceux du worker, sans ClamAV dans cette exécution ; ils sont exécutés juste après.
- **ClamAV réel et EICAR** : 11 + 62 tests réussis, aucun ignoré.
- Sauvegarde préalable de 491 977 octets (76 tables de données).
- Conteneur `migrate` terminé avec le code 0 ; `verify_head` réussi avec les contrôles D14.
- Rôles sans privilège ; aucune appartenance ; tout le schéma appartient à `nl_owner` ; 5 fonctions `SECURITY DEFINER` verrouillées.
- **Privilèges de l'API minimaux** : aucune table avec `TRUNCATE`, `TRIGGER` ou `REFERENCES` ; ni `CREATE`, ni `TEMP` ; ni verdicts, ni quotas, ni permissions, ni effacement d'audit, ni `clean_key`.
- API en `nl_api` sans identifiant d'administration ; sessions en base : `nl_api` et `nl_media_scanner` seulement.
- **Sonde dans le conteneur API : 20/20 refusées.**
- **Aucune donnée perdue** : compteurs 34/34/…/210 avant, 37/37/…/229 après, seuls les ajouts des scripts.
- Aucun programme médical publié ; pilote PAB en brouillon ; aucune route de diffusion ajoutée.

## 6. Processus de migration d'une installation existante et reprise

Étapes de `validate-d14-postgres-roles.ps1` :
1. **Inventaire** : migration, rôles, propriétaires, volumes de 10 tables.
2. **Sauvegarde** `pg_dump -Fc` vers `backups\<base>-avant-d14-<date>.dump` (hors Git), contrôlée par `pg_restore -l`.
3. Le conteneur `migrate` applique 0009 : **rôles créés, propriétés transférées, privilèges minimaux, gardes**.
4. **Vérifications** :
   - connexions réellement utilisées ;
   - fonctions et déclencheurs ;
   - sonde adversariale depuis le conteneur de l'API ;
   - aucune donnée perdue (aucun compteur ne diminue).

**Reprise en cas d'échec.**

Dans l'ordre de préférence :
1. **Retour arrière exact, sans toucher aux données** :
   `docker compose run --rm migrate alembic downgrade 0008_media_scanner_role`, puis revenir au code de `main` (`git checkout main`) et `docker compose up -d --build`.
2. **Dernier recours : restauration de la sauvegarde.** Elle remplace l'état courant par celui de la sauvegarde.
   1. `docker compose stop api media-worker web`.
   2. `docker compose cp backups\<fichier>.dump postgres:/tmp/restore.dump`.
   3. `docker compose exec -T postgres pg_restore -U nurselearn -d nurselearn --clean --if-exists /tmp/restore.dump`.
   4. Revenir au code de `main`.

Aucun rôle n'est supprimé : `nl_owner` et `nl_api` sont conservés, et sans droits sur la base après retour arrière.

## 7. Risques résiduels

| Risque | Niveau | Traitement |
|---|---|---|
| L'identité **déclarée** par l'API (`nl.actor_id`) reste crue par la base : une API compromise peut agir au nom de n'importe quel utilisateur, **dans les limites de `nl_api`**. Elle ne peut ni produire un verdict, ni s'attribuer un rôle plateforme, ni toucher aux quotas, à l'audit ou aux gardes | Moyen | Inhérent à une application à connexion unique. Atténuation future : jeton d'acteur signé vérifié en base, ou rôles par fonction |
| Migrations exécutées en superutilisateur (conteneur `migrate`) | Moyen (production) | Migrateur non superutilisateur une fois les extensions installées par l'administration ; conteneur `migrate` lancé à la demande |
| `nl_api` peut lire toutes les tables (dont les empreintes de mots de passe, nécessaires à la connexion) | Faible | Lecture par colonnes à affiner lors d'un lot ultérieur |
| Les tables des phases futures n'ont aucun droit d'écriture pour l'API | — | Volontaire : chaque phase accordera explicitement ses droits dans sa migration |
| Une seule clé S3 pour l'API et le worker | Moyen | Avant 2.2.3-d (voir sa préparation) |

## 8. Conditions de production

- `API_DB_PASSWORD`, `MEDIA_SCANNER_DB_PASSWORD`, `POSTGRES_PASSWORD` et `TOKEN_HASH_KEY` stockés dans un gestionnaire de secrets, avec rotation.
- Le compte d'administration n'est jamais fourni à un conteneur applicatif.
- `verify_head.sql` est exécuté après chaque déploiement : il échoue si un objet n'appartient pas à `nl_owner`, si `nl_api` gagne un privilège interdit, ou si une fonction `SECURITY DEFINER` est ouverte à `PUBLIC`.
- Chaque nouvelle migration doit accorder explicitement ses privilèges à `nl_api`, sans rien accorder par défaut.
- Sauvegarde vérifiée avant chaque migration.
