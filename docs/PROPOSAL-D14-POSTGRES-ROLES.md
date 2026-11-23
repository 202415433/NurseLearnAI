# Proposition — D14 : séparation des rôles PostgreSQL

> **Statut : proposition, non développée.** Elle devient une **condition BLOQUANTE avant 2.2.3-d** (diffusion des
> médias) et avant toute production. Jusqu'ici, D14 était prévue au lot 2.5 ([PHASE2.2.3-PLAN.md](PHASE2.2.3-PLAN.md) § D14).
> Références : [VALIDATION-PHASE2.2.3C.md](VALIDATION-PHASE2.2.3C.md) § 8 · [PHASE2.2.3D-READINESS.md](PHASE2.2.3D-READINESS.md).

## 1. Constat

En développement, l'API se connecte avec `nurselearn`, le compte créé par l'image Postgres : il est **superutilisateur et propriétaire de tous les objets**. Il peut donc :
- désactiver les déclencheurs (`ALTER TABLE … DISABLE TRIGGER`) ;
- passer en `session_replication_role = replica`, ce qui court-circuite tous les déclencheurs ;
- changer d'identité (`SET SESSION AUTHORIZATION nl_media_scanner`) ;
- modifier ou supprimer les fonctions de garde ;
- poser `nl.role_grant_bypass` à volonté.

Les invariants de 0003 à 0008 sont donc garantis **contre les erreurs**, mais **pas contre une API compromise**. La 2.2.3-c a séparé le worker antivirus (`nl_media_scanner`) ; D14 généralise ce principe.

## 2. Rôles proposés

| Rôle | Connexion | Propriétaire | Usage | Interdits |
|---|---|---|---|---|
| `postgres` (bootstrap) | Oui, administrateur | Cluster | Création du cluster et des rôles, maintenance | Jamais transmis à un conteneur applicatif |
| `nl_owner` | **NOLOGIN** | Schéma `public`, tables, fonctions, déclencheurs | Porte la propriété | Aucune connexion directe |
| `nl_migrator` | Oui, **ponctuelle** | Membre de `nl_owner` (`SET ROLE`) | Alembic et données de référence, dans un conteneur `migrate` lancé puis arrêté | Absent du conteneur API ; mot de passe distinct |
| `nl_api` | Oui | Rien | API FastAPI | Ni superutilisateur, ni `BYPASSRLS`, ni propriétaire, ni membre de `nl_owner` |
| `nl_media_scanner` | Oui | Rien | Worker antivirus (existe depuis 0008) | Inchangé : privilèges minimaux listés |

## 3. Permissions minimales de `nl_api` (principes)

- `USAGE` sur le schéma, **sans `CREATE`**.
- Privilèges **par table et par colonne**, établis à partir des requêtes réelles de l'API :
  - `SELECT`, `INSERT` et `UPDATE` ciblés ;
  - **aucun `DELETE`** sur les tables en ajout seul (`audit_logs`, `media_scan_events`, publications, versions figées) ;
  - **aucun `TRUNCATE`, `REFERENCES` ni `TRIGGER`**.
- Aucun privilège sur les colonnes d'analyse (`media_assets.state` vers les états d'analyse, `clean_key`, `scan_attempt`) : elles restent réservées à `nl_media_scanner`.
- `EXECUTE` seulement sur les fonctions appelées par l'API (`nl_media_expire_reservations`, etc.).
- `REVOKE EXECUTE … FROM PUBLIC` sur toute fonction `SECURITY DEFINER`.
- `ALTER DEFAULT PRIVILEGES FOR ROLE nl_owner` : tout nouvel objet est fermé par défaut, et chaque migration accorde explicitement ce qu'il faut.

## 4. Protection des fonctions et déclencheurs

- Toutes les fonctions et tous les déclencheurs appartiennent à `nl_owner`. Seul le propriétaire peut les modifier, les supprimer ou les désactiver.
- Fonctions `SECURITY DEFINER` : `SET search_path = public, pg_temp` obligatoire (déjà le cas en 0008), propriétaire `nl_owner`, `EXECUTE` accordé nommément.
- `nl.role_grant_bypass` : n'est accepté que si `current_user = 'nl_owner'` (migration ou données de référence). L'API ne peut plus l'utiliser.
- `session_replication_role` : réservé au superutilisateur depuis PostgreSQL 15, et n'est jamais accordé (`GRANT SET`).
- Contrôle automatique : `verify_head.sql` vérifie qu'aucun objet n'appartient à un rôle de connexion applicatif, et que `nl_api` n'est ni superutilisateur ni membre de `nl_owner`.

## 5. Mise en œuvre envisagée (lot dédié, à autoriser)

1. **Migration additive 0009** : rôles `nl_owner` et `nl_api` (`NOLOGIN` à la création, mots de passe posés hors dépôt), transfert de propriété vers `nl_owner`, privilèges de `nl_api`, durcissement de `nl.role_grant_bypass`. Retour arrière : propriété rendue, privilèges retirés.
2. **Docker Compose** : service `migrate` ponctuel (Alembic et données de référence, avec `nl_migrator`), puis `api` avec `nl_api`. Le superutilisateur reste réservé à l'initialisation du conteneur `postgres`.
3. **CI et Windows** : toute la suite API s'exécute **sous `nl_api`** ; les migrations sous `nl_migrator`.
4. Documentation d'exploitation : rotation des mots de passe et gestionnaire de secrets en production.

## 6. Tests de refus obligatoires (connectés en `nl_api`)

| Opération | Attendu |
|---|---|
| `ALTER TABLE media_assets DISABLE TRIGGER USER` | Refus (propriétaire requis) |
| `DROP TRIGGER` / `CREATE OR REPLACE FUNCTION nl_…` | Refus |
| `SET session_replication_role = replica` | Refus |
| `SET SESSION AUTHORIZATION` / `SET ROLE nl_owner` | Refus |
| `CREATE TABLE` dans `public` | Refus |
| `TRUNCATE` et `DELETE` sur `audit_logs` ou `media_scan_events` | Refus |
| `UPDATE media_assets SET state = 'clean'` ou `scan_attempt` | Refus |
| `INSERT INTO media_scan_events` | Refus |
| `nl.role_grant_bypass = on` + attribution d'un rôle plateforme | Refus |
| `EXECUTE nl_media_scanner_provision()` / `nl_media_scanner_identity()` | Refus |
| `COPY … TO PROGRAM`, `pg_read_server_files` | Refus |
| **Positif** : toute la suite API (330 tests et plus) | Réussie sous `nl_api` |

## 7. Notification des signatures ClamAV (correctif limité proposé)

**Constat (validation Windows 2.2.3-c).** `freshclam` met bien à jour les signatures, mais ne peut pas prévenir clamd : `Can't connect to clamd through /tmp/clamd.sock`. Dans l'image officielle, le socket local de clamd est `/tmp/clamd.sock`, alors que le volume partagé est monté sur `/run/clamav`. clamd recharge donc les signatures à sa vérification périodique (`SelfCheck`, 600 s).

**Effet.** Aucun sur la sécurité : pendant ce délai, des signatures de plus de 48 h donnent `signatures_stale` et jamais `clean`. Il en résulte au plus 10 minutes d'analyses en échec sûr après une longue période sans mise à jour.

**Correctif limité proposé (non implémenté).**
- Monter le volume `clamav-sock` sur **`/tmp`** dans `clamd` et `freshclam`, au lieu de `/run/clamav`. Seuls ces deux conteneurs le partagent ; aucun réseau supplémentaire.
- Validation : le journal de `freshclam` affiche `Clamd successfully notified about the update`.
- Retirer du script Windows la mise à jour explicite, qui échoue sur le verrou du journal tenu par le démon ; ne garder que `clamdscan --reload`.
