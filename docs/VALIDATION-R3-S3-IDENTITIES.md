# Validation — Correctif R3 : séparation des identités S3 / RustFS

Branche : `security-r3-s3-identity-separation` (depuis `main` @ `33e02ec`, D14 fusionnée).
Périmètre : identités et permissions de stockage uniquement. **Aucune fonctionnalité de diffusion** (2.2.3-d non commencée), aucune nouvelle migration, aucune route ajoutée.

## 1. Constat de l'audit (avant R3)

| Point | Situation avant R3 |
|---|---|
| Identifiants | Une seule clé, `S3_ACCESS_KEY` / `S3_SECRET_KEY`, qui est la **clé racine** RustFS (`RUSTFS_ACCESS_KEY` / `RUSTFS_SECRET_KEY`) |
| Utilisateurs de la clé | serveur `s3`, API, worker `media-worker`, tests, CI (`ci-media-access`) |
| IAM | Aucune politique évaluée : la clé racine contourne l'IAM de RustFS |
| Compartiments | Créés à la volée par l'API et le worker (`CreateBucket`) |
| Politiques de compartiment | Aucune (pas d'accès public ; le refus anonyme était déjà testé) |
| Console | `127.0.0.1:9001`, clé racine |
| Conséquence | Une API compromise pouvait lire la quarantaine, écrire, remplacer ou supprimer les médias sains, créer des compartiments, poser une politique publique, administrer RustFS |

Opérations réellement utilisées par le code (`app/domains/media/storage.py`) :

| Code | Opération S3 | Compartiment | Service |
|---|---|---|---|
| `put_quarantine` | `PutObject` (+ `HeadBucket`, toléré en 403) | quarantaine | API |
| `delete_quarantine` | `DeleteObject` (objet que la requête vient d'écrire, si la transaction échoue) | quarantaine | API |
| `download_quarantine_sync` | `GetObject` | quarantaine | worker |
| `put_clean_sync` | `PutObject`, puis `HeadObject` (contrôle de l'empreinte) | sain | worker |
| `delete_own_clean_copy_sync` | `DeleteObject` (sa copie non enregistrée en base) | sain | worker |
| `list_keys_sync` | `ListBucket` (réconciliation) | les deux | worker |

## 2. Architecture des identités

| Identité RustFS | Remise à | Secret (`.env`) | Rôle |
|---|---|---|---|
| clé racine (`S3_ACCESS_KEY`) | serveur `s3` et conteneur ponctuel `s3-init` **seulement** | `S3_SECRET_KEY` | amorçage, administration d'urgence |
| `nl-api-uploader` | `api` | `S3_API_SECRET_KEY` | téléversement en quarantaine |
| `nl-media-scanner` | `media-worker` | `S3_SCANNER_SECRET_KEY` | analyse, copie saine, réconciliation |
| `nl-media-reader` | **aucun service** (réservée à 2.2.3-d) | `S3_READER_SECRET_KEY` | lecture future du compartiment sain |

**Provisionnement** — `s3-init` (image de l'API, `python -m app.ops.s3_provision`), sur le modèle de `migrate` : démarre quand `s3` est sain, s'arrête avec le code 0 ; `api` et `media-worker` dépendent de sa réussite (`service_completed_successfully`). Il est idempotent :

1. crée les compartiments manquants, **privés**, et échoue si un compartiment porte une politique (accès hors IAM) ;
2. enregistre une politique gérée par identité (`PUT /rustfs/admin/v3/add-canned-policy`) ;
3. crée ou met à jour l'utilisateur (`add-user`, statut `enabled`) ;
4. rattache la politique (`set-user-or-group-policy`) ;
5. vérifie chaque identité (`user-info` : active, rattachée à sa seule politique).

Les appels d'administration sont signés SigV4 (service `s3`). La sortie est une ligne JSON **sans secret** ; les erreurs n'incluent que le code HTTP et la réponse du serveur. Contrôles d'entrée : secrets de 32 à 40 caractères `[A-Za-z0-9]` (limite S3), **distincts** (refus de toute réutilisation). Options : `--check` (vérification seule), `--inventory` (nombre d'objets par compartiment, sans contenu).

Les applications ne créent plus de compartiment : `HeadBucket` reçoit 403 pour une identité restreinte, ce qui est désormais toléré ; l'écriture qui suit échoue proprement si le compartiment manque.

## 3. Matrice des permissions

Politiques effectives (`app/ops/s3_provision.py::policies`) ; tout ce qui n'est pas listé est refusé (refus implicite IAM).

| Action | API `nl-api-uploader` | Worker `nl-media-scanner` | Lecture `nl-media-reader` | Anonyme |
|---|---|---|---|---|
| `PutObject` quarantaine | ✅ | ❌ | ❌ | ❌ |
| `DeleteObject` quarantaine | ✅ (nettoyage) | ❌ | ❌ | ❌ |
| `AbortMultipartUpload` quarantaine | ✅ | ❌ | ❌ | ❌ |
| `GetObject` / `HeadObject` quarantaine | ❌ | ✅ | ❌ | ❌ |
| `ListBucket` quarantaine | ❌ | ✅ (réconciliation) | ❌ | ❌ |
| `PutObject` sain | ❌ | ✅ | ❌ | ❌ |
| `GetObject` / `HeadObject` sain | ❌ | ✅ (contrôle d'intégrité) | ✅ | ❌ |
| `DeleteObject` sain | ❌ | ✅ (sa propre copie) | ❌ | ❌ |
| `AbortMultipartUpload` sain | ❌ | ✅ | ❌ | ❌ |
| `ListBucket` sain | ❌ | ✅ (réconciliation) | ❌ | ❌ |
| `CreateBucket` / `DeleteBucket` | ❌ | ❌ | ❌ | ❌ |
| `PutBucketPolicy` / ACL | ❌ | ❌ | ❌ | ❌ |
| API d'administration RustFS (`/rustfs/admin/v3`) | ❌ | ❌ | ❌ | ❌ |
| Autres compartiments | ❌ | ❌ | ❌ | ❌ |

L'API n'a **aucune lecture**, même en quarantaine : le contrôle des octets se fait avant l'écriture (fichier temporaire, empreinte, signature de format) et le worker recalcule l'empreinte au téléchargement.

## 4. Exigences de sécurité

| Exigence | Mise en œuvre | Vérification |
|---|---|---|
| Aucune clé réelle dans Git | `.env.example` : secrets vides ; Compose : `${…:?}` (démarrage refusé si absent) ; CI : secrets aléatoires par exécution | CI `compose` |
| Aucune clé dans les journaux | Sortie JSON du provisionneur sans secret ; masquage dans les scripts | `journaux_sans_secret` (Windows) |
| Aucun accès anonyme | Compartiments privés, aucune politique de compartiment (le provisionneur échoue s'il en trouve une) | test 11, `anonyme_refuse_*` |
| Aucun privilège administrateur dans l'API ou le worker | Clé racine remise uniquement à `s3` et `s3-init` | CI (`config --format json`), `cle_racine_reservee_a_s3_et_s3_init` |
| Aucune réutilisation d'identifiants | Un secret par identité, distincts entre eux et de la clé racine (contrôlé par le provisionneur) | CI, `secrets_s3_presents_distincts` |
| Aucune clé de lecture exposée au frontend | `S3_READER_SECRET_KEY` n'est remis qu'à `s3-init` ; `web` ne reçoit aucune variable S3 | `aucune_cle_s3_dans_le_frontend` |
| Aucune URL publique ni présignée | Aucun appel `generate_presigned_url`, aucune ACL publique | `aucune_url_publique_ni_presignee` |

## 5. Tests adversariaux (RustFS réel)

`services/api/tests/api/test_s3_identities.py`, exécuté seulement contre RustFS (`NL_TEST_S3_KIND=rustfs` : un serveur S3 de mise au point n'évalue pas l'IAM). Le module provisionne des identités de test `nl-test-*` avec des secrets aléatoires, puis agit avec chaque identité restreinte.

| # | Exigence | Test |
|---|---|---|
| 1 | L'API téléverse en quarantaine | `test_api_identity_uploads_to_quarantine_only` |
| 2 | L'API ne lit pas le compartiment sain | idem (`GetObject` sain → 403) |
| 3 | L'API n'écrit pas dans le compartiment sain | idem (`PutObject` sain → 403) |
| 4 | L'API ne supprime pas dans le compartiment sain | idem (`DeleteObject` sain → 403, objet intact) |
| 5 | Le worker lit la quarantaine | `test_scanner_identity_reads_quarantine_and_writes_clean` |
| 6 | Le worker écrit un média validé | idem (écriture + `HeadObject` de l'empreinte) |
| 7 | Le worker n'administre pas les compartiments | idem (`CreateBucket`, `DeleteBucket`, `PutBucketPolicy` → 403 ; ACL publiques → 403 ou 501) |
| 8 | La lecture lit le compartiment sain | `test_reader_identity_is_read_only_on_clean` |
| 9 | La lecture ne lit pas la quarantaine | idem |
| 10 | La lecture n'écrit ni ne supprime | idem |
| 11 | Accès anonyme refusé | `test_anonymous_access_is_refused` (GET, PUT, DELETE sans signature) |
| 12 | Élévation de privilèges en échec | `test_privilege_escalation_attempts_fail` (chaque identité tente `add-user`, `add-canned-policy`, de se rattacher `consoleAdmin` ou la politique du worker, puis une politique de compartiment publique ; rien ne change) |
| 13 | Le multipartite ne contourne rien | `test_multipart_does_not_bypass_permissions` |
| 14 | Le redémarrage conserve les politiques | CI `compose` et script Windows : `restart s3`, puis `--check` et sondes |
| — | Compromission de l'API sans effet sur un objet sain | `test_compromised_api_identity_cannot_alter_a_clean_object` (remplacement, suppression simple et groupée, copie serveur, multipartite, ACL, étiquettes, politique → refusés ; octets inchangés) |
| — | Chaîne applicative avec les identités restreintes | `test_application_storage_chain_works_with_restricted_identities` (`MediaStorage` : téléversement API → analyse et copie saine worker → lecture) |

**Sondes dans les conteneurs** (`scripts/r3_s3_probe.py`) : exécutées DANS `api` et `media-worker`, avec les identifiants que Compose leur remet réellement. Les opérations interdites visent des clés aléatoires (aucun objet existant touché ; ni ACL ni suppression de compartiment) ; 403 = refus ; toute autre réponse fait échouer la sonde (code de sortie 1).

## 6. Non-régression

- Suites base de données et API (moto, imitation de clamd) sous `nl_api` : inchangées et vertes.
- CI : job `api` (RustFS et ClamAV réels, tests R3 inclus) ; job `compose` (identités remises à chaque service, `s3-init` terminé avec le code 0, `--check`, sondes API et worker, redémarrage de RustFS).
- Windows : `scripts\validate-r3-s3-identities.ps1` enchaîne `validate-d14-postgres-roles.ps1` → 2.2.3-c (ClamAV et EICAR réels) → 2.2.3-b … 1.2, puis les contrôles R3.
- Inchangés : quotas, isolation des établissements, gardes D14, migrations 0001 à 0009, absence de diffusion, absence de publication médicale.

## 7. Validation sous Windows

```powershell
cd C:\NurseLearnAI-git
git fetch origin; git switch security-r3-s3-identity-separation
powershell -ExecutionPolicy Bypass -File .\scripts\validate-r3-s3-identities.ps1
```

Le script ajoute à `.env` les trois secrets s'ils manquent (valeurs aléatoires, jamais affichées), fait l'inventaire (volumes, base, objets S3), lance la non-régression complète puis les contrôles R3, redémarre RustFS pour vérifier la persistance et compare l'inventaire final. Il ne supprime aucun volume ni aucune donnée. Journal : `docs\validation-logs\r3-<date>.log`.

## 8. Rotation des clés

Identités de service (sans interruption notable) :

1. générer un nouveau secret (`python -c "import secrets;print(secrets.token_hex(16))"`) et le placer dans `.env` ;
2. `docker compose up -d s3-init` : `add-user` met à jour le secret de l'identité existante ;
3. `docker compose up -d --force-recreate api media-worker` (ils relisent leur secret) ;
4. vérifier : `docker compose run --rm --no-deps s3-init python -m app.ops.s3_provision --check`.

Clé racine : changer `S3_SECRET_KEY`, recréer `s3` puis relancer `s3-init`. Les identités de service ne changent pas. En cas de compromission d'une identité : la désactiver immédiatement (`rc admin user disable`, ou nouveau secret + étapes 2 et 3), puis analyser les journaux d'accès RustFS.

## 9. Séparation des responsabilités

- **Exploitation (clé racine)** : provisionnement, rotation, administration d'urgence ; jamais dans un conteneur applicatif.
- **API** : accepte et dépose des octets non vérifiés, sans pouvoir les relire ni les publier.
- **Worker** : seul à transformer un objet de quarantaine en média sain (après empreinte et ClamAV) ; PostgreSQL reste la source de vérité (`nl_media_scanner`, D14).
- **Lecture (2.2.3-d)** : lecture seule du compartiment sain, derrière une route authentifiée à concevoir ; aucune écriture possible.

## 10. Risques résiduels

| Risque | Portée | Atténuation |
|---|---|---|
| L'API peut supprimer ou remplacer **n'importe quel** objet de quarantaine (l'IAM S3 ne distingue pas « ses » objets) | Déni de service sur les médias en attente | Remplacement détecté par le worker (taille et SHA-256 comparés à la base → `integrity_mismatch`, jamais validé) ; suppression → `object_missing` ; aucun effet sur le compartiment sain |
| Le worker peut écrire ou supprimer dans le compartiment sain | Un worker compromis pourrait altérer des médias validés | Identité distincte, réseau `media-scan` interne, connexion PostgreSQL `nl_media_scanner` ; à réduire par versionnement ou verrouillage d'objets (WORM) avant la production |
| La clé racine reste dans `.env` en développement | Poste de développement compromis | Production : secret géré (coffre), remis uniquement au provisionnement, console désactivée ou isolée |
| Console RustFS active (`127.0.0.1:9001`) | Locale uniquement | Désactiver en production (`RUSTFS_CONSOLE_ENABLE=false`) |
| `nl-media-reader` provisionnée mais inutilisée | Aucune (secret remis à aucun service) | Utilisée seulement par 2.2.3-d, après autorisation |
| Journaux d'accès S3 non centralisés | Enquête plus difficile | À prévoir avec l'observabilité (phase ultérieure) |
| Le format des réponses d'administration (`user-info`) dépend de la version de RustFS (`rustfs/rustfs:1.0.0` épinglée) | Une mise à jour pourrait faire échouer `--check` | Image épinglée ; `--check` bloque le démarrage plutôt que de laisser passer |

## 11. Conditions de production

1. Secrets générés et stockés dans un coffre ; aucun `.env` partagé ; rotation documentée (§ 8).
2. Clé racine hors des hôtes applicatifs ; `s3-init` exécuté par l'exploitation.
3. TLS entre les services et le stockage ; console d'administration désactivée ou isolée.
4. Versionnement ou verrouillage d'objets sur le compartiment sain ; règle de cycle de vie sur la quarantaine.
5. Journaux d'accès S3 conservés et surveillés (refus 403 répétés = alerte).
6. Validation Windows R3 et CI verte avant toute fusion ; 2.2.3-d seulement après autorisation explicite.
