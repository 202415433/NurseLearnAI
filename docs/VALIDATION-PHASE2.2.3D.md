# Validation — Étape 2.2.3-d : diffusion sécurisée des médias

Branche : `phase-2.2.3d-secure-media-delivery` (depuis `main` @ `bd0712c` : 2.2.3-a/b/c, D14 et R3 intégrées).
Périmètre : diffusion authentifiée des médias sains, révocation, limitation de débit, mécanismes de conservation.
**Aucune interface (2.2.3-e)**, aucune migration, aucun jeton de diffusion autonome, aucune URL S3 publique ni présignée, aucune publication médicale.

## 1. Audit préalable et hypothèses retenues

| Point audité | Constat | Conséquence pour 2.2.3-d |
|---|---|---|
| Points d'accès existants | L'API d'édition (`/api/v1/editorial/media`) réserve, téléverse, liste, lit les métadonnées et supprime ; aucun octet n'est servi | La diffusion est ajoutée dans un **service séparé** ; l'API d'édition ne gagne que la révocation |
| États des médias | `pending_upload → quarantined → scanning → clean / infected / scan_failed`, `clean → infected` (nouvelle analyse), `revoked` (relecteur plateforme ou administration) ; suppression logique et purge contrôlées par déclencheurs (0007) | Seul `clean`, non supprimé, non purgé, est diffusable |
| Transitions PostgreSQL | Toute transition vers `infected`/`revoked`, toute suppression ou purge incrémente `access_epoch` ; `clean` exige un verdict traçable (0007, 0008) | La diffusion relit l'état et l'époque à chaque requête et pendant les flux |
| Médias, versions de leçons, éditions | `lesson_version_media` (rattachement à un brouillon, figé ensuite) ; éditions lues par `nl_release_lessons` ; catalogue = éditions **exposées** (règle 2.2.2-c, programmes **publics** uniquement) | Apprenant : édition exposée + version citée + média rattaché (ou sous-titres d'une vidéo rattachée) |
| `access_epoch` | Entier croissant (la base refuse toute décroissance) | Utilisé comme validateur (`ETag`) et comme témoin de révocation pendant un flux |
| Droits | Sessions opaques (Bearer seulement), utilisateur `active` ; `learning:participate` (rôles étudiant, formateur, administration ; pas relecteur ni administrateur d'établissement seuls) ; droits éditoriaux par établissement | 401 sans session, 403 sans droit d'apprentissage, 404 hors contexte ou hors portée |
| `nl-media-reader` (R3) | Lecture seule du compartiment sain, remise à aucun service jusqu'ici | Remise **uniquement** au nouveau service `media-delivery` |
| Limites techniques | boto3 synchrone (fil de travail), FastAPI `StreamingResponse`, RustFS `Range` natif | Flux par morceaux bornés (256 Kio), plage ouverte bornée (8 Mio) |
| Conservation | `legal_hold` (administration plateforme, audit en base), signalements, rattachements figés bloquent suppression et purge (0007) ; aucune purge planifiée | Pose/levée par commande d'administration ; rapport de conservation en lecture seule ; **aucune purge** |

**Décisions manquantes et hypothèses retenues**

| Décision | Hypothèse retenue (révisable) |
|---|---|
| Jetons de diffusion (plan § 3.7) | **Abandonnés** (restriction « aucun jeton autonome ») : chaque requête présente la session ; le serveur Web relaiera la session (2.2.3-e) |
| Accès des apprenants d'un établissement aux contenus privés | Impossible tant que les inscriptions n'existent pas (2.2.4) : le catalogue n'expose que des programmes publics |
| Aperçu éditorial | Autorisé aux détenteurs de `content:create/review/publish` dans la portée du média, pour un média `clean` seulement |
| Rattachement par l'API | **Non livré** : `nl_api` n'a aucun droit sur `lesson_version_media` (0009) et l'étape exclut toute migration ; rattachement par la connexion d'administration en attendant une migration de privilèges dédiée |
| `legal_hold` par l'API | Non livré pour la même raison : commande d'administration `python -m app.cli media-legal-hold` (conteneur `migrate`) |
| Purge et orphelins | Aucune suppression automatique : la politique de durées (§ 3.9) n'est pas approuvée par juridiction ; rapport en lecture seule ; orphelins déjà signalés par la réconciliation du worker |
| Nouvelle analyse périodique des médias `clean` | Non livrée (hors du périmètre demandé) ; la diffusion respecte immédiatement toute invalidation (`clean → infected`) |
| Plages HTTP | Vidéo uniquement ; une seule plage ; autres formats : `Range` ignoré (réponse complète, RFC 9110) |
| Statut d'un média indisponible | 404 (aucune information sur l'existence ni l'état) |

## 2. Architecture de diffusion

```
navigateur ──(cookie HttpOnly)──> serveur Web (2.2.3-e, non livré) ──(Bearer)──> media-delivery ──(nl-media-reader)──> RustFS nl-media-clean
                                                                                    │
                                                                                    ├──(nl_api)──> PostgreSQL : session, droits, état, verdict, édition
                                                                                    └──────────> Redis : limites de débit (fermé par défaut)
```

- **Service séparé** `media-delivery` (`uvicorn app.main:create_delivery_app --factory`, `127.0.0.1:28001`), même image que l'API, système de fichiers en lecture seule, `no-new-privileges`, contrôle de santé `/ready`.
- **Identité S3** : `nl-media-reader` exclusivement (ni téléversement, ni analyse, ni clé racine) ; l'API d'édition n'a pas cette identité et ne sert aucun octet.
- **Identité PostgreSQL** : `nl_api` (D14), comme l'API ; aucun droit d'écriture supplémentaire (seul l'audit `media.access_granted` est inséré).
- **Aucune URL de stockage** : le service lit l'objet et le retransmet en flux ; aucune redirection, aucune adresse S3 dans les en-têtes ou les corps.

## 3. Points d'accès

| Service | Méthode et chemin | Usage |
|---|---|---|
| `media-delivery` | `GET /api/v1/media/{media_id}/content?release={édition}&lesson_version={version}` | Apprenant : média d'une leçon d'une édition publiée et exposée |
| `media-delivery` | `GET /api/v1/media/{media_id}/preview` | Aperçu éditorial d'un média sain de la portée |
| API d'édition | `POST /api/v1/editorial/media/{media_id}/revoke` (`{"reason": …}`, session sensible) | Révocation par un relecteur plateforme ou l'administration |
| Exploitation | `python -m app.cli media-legal-hold <média> --by <admin> (--reason … \| --release)` | Pose ou levée d'une conservation légale (conteneur `migrate`) |
| Exploitation | `python -m app.ops.media_retention` | Rapport de conservation en lecture seule (conteneur `api`) |

## 4. Matrice des autorisations

Contrôles appliqués à **chaque** requête, dans cet ordre (le premier échec répond) :

| # | Contrôle | Échec |
|---|---|---|
| 1 | En-tête `Authorization: Bearer` ; session valide, non révoquée, non expirée ; utilisateur `active` | 401 |
| 2 | Limites par utilisateur (par point d'accès) et par établissement | 429 (`Retry-After`) ; Redis indisponible : 503 |
| 3 | Apprenant : `learning:participate` (global ou établissement) | 403 |
| 4 | Média existant ; aperçu : droit éditorial dans la portée du média | 404 |
| 5 | Apprenant : média public ; édition **exposée** par le catalogue ; version de leçon citée par l'édition ; média rattaché à cette version, ou sous-titres d'une vidéo rattachée | 404 |
| 6 | `state = 'clean'`, non supprimé, non purgé, clé saine posée ; **dernier** verdict ClamAV = `clean`, de la tentative courante, sur les mêmes octets (SHA-256), moteur et signatures identifiés | 404 |
| 7 | Plage valide (vidéo) | 416 (`Content-Range: bytes */taille`) |
| 8 | Budget d'octets par minute ; requêtes `Range` par média et par minute | 429 |
| 9 | Objet sain présent, taille et SHA-256 (métadonnée) conformes à la base | 503 |
| 10 | Flux simultanés par utilisateur | 429 |
| 11 | Pendant le flux, toutes les 10 s : session, droits, état, époque, empreinte inchangés | connexion coupée |

| Profil | Apprenant (`content`) | Aperçu (`preview`) |
|---|---|---|
| Anonyme | 401 | 401 |
| Étudiant | média public d'une édition exposée : 200/206 | 404 |
| Étudiant sans rôle (rôle retiré) | 403 | 404 |
| Formateur d'établissement A | idem étudiant | médias sains de A : 200 ; de B ou publics : 404 |
| Formateur plateforme, administration | idem étudiant | médias sains publics et de tout établissement visible |
| Relecteur ou administrateur d'établissement **sans** rôle d'apprentissage | 403 | relecteur : médias sains de sa portée ; administrateur d'établissement : 404 |
| Tout profil, média `quarantined`, `scanning`, `infected`, `scan_failed`, `revoked`, supprimé | 404 | 404 |

## 5. HTTP et formats

| Type | `Content-Type` | Disposition | Plages |
|---|---|---|---|
| JPEG, PNG, WebP | `image/jpeg`, `image/png`, `image/webp` | `inline` | ignorées (200) |
| PDF | `application/pdf` | **`attachment`**, nom assaini | ignorées (200) |
| MP4 | `video/mp4` | `inline` | **une** plage : 206 / 416 |
| WebVTT | `text/vtt; charset=utf-8` (contenu assaini au téléversement) | `inline` | ignorées (200) |

En-têtes communs : `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`, `Pragma: no-cache`, `Content-Security-Policy: default-src 'none'; sandbox; frame-ancestors 'none'`, `Cross-Origin-Resource-Policy: same-site`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `ETag: "<sha256[0:32]>-<époque>"`, `Vary: Authorization, Range`. Le type vient de la **base**, jamais du stockage ni du nom de fichier ; le nom téléchargé est réduit à `[A-Za-z0-9_-]` avec l'extension du type réel.

**Politique HTTP Range** : `bytes=début-fin`, `bytes=début-` (réponse bornée à 8 Mio, le lecteur redemande la suite), `bytes=-N` ; plusieurs plages, unité inconnue, syntaxe invalide, début au-delà de la taille ou fin avant le début → **416** ; `If-Range` différent de l'`ETag` courant → représentation complète (200). Chaque requête `Range` est authentifiée et autorisée à neuf, compte dans les limites de requêtes, de plages par média et d'octets.

**Mémoire bornée** : un seul morceau (256 Kio par défaut) en mémoire par flux ; aucune lecture complète du fichier.

## 6. Révocation

| Événement | Mécanisme | Effet |
|---|---|---|
| Session révoquée (déconnexion, rotation détournée, administration) | `auth_sessions.revoked_at` | 401 à la requête suivante ; coupure du flux à la revérification |
| Utilisateur désactivé | `users.status <> 'active'` | 401 |
| Rôle retiré | `user_roles` relu à chaque requête | 403 (apprenant) / 404 (aperçu) |
| Retrait d'un établissement | droits d'établissement relus | 404 (aperçu) |
| Programme ou édition non autorisé (archivage, édition remplacée) | règle d'exposition relue | 404 |
| Média révoqué | `POST …/revoke` → `revoked`, époque + 1, audit sans motif | 404 ; flux coupé |
| Verdict invalidé (`clean → infected`) | identité de service, époque + 1 | 404 ; flux coupé |
| Changement d'époque | comparaison à la revérification | flux coupé ; `ETag` différent |

**Transferts longs** : revérification toutes les `MEDIA_DELIVERY_RECHECK_SECONDS` (10 s) ; en cas d'échec, la connexion est fermée avant la longueur annoncée (le client voit un transfert incomplet). **Les octets déjà transmis ne peuvent pas être repris** ; la fenêtre d'exposition est bornée par l'intervalle de revérification et par la taille des plages (8 Mio pour une plage ouverte).

## 7. Limitation de débit (Redis, fenêtre d'une minute, fermée par défaut)

| Limite | Variable | Défaut |
|---|---|---|
| Requêtes apprenant par utilisateur | `MEDIA_DELIVERY_CONTENT_PER_MINUTE_USER` | 240 |
| Requêtes d'aperçu par utilisateur | `MEDIA_DELIVERY_PREVIEW_PER_MINUTE_USER` | 120 |
| Requêtes par établissement (de l'utilisateur ; « individuel » sinon) | `MEDIA_DELIVERY_PER_MINUTE_ORG` | 3 000 |
| Requêtes `Range` par utilisateur et par média | `MEDIA_DELIVERY_RANGE_PER_MINUTE_MEDIA` | 120 |
| Octets par utilisateur | `MEDIA_DELIVERY_BYTES_PER_MINUTE_USER` | 512 Mio |
| Flux simultanés par utilisateur | `MEDIA_DELIVERY_CONCURRENT_STREAMS_USER` | 4 |
| Requêtes par adresse (toutes routes `/api/`, partagé avec l'API) | `RATE_LIMIT_PER_MINUTE` | 300 |

Les plages multiples sont refusées (416) : elles ne peuvent pas servir à contourner les limites. Redis indisponible → 503 (aucune diffusion sans limitation).

## 8. Conservation

- **`legal_hold`** : posé ou levé par un administrateur de la plateforme (`user:manage`) via `app.cli media-legal-hold` ; la base refuse tout autre acteur, journalise `media.legal_hold_set` / `released` ; `nl_api` n'a pas le droit d'écrire cette colonne. Un média sous conservation ne peut être ni supprimé ni purgé ; la conservation **n'ouvre aucun accès**.
- **Éditions figées** : rattachements d'une version non brouillon immuables (base) ; média rattaché non supprimable ; une révocation laisse l'édition publiée (média indisponible).
- **Événements d'audit** : `media.access_granted` (une fois par minute, utilisateur et média, sans jeton ni adresse), `media.revoked` (sans le motif), `media.legal_hold_*`.
- **Médias révoqués** : objet conservé, jamais diffusé ; purge seulement par décision manuelle future.
- **Objets orphelins** : signalés par la réconciliation du worker (2.2.3-c), jamais supprimés.
- **Purge automatique : désactivée.** `app.ops.media_retention` (lecture seule) chiffre les éligibilités par catégorie (§ 3.9 du plan) et les protections.

## 9. Tests

`services/api/tests/api/test_media_delivery.py` — chaîne complète sur PostgreSQL, Redis, RustFS et ClamAV **réels** (CI, Windows) : téléversement par l'API, analyse par le worker, rattachement, workflow éditorial, publication d'une édition publique non médicale, diffusion par `create_delivery_app` avec l'identité de lecture de test.

| # | Exigence | Test |
|---|---|---|
| 1 | Accès autorisé à un média sain | `test_authorized_learner_gets_clean_image_with_safe_headers`, `test_pdf_…`, `test_captions_…` |
| 2 | Sans session | `test_no_session_or_invalid_session_is_401` |
| 3 | Entre établissements | `test_cross_establishment_isolation` |
| 4 | Utilisateur désactivé | `test_disabled_user_and_revoked_session_are_refused` |
| 5 | Média infecté (EICAR, ClamAV réel) | `test_infected_and_unscanned_media_are_never_served` |
| 6 | Média non analysé | idem |
| 7 | Média révoqué | `test_revoked_media_and_invalidated_verdict` |
| 8 | Changement de rôle | `test_role_change_is_effective_at_the_next_request` |
| 9 | Session révoquée | `test_disabled_user_and_revoked_session_are_refused` |
| 10 | Changement d'époque pendant un flux | `test_long_stream_is_cut_when_access_is_revoked` |
| 11 | Accès direct S3 | `test_no_direct_or_anonymous_storage_access` (RustFS) |
| 12 | Lecture de quarantaine | `test_delivery_uses_the_read_only_identity`, test 6 |
| 13 | Identité S3 de lecture | `test_delivery_uses_the_read_only_identity` ; sonde `reader` dans le conteneur |
| 14 | Range valide | `test_video_range_requests`, `test_open_range_is_bounded` |
| 15 | Range invalide | `test_invalid_and_multiple_ranges_are_refused`, `test_safe_filename_and_range_parser_units` |
| 16 | Range multiples / répétées | idem, `test_repeated_ranges_and_byte_budget_are_limited` |
| 17 | Coupure RustFS (avant, pendant) | `test_storage_failure_before_and_during_a_stream` |
| 18 | Coupure PostgreSQL | `test_database_failure_closes_delivery` |
| 19 | Limitation de débit | `test_rate_limits_per_user_endpoint_and_establishment`, `test_concurrent_streams_are_limited` |
| 20 | `legal_hold` | `test_legal_hold_blocks_deletion_and_never_grants_access` |
| 21 | Éditions figées | `test_frozen_editions_protect_their_media` |
| 22 | Aucune fuite entre établissements | `test_cross_establishment_isolation`, `test_knowing_the_identifier_is_never_enough` |
| 23 | Aucune publication médicale | `test_no_medical_publication_and_application_identities` |
| 24 | Aucune régression D14 et R3 | idem (connexion `nl_api`), suites existantes, `validate-r3-s3-identities.ps1` |
| — | Objet sain altéré ou absent | `test_incoherent_or_missing_clean_object_is_not_served` (503) |
| — | Conservation en lecture seule | `test_retention_report_is_read_only` |

Codes vérifiés : 200, 206, 401, 403, 404, 416, 429 et 503.

**Résultats locaux** (conteneur de développement : PostgreSQL 16 réel, Redis réel, serveur S3 de mise au point, imitation de clamd ; application en `nl_api`) : `test_media_delivery.py` 26 réussis, 1 ignoré (accès anonyme : RustFS uniquement) ; suite API complète 218 réussis, 11 ignorés (RustFS/ClamAV réels) ; suite base de données 171 réussis ; Ruff et mypy sans erreur. La validation sur RustFS et ClamAV réels se fait sous Windows et en CI.

## 10. Validation sous Windows

```powershell
cd C:\NurseLearnAI-git
git switch phase-2.2.3d-secure-media-delivery
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3d.ps1
```

Le script : état Git ; secrets ; inventaire (volumes, base, objets) ; non-régression complète (R3 → D14 → 2.2.3-c → … → 1.2) ; services (dont `media-delivery`) ; identités PostgreSQL et RustFS ; signatures ClamAV ; points d'accès ; accès anonyme ; pytest de diffusion, Range, révocation et adversariaux, plus téléversement, analyse et identités, sur la pile réelle ; rapport de conservation ; journaux ; intégrité ; aucune publication médicale. Journal : `docs\validation-logs\phase2.2.3d-<date>.log`.

### Résultat (2026-11-23, journal `phase2.2.3d-20261123-153209.log`)

**30/30 contrôles 2.2.3-d, 0 échec**, sur `2908dcf` :
- non-régression : R3 32/32 ; D14 23/23 ; 2.2.3-c (ClamAV et EICAR réels) ; 9 tests adversariaux R3 ; politiques S3 persistantes après redémarrage ;
- 9/9 services sains, dont `media-delivery` (127.0.0.1:28001) ;
- diffusion : identité S3 `nl-media-reader` seule (ni clé racine, ni autre identité), connexion `nl_api` non superutilisateur ; sonde dans le conteneur : 9/9 opérations interdites refusées, lecture du compartiment sain permise ;
- signatures ClamAV fraîches (14,4 h) ;
- API d'édition : aucune route de diffusion (révocation seulement) ; diffusion : deux routes `GET` ;
- accès anonyme : 401 (diffusion), 401 (session inconnue), 403 (S3) ;
- `test_media_delivery.py` : **27/27 réussis** sur PostgreSQL, Redis, RustFS et ClamAV réels (dont 5 Range, 5 révocation, 16 adversariaux) ; téléversement, analyse et identités : 63/63 ;
- rapport de conservation en lecture seule ; aucun secret ni URL de stockage dans les journaux ;
- aucun volume, aucune donnée ni aucun objet perdu ; aucun programme médical publié ; pilote PAB en brouillon.

Première exécution (`819a268`, 26/30) : les 27 tests de diffusion étaient déjà réussis, mais la fonction de comptage du script (`Group`) était masquée par l'alias PowerShell `group` (`Group-Object`) ; renommée dans `2908dcf`. Aucun de ces échecs ne concernait le code de diffusion.

## 11. Risques résiduels

| Réf. | Risque | Atténuation / suite |
|---|---|---|
| D3-1 | Octets déjà transmis avant une révocation non récupérables | Revérification toutes les 10 s, plages ouvertes bornées à 8 Mio ; documenté |
| D3-2 | La diffusion partage la connexion PostgreSQL `nl_api` (droits de lecture larges, R2) | Rôle dédié en lecture seule dans une migration future |
| D3-3 | Rattachement et `legal_hold` sans route d'API (connexion d'administration) | **Corrigé en 2.2.3-e** : migration 0010 (fonctions contrôlées `nl_media_attach`, `nl_media_detach`, `nl_media_set_legal_hold`, permission `media:legal_hold`, journal en ajout seul), routes et interfaces — [VALIDATION-PHASE2.2.3E.md](VALIDATION-PHASE2.2.3E.md) |
| D3-4 | Pas de nouvelle analyse périodique des médias `clean` | À livrer (tâche planifiée du worker) ; la diffusion respecte déjà toute invalidation |
| D3-5 | Limite par adresse partagée entre l'API et la diffusion | Ajuster `RATE_LIMIT_PER_MINUTE` ou séparer le sel en production |
| D3-6 | Apprenants d'établissement sans accès aux contenus privés | Inscriptions (2.2.4) |
| D3-7 | Contrôle d'intégrité par métadonnée (taille et SHA-256 déclarés), pas par relecture complète | Rapprochement hebdomadaire et restauration testée (plan § 3.11) |
| R3-1 à R3-4 | Voir [VALIDATION-R3-S3-IDENTITIES.md](VALIDATION-R3-S3-IDENTITIES.md) | Inchangés |

## 12. Conditions avant production

1. Interface (2.2.3-e) : le serveur Web relaie la session au service de diffusion ; jamais d'appel direct du navigateur à RustFS ni au service sans relais.
2. TLS de bout en bout ; `TOKEN_HASH_KEY` et `RATE_LIMIT_SALT` partagés entre l'API et la diffusion, gérés en coffre.
3. Politique de conservation et de purge approuvée par juridiction avant toute suppression.
4. Nouvelle analyse périodique des médias `clean` en service.
5. Mesures de charge (vidéo, plages, mémoire) et ajustement des limites.
6. Rôle PostgreSQL dédié à la diffusion ; migration de privilèges pour le rattachement et `legal_hold`.
7. Validation Windows 2.2.3-d et CI vertes ; push et fusion sur autorisation explicite.
