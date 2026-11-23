# Validation — Étape 2.2.3-c : antivirus ClamAV et analyse asynchrone

**Branche** : `phase-2.2.3c-antivirus-worker`, à partir de `main` = `60f2ef8` (fusion de la PR #16, 2.2.3-b).
**Références** : [plan 2.2.3](PHASE2.2.3-PLAN.md) § 8 · [préparation 2.2.3-c](PHASE2.2.3C-READINESS.md) ·
[validation 2.2.3-b](VALIDATION-PHASE2.2.3B.md) · [ADR-0011](adr/0011-medias-antivirus.md) · D7, D15–D24, S1–S14.

**Périmètre.**
- Migration additive 0008 : rôle de service.
- ClamAV dans Docker Compose (profil `media`).
- Worker d'analyse asynchrone.
- Réconciliation.

**Hors périmètre, non développé.**
- Toute diffusion (2.2.3-d) et toute interface (2.2.3-e).
- Aucune route ajoutée à l'API.
- Aucun passage manuel à `clean`.
- Aucune migration historique modifiée.
- Aucune donnée ni aucun volume supprimé.
- Aucune publication médicale ; le pilote PAB reste en brouillon.

## 1. Séparation des privilèges (migration 0008)

Un verdict antivirus exige **deux identités distinctes**, toutes deux vérifiées par PostgreSQL.

| Identité | Nature | Garanties |
|---|---|---|
| `nl_media_scanner` | Rôle de **connexion** PostgreSQL (objet de cluster), authentifié par mot de passe | Non superutilisateur ; ni `CREATEDB`, ni `CREATEROLE`, ni `BYPASSRLS`, ni héritage ; membre d'aucun rôle. Créé `NOLOGIN` par la migration ; le mot de passe est posé hors dépôt (`MEDIA_SCANNER_DB_PASSWORD`, appliqué par `python -m app.seed`) |
| `media_scanner` | Rôle **métier** (table `roles`) | Seul détenteur de `media:scan`, et ne détient rien d'autre. Attribué à un unique compte de service (`00000000-0000-4000-a000-00000000c1a5`) : global, sans mot de passe, sans session API, non invitable, sans autre rôle |

Gardes ajoutées, toutes additives. Les déclencheurs « 05 » s'exécutent avant les gardes 0007, qui restent appliquées.

- **Transitions d'analyse et verdicts** : `nl_is_media_scanner_session()` exige à la fois :
  - `session_user = 'nl_media_scanner'`, établi par l'authentification PostgreSQL et non déclaré par le client ;
  - l'acteur déclaré égal au compte de service.

  Un acteur ou un nom de rôle fournis par un client **ne suffisent jamais**. L'API, même en déclarant l'acteur de service, est refusée.
- **La connexion de service ne peut rien faire d'autre** :
  - aucune autre identité, aucune modification hors transition d'analyse ;
  - privilèges SQL **exhaustivement listés** :
    - `SELECT` sur `media_assets`, `media_scan_events` et `role_permissions` ;
    - `SELECT` sur `users(id, status, deleted_at)` et sur `user_roles(user_id, role_id, organization_id)` ;
    - `UPDATE` sur `media_assets(state, scan_attempt, clean_key)` ;
    - `INSERT` sur les colonnes du verdict ;
    - deux fonctions.
  - Pas d'e-mail ni d'empreinte de mot de passe, pas de journal d'audit, pas de contenus pédagogiques.
  - Pas de suppression, de purge, ni de conservation légale.
- **Audit de service** : `nl_media_scanner_audit()`, une fonction propriétaire qui n'ouvre aucun accès à `audit_logs`.
  - Actions limitées à `media.scan_*` et `media.reconcile_*`.
  - Métadonnées plates et bornées (2 000 caractères).
- **Journal des verdicts en ajout seul** : gardes 0007 conservées (UPDATE, DELETE et TRUNCATE refusés).
- **`clean` uniquement après un verdict valide** : gardes 0007 conservées et complétées.
  - Même tentative, même SHA-256.
  - Signatures datées de moins de 48 h.
  - Moteur et version des signatures renseignés.
  - Copie saine posée seulement lors du passage `scanning → clean`.

**Base vide et base existante.**
- Base vide : migration → retour arrière → migration, avec un schéma identique à l'octet près.
- Base 0007 avec des médias : migration sans perte ; retour arrière exact tant qu'aucune analyse n'existe, **refusé** dès qu'un verdict est enregistré.
- Le compte de service est créé par les données de référence (`0003_media_scanner_account.sql`) dès que les langues existent.

## 2. ClamAV dans Docker (profil `media`)

Démarrage : `docker compose --profile media up -d`. Image officielle `clamav/clamav:1.5.5` (GPL-2.0, gratuite), signatures préchargées.

| Service | Rôle | Réseau | Limites | Santé |
|---|---|---|---|---|
| `freshclam` | Mise à jour des signatures (12 fois par jour) | `clamav-updates` uniquement | 1 Go, 0,5 CPU | Fichier `daily` présent |
| `clamd` | Analyse (`INSTREAM`) | `media-scan`, **interne** : ni Internet, ni base, ni stockage | 3 Go, 1 CPU | `clamdcheck.sh` |
| `media-worker` | Analyse et verdicts | `default` + `media-scan` | 1 Go, 0,5 CPU, système de fichiers en lecture seule, `/tmp` en mémoire (450 Mo), `cap_drop: ALL` | Battement de cœur de moins de 2 min |

Dispositions communes :
- Aucun port ClamAV n'est publié. Politique `restart: unless-stopped`.
- Volumes dédiés : `clamav-db` pour les signatures (jamais supprimé) et `clamav-sock` pour le rechargement de clamd.
- ClamAV n'a **aucun identifiant S3** ni accès au stockage. Il reçoit les octets du worker, et seul le worker écrit dans le compartiment sain.

**Signatures absentes ou périmées.**
- La réponse `VERSION` de clamd est analysée : moteur, version et date des signatures.
- Date illisible ou de plus de 48 h : `scan_failed` (`signatures_stale`), jamais `clean`.
- La base revérifie indépendamment, par la contrainte `ck_media_scan_clean_traceable` et la garde de transition.

**Mémoire.** Ordre de grandeur documenté : environ 1,1 à 1,5 Go pour clamd, signatures chargées, avec un pic d'environ deux fois cette valeur au rechargement. Mesure réelle : voir § 7 (Windows) et l'étape « Profil media » du job CI Docker Compose (`docker stats`). Docker Desktop doit allouer **au moins 6 Go** à WSL 2.

## 3. Architecture du worker

```
PostgreSQL (vérité) ──► réconciliation ──► Redis Streams « nl:media:scan » (groupe media-scanners)
                                                │
                     ┌──────────────────────────┘
                     ▼
   prise en charge : UPDATE … SET state='scanning', scan_attempt+1 WHERE state IN (quarantined | scan_failed échu)
                     ▼
   RustFS quarantaine ──► fichier temporaire 0700 ──► taille + SHA-256 = valeurs en base ?
                     ▼
   clamd VERSION (signatures) ──► clamd INSTREAM ──► « stream: OK » | « … FOUND » | autre = erreur
                     ▼
   clean : copie vers nl-media-clean (clé aléatoire) + vérification de l'empreinte
                     ▼
   UNE transaction : verdict (media_scan_events) + transition + audit ── échec → retrait de SA copie
```

Correspondance avec la mission 4 :

| Exigence | Mise en œuvre |
|---|---|
| 1. Médias en attente | `Store.pending()` : `quarantined`, et `scan_failed` échus (attente exponentielle, au plus 6 tentatives) |
| 2. Redis | Streams + groupe de consommateurs ; déduplication à l'émission ; reprise des messages orphelins (`XAUTOCLAIM`) |
| 3. Quarantaine | `download_quarantine_sync` : lecture bornée à la taille enregistrée + 1 octet |
| 4. Identité et SHA-256 | Taille et empreinte relues ; un écart donne `integrity_mismatch` (jamais analysé ni validé) |
| 5. ClamAV | Client `INSTREAM` sans dépendance ; seule la réponse exacte `stream: OK` vaut « sain » |
| 6. Événement traçable | Moteur, version et date des signatures, menace, empreinte, durée, tentative ; analyste imposé par la base |
| 7. Transition | `UPDATE … WHERE state = 'scanning' AND scan_attempt = tentative` : une tentative perdue est refusée |
| 8. Copie saine | Octets **analysés** (et non une copie serveur à serveur), clé aléatoire, `application/octet-stream`, empreinte vérifiée **avant** l'enregistrement |
| 9. Infectés, échecs | Aucune copie ; `infected` et `scan_failed` sont hors de toute diffusion ; un média infecté n'est jamais revalidé automatiquement |
| 10. Audit | `media.scan_started`, `scan_clean`, `scan_infected`, `scan_failed` (motif), `reconcile_*`. Ni nom de fichier, ni clé, ni contenu |

**Idempotence.**
- La prise en charge est un `UPDATE` conditionnel sous verrou de ligne : un message rejoué, dupliqué, ou deux workers donnent **une seule** tentative.
- Le message Redis est acquitté **après** l'enregistrement : un arrêt avant acquittement provoque un rejeu sans effet.

## 4. Machine à états et reprise après panne

```
pending_upload ─(déposant)─► quarantined ─(service)─► scanning ─┬─► clean ───(nouvelle analyse)──► infected
                                    ▲                           ├─► infected
                                    │                           └─► scan_failed ─(échéance, < 6 tentatives)─► scanning
                                    └──────── (bail expiré : verdict « timeout » ► scan_failed)
```

| Panne | Comportement (toujours en sécurité) |
|---|---|
| Redis indisponible | Le worker attend (reprise de 1 à 30 s) ; aucun état modifié. PostgreSQL reste la vérité : les tâches sont réémises à la reprise |
| ClamAV indisponible | `scan_failed` (`clamd_unavailable`), nouvel essai différé |
| RustFS indisponible | `scan_failed` (`storage_unavailable`) ; si la copie saine échoue : `clean_copy_failed` |
| PostgreSQL indisponible | Aucune prise en charge ni aucun verdict ; si la panne survient au verdict, la copie saine de la tentative est retirée et le média reste `scanning` jusqu'au bail |
| Worker arrêté pendant l'analyse, après le verdict ou pendant la copie | Le média reste `scanning`. À l'expiration du bail (15 min), la réconciliation inscrit `timeout`, passe en `scan_failed` et relance ; la copie éventuelle est **signalée** comme orpheline |
| Analyse expirée | `timeout` (délai clamd de 120 s) |
| Signatures périmées | `signatures_stale` : jamais `clean` |
| Fichier supprimé ou modifié | `object_missing` / `integrity_mismatch` |
| Deux workers, événement dupliqué | Une seule tentative ; les autres renvoient « déjà pris » |
| Verdict d'un worker lent après clôture | Refusé par la base (tentative perdue) ; sa copie saine est retirée |

## 5. Réconciliation

Exécutée par un seul worker à la fois (verrou Redis), chaque minute.

- **Tâches perdues** : réémission des médias `quarantined` et des `scan_failed` échus.
- **Médias bloqués** : bail expiré → `timeout` → `scan_failed`, journalisé (`media.reconcile_lease_expired`). Les tentatives épuisées et les réservations échues sont comptées.
- **Objets orphelins** :
  - quarantaine sans ligne en base ;
  - copie saine sans référence ;
  - média `clean` sans objet.

  Ils sont **signalés** (`media.reconcile_report`, au plus 5 clés d'exemple), **jamais supprimés**. Aucune politique de suppression n'est validée à ce jour.
- **Ce que la réconciliation ne touche pas.** Elle ne modifie aucun média `clean`, rattaché, sous `legal_hold` ou signalé, et ne touche aucune édition figée. Elle n'a d'ailleurs pas les privilèges SQL nécessaires.

## 6. Tests de sécurité (mission 7)

**Base de données** : `database/tests/test_media_scanner_role.py`, 11 tests.
- Rôle métier et rôle de connexion ; inventaire exact des privilèges.
- Cycle complet par la seule session de service.
- Refus pour toute autre session, même en déclarant l'acteur de service.
- Usurpation et champs interdits.
- Désactivation des déclencheurs et `session_replication_role` refusées.
- Verdict valide exigé : tentative, empreinte, signatures de moins de 48 h.
- Ajout seul ; audit de service.
- Rôle non attribuable, non extensible, non invitable, sans session ni mot de passe.
- Base vide, base existante, retour arrière refusé après analyse, rôle de connexion préexistant.

**Les tests 0007 restent figés sur leur propre état** (base arrêtée à 0007).

**Worker** : `services/api/tests/api/test_media_scan.py`, 19 tests, sur PostgreSQL, Redis, RustFS et ClamAV **réels** (CI et Windows).

| # | Exigence | Test |
|---|---|---|
| 1 | Fichier sain | `test_clean_file_is_validated_copied_and_traceable` |
| 2 | EICAR détecté | `test_eicar_is_detected_and_never_copied` : EICAR reconstitué à l'exécution, déposé en quarantaine de test seulement, jamais copié |
| 3 | Signatures périmées | `test_stale_signatures_never_produce_clean` |
| 4 | Refus de forcer `clean` | `test_clean_cannot_be_forced_outside_the_service_connection` |
| 5 | Empreinte incorrecte, objet supprimé | `test_modified_or_deleted_object_is_never_validated` |
| 6 | Mauvaise tentative | `test_lost_lease_verdict_is_rejected` (+ tests de base) |
| 7 | Worker non autorisé | `test_unauthorized_worker_identities_are_refused` |
| 8 | Tâches dupliquées | `test_duplicate_redis_events_produce_a_single_verdict` |
| 9 | Concurrence | `test_two_workers_on_the_same_media` |
| 10 | Pannes et reprise | `test_clamd_unavailable_or_timeout`, `test_storage_unavailable`, `test_redis_and_postgres_unavailable_change_nothing`, `test_worker_killed_mid_scan_is_recovered_safely` (×4), `test_commit_failure_removes_only_its_own_clean_copy`, `test_reconciliation_requeues_lost_tasks_and_reports_orphans` |
| 11–14 | Isolation, quarantaine privée, aucun lien public, aucune diffusion | `test_isolation_privacy_and_no_distribution` (lecture anonyme 403 des deux compartiments sur RustFS) |
| 15 | Non-régression | Suites complètes + `validate-phase2.2.3b.ps1` enchaîné |

**Résultats locaux** (PostgreSQL 16 réel, Redis réel ; S3 et clamd **simulés** localement, faute de démon Docker dans l'environnement de l'assistant) :

| Suite | Résultat |
|---|---|
| Base de données + chaîne de migrations | **161 réussis** |
| API complète (S3 + Redis + clamd simulé) | **192 réussis**, 1 ignoré (Mailpit) |
| API sans S3 ni ClamAV | 130 réussis, 63 ignorés |
| Alembic réel : upgrade → `verify_head` → downgrade base → `verify_base` → upgrade | OK |
| Ruff, format, mypy (52 fichiers) | OK |

Dépendances : aucune nouvelle (psycopg, redis et boto3 sont déjà déclarés).

**Exigé avant le push** : la même suite sur **ClamAV réel**.
- En CI : service `clamav/clamav:1.5.5` dans le job API, et profil `media` complet dans le job Docker Compose.
- Sous Windows : `validate-phase2.2.3c.ps1`.

## 7. Validation Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3c.ps1
```

Étapes du script :
1. **État Git** et migrations historiques inchangées.
2. **Mot de passe de service** : ajouté à `.env` s'il manque (aléatoire, non affiché).
3. **Non-régression complète** : 2.2.3-b → 1.2, migration 0008 appliquée, `verify_head`.
4. **Profil `media`** :
   - santé des 8 services ;
   - **signatures de moins de 48 h** (attente de 15 min au plus) ;
   - réseaux : clamd interne, sans port publié, injoignable depuis l'API ; freshclam isolé ;
   - identité du worker : `nl_media_scanner`, non superutilisateur, sans accès à l'audit ni aux e-mails ;
   - **mémoire réelle** (`docker stats`).
5. **pytest** sur base jetable, rattaché aux réseaux `default` et `media-scan` : rôle de service, worker (EICAR sur ClamAV réel, pannes, concurrence), téléversement.
6. **Lecture seule** :
   - `media:scan` réservée ;
   - compte de service sans mot de passe ni session ;
   - aucun média `clean` sans verdict valide ;
   - verdict forcé refusé ;
   - journal en ajout seul ;
   - rôle non attribuable ;
   - aucune route de diffusion ;
   - compartiment sain illisible anonymement ;
   - aucune publication médicale ; pilote en brouillon ;
   - journaux du worker sans secret.

**Résultats Windows** : à compléter après exécution (mémoire mesurée de clamd, nombre de tests).

## 8. Risques résiduels

| Risque | Niveau | Traitement |
|---|---|---|
| **L'API se connecte en superutilisateur PostgreSQL** (développement) : elle peut techniquement contourner toutes les gardes (`SET SESSION AUTHORIZATION`, désactivation des déclencheurs) | **Élevé avant la production** | Prérequis de 2.2.3-d et de la production : compte applicatif ordinaire, propriétaire distinct des objets. Lot dédié recommandé |
| Une seule clé S3 pour l'API et le worker (D19, cloisonnement applicatif) | Moyen | Clés RustFS distinctes : API en écriture sur la quarantaine seulement, worker en lecture de quarantaine et écriture du compartiment sain |
| Dépendance à la fraîcheur des signatures (miroirs ClamAV, limitation de débit) | Moyen | Échec sûr (`signatures_stale`) ; alerte d'exploitation à prévoir |
| ClamAV ne détecte pas tout (PDF et MP4 non réécrits) | Moyen | Défense en profondeur 2.2.3-b + nouvelle analyse périodique (à planifier avec 2.2.3-d) |
| Orphelins jamais supprimés | Faible | Signalés ; politique de suppression à valider explicitement |
| Mot de passe de service dans `.env` et dans l'environnement de l'API (qui le pose) | Faible (dév.) | Gestionnaire de secrets en production |

## 9. Ce qui n'est pas fait (volontairement)

- Aucune route de diffusion.
- Aucun lien présigné ni public.
- Aucune interface.
- Aucune nouvelle analyse périodique planifiée.
- Aucune purge ni suppression automatique d'objet.

Prérequis de la suite : [PHASE2.2.3D-READINESS.md](PHASE2.2.3D-READINESS.md).
