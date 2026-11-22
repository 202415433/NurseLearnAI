# Validation — Étape 2.2.3-b : téléversement sécurisé et stockage privé en quarantaine

**Branche** : `phase-2.2.3b-secure-upload`, à partir de `main` = `994b6fe` (fusion de la PR #15, 2.2.3-a).
**Références** : [plan 2.2.3](PHASE2.2.3-PLAN.md) § 8 · [préparation 2.2.3-b](PHASE2.2.3B-READINESS.md) ·
[validation 2.2.3-a](VALIDATION-PHASE2.2.3A.md) · [ADR-0011](adr/0011-medias-antivirus.md) · D15–D24, S1–S14.

**Périmètre.** Réservation, réception en flux, validation stricte et dépôt en **quarantaine privée**.
Hors périmètre, et **non développé** :
- analyse antivirus, worker, ClamAV (2.2.3-c) ;
- tout passage à `clean` (aucune transition manuelle) ;
- toute diffusion de média (2.2.3-d) ;
- toute interface (2.2.3-e).

**Ce qui n'est pas touché.**
- Aucune migration n'est ajoutée ni modifiée : 0007 reste la tête.
- Aucune donnée ni aucun volume Docker n'est supprimé.
- Aucune publication médicale ; le pilote « PAB Québec — Hygiène des mains » reste en brouillon.
- Aucun service payant.

## 1. Architecture

Le téléversement se fait en **deux requêtes**, jamais par le stockage directement :

| Étape | Opération | Contrôles |
|---|---|---|
| 1. Réservation | `POST /api/v1/editorial/media` (JSON) | Bearer ; session **sensible** (≤ 30 min) ; `content:create` dans la portée ; établissement visible ; type et taille déclarés (liste blanche D15) ; débit (D16) ; **réservation atomique** du quota par PostgreSQL (D17) avant tout octet reçu |
| 2. Réception | `PUT /api/v1/editorial/media/{id}/content` (corps brut, `application/octet-stream`) | Déposant seulement ; état `pending_upload` et réservation non expirée ; `Content-Length` ≤ limite ; verrou Redis (un seul envoi à la fois) ; lecture **en flux** avec arrêt dès la limite dépassée ; SHA-256 ; validation isolée ; dépôt en quarantaine ; passage `quarantined` |
| Consultation | `GET …/media`, `GET …/media/{id}` | Métadonnées seulement : ni clé de stockage, ni URL, ni contenu |
| Abandon | `DELETE …/media/{id}` | `upload_aborted` (réservation) ou `user_request` ; libère l'espace |

Aucune route ne renvoie le contenu d'un fichier. L'inventaire exact (5 opérations) est vérifié par
les tests et par `validate-phase2.2.3b.ps1`.

**Aucune transaction ouverte pendant le flux.** Les vérifications sont faites, puis la transaction
est close avant la lecture du corps. L'état final est écrit dans une transaction courte
(`editorial_tx`), qui fixe l'acteur `nl.actor_id` et traduit les erreurs PostgreSQL.

**Validation isolée (bac à sable).** Chaque fichier est analysé par un **processus séparé**
(`python -I app/domains/media/sandbox.py`) :
- environnement minimal ;
- limites `RLIMIT_AS` (768 Mio par défaut), `RLIMIT_CPU`, `RLIMIT_FSIZE` (256 Mio), `RLIMIT_NOFILE` (32), `RLIMIT_CORE` (0) ;
- délai d'expiration (60 s par défaut) ;
- répertoire temporaire privé (0700), toujours supprimé.

Un plantage, une bombe ou un dépassement donne un refus, jamais une acceptation.

**Stockage privé (RustFS, S3).**
- Compartiment de quarantaine `nl-media-quarantine`, créé **privé** à la première utilisation.
- Clés **aléatoires** (`uuid/uuid`), sans nom de fichier ni identifiant d'établissement.
- `Content-Type` neutre (`application/octet-stream`) ; empreinte SHA-256 en métadonnée.
- Aucune URL publique ni présignée. Le navigateur n'accède jamais au stockage : l'API le joint par le réseau Docker interne.
- Le compartiment `nl-media-clean` est seulement **configuré** ; rien n'y est écrit avant 2.2.3-c.

**Erreurs.** Toutes en `application/problem+json` avec un `code` stable :

| Code | Statut |
|---|---|
| `quota_exceeded` | 413 |
| `media_too_large` | 413 |
| `media_type_forbidden`, `media_type_mismatch` | 422 |
| `image_*`, `pdf_*`, `mp4_*`, `vtt_invalid` | 422 |
| `media_empty` | 422 |
| `rate_limited` | 429 |
| `upload_closed`, `upload_in_progress`, `invalid_reservation` | 409 |
| `upload_interrupted` | 400 |
| `storage_unavailable` | 503 |

Isolation entre établissements : **404**.

**Audit (sans données sensibles).** Événements journalisés :
- `media.reserved`, `media.uploaded` (type, taille, SHA-256 reçu et stocké) ;
- `media.rejected` (code), `media.upload_interrupted`, `media.storage_failed` ;
- `media.deleted` (motif).

Jamais de nom de fichier, de texte alternatif, de contenu ni de clé de stockage.

## 2. Formats validés

| Format | Limite | Traitement |
|---|---|---|
| JPEG, PNG, WebP | 5 Mio | Signature réelle et cohérence avec le type déclaré. Côté ≤ 8 000 px, image fixe (une seule trame). `verify()` puis décodage complet ; bombe de décompression refusée. **Ré-encodage** (JPEG q90, WebP q90, PNG optimisé) **sans EXIF, ICC, XMP ni commentaire**, vérifié en relisant la sortie. Toute donnée accolée (polyglotte) disparaît |
| PDF | 20 Mio | Signature `%PDF-` en tête ; rien après le dernier `%%EOF` (polyglotte refusé). Recherche brute des jetons actifs, y compris dans les objets non référencés. Lecture **stricte** (pypdf), chiffrement refusé, 1 à 2 000 pages. Parcours de **tous** les objets (≤ 200 000) : JavaScript, actions automatiques, `/Launch`, fichiers joints, XFA, RichMedia et annotations actives refusés. Fichier conservé **à l'identique** (pas de réécriture) |
| MP4 | 200 Mio | Analyse des boîtes avec tailles strictes (≤ 20 000 boîtes, profondeur ≤ 12). `ftyp` en tête et marque autorisée ; boîtes de premier niveau en liste blanche ; un seul `moov`, au moins un `mdat`. Durée ≤ 3 h ; pistes `vide` ou `soun` seulement, au moins une piste vidéo. Codecs autorisés : `avc1`, `avc3`, `hvc1`, `hev1`, `av01`, `vp09`, `mp4a`, `Opus` ; flux chiffrés refusés |
| WebVTT | 1 Mio | UTF-8 strict, en-tête `WEBVTT`, aucun caractère de contrôle. Blocs `STYLE` refusés ; `NOTE` et `REGION` retirés ; minutages vérifiés. Texte des répliques **assaini** : seules les balises WebVTT autorisées sont gardées, tout le reste est échappé. Fichier **réécrit** |
| SVG, HTML, archives, exécutables, GIF et tout autre type | — | Refusés à la déclaration **et** à la signature réelle |

Pour les images et les sous-titres, la sortie transformée peut différer de la taille envoyée : la
réservation porte donc sur le **maximum du type**. Pour le PDF et le MP4, elle porte sur la taille déclarée.

## 3. Bibliothèques (D20)

| Bibliothèque | Version | Licence | Rôle |
|---|---|---|---|
| Pillow | ≥ 13, < 14 (13.0.0 testée) | MIT-CMU (HPND) | Décodage et ré-encodage des images. La 12.3 avait des CVE (pip-audit) |
| pypdf | ≥ 6.21.4, < 7 | BSD-3-Clause | Analyse structurelle stricte des PDF. La 6.17 avait des CVE |
| boto3 | ≥ 1.35, < 2 | Apache-2.0 | Client S3 vers RustFS (signature v4, chemin, délais, 2 reprises) |
| MP4, WebVTT | — | — | Analyseurs **internes** sans dépendance (pas de ffmpeg dans l'API) |

`moto` (serveur S3 de test) n'a servi qu'à la mise au point locale, faute de pouvoir exécuter RustFS
dans l'environnement de développement de l'assistant. Il **n'est pas** une dépendance du projet.
La CI et la validation Windows utilisent **RustFS réel** (`rustfs/rustfs:1.0.0`, même image que Compose).

## 4. Résultats des tests (local)

| Suite | Résultat |
|---|---|
| API complète avec S3 (`services/api/tests`) | **173 réussis**, 1 ignoré (Mailpit absent localement) |
| dont `test_media_upload.py` | **43 réussis** |
| Base de données (`database/tests`) | **145 réussis** |
| Ruff, mypy (strict) | OK |
| pip-audit (fermeture des dépendances déclarées de l'API) | Aucune vulnérabilité connue, après ajout d'un plancher `opentelemetry-api>=1.45.2` (dépendance transitive de FastAPI, deux CVE récentes sur 1.45.0) |

Couverture de `test_media_upload.py` :
- **Formats valides** : PNG ré-encodé sans métadonnées ; JPEG, WebP, PDF, MP4 ; VTT assaini ; PDF stocké à l'identique.
- **22 fichiers invalides** : SVG, HTML et ZIP déguisés, exécutable ; JPEG annoncé PNG ; image tronquée ; bombe de décompression ; PDF avec JavaScript, avec fichier joint, chiffré, malformé, polyglotte PDF+ZIP, avec objet actif non référencé ; MP4 tronqué, chiffré, trop long, à piste non vidéo ; VTT sans en-tête, avec `STYLE`, à minutage invalide, non UTF-8. À chaque refus, la réservation est libérée.
- **Polyglotte image** : neutralisé par le ré-encodage.
- **Types et tailles** : types interdits à la déclaration ; contrats incohérents ; taille limite acceptée et limite + 1 octet refusée.
- **Droits** : isolation entre établissements (404) ; étudiant et instructeur d'un autre établissement refusés ; session non sensible refusée.
- **Quotas** : quota atteint puis libéré ; 6 réservations simultanées pour 3 places, **exactement 3** acceptées ; aucun compteur négatif.
- **Charge et pannes** :
  - débit dépassé (429) ;
  - stockage injoignable (503, réservation conservée, nouvel essai réussi) ;
  - envoi interrompu (rien de stocké, reprise possible) ;
  - envoi parallèle du même média refusé ;
  - performance du recalcul d'occupation.
- **Exposition** : aucune route de diffusion ; lecture S3 anonyme refusée (403 sur RustFS réel).
- **Audit** : aucune donnée sensible.

**À confirmer sur RustFS réel** : la suite tourne en CI (service RustFS du job `api`) et sous Windows
(`validate-phase1.2.ps1` transmet `NL_TEST_S3_ENDPOINT=http://s3:9000`). Le contrôle
`tests_televersement_executes_sur_rustfs` vérifie qu'aucun test n'a été ignoré faute de stockage.

## 5. Quotas et performances

Les quotas sont appliqués **par PostgreSQL**, jamais contournés par l'API :
- 1 Gio par utilisateur, 5 Gio par établissement, 20 Gio pour la plateforme ;
- verrou `FOR UPDATE` sur la ligne de quota, puis occupation **dérivée** des médias (pas de compteur stocké) ;
- l'expiration des réservations (`nl_media_expire_reservations`) est appelée à chaque nouvelle réservation.

Mesures locales (PostgreSQL 16) :

| Mesure | Résultat |
|---|---|
| Réservation complète (HTTP + transaction) avec 2 005 médias dans la portée | ≈ 12 ms |
| Calcul d'occupation d'une portée de 2 005 médias | ≈ 1,2 ms |
| Ré-encodage d'un JPEG 4 000 × 3 000 (5,4 Mo) dans le bac à sable | ≈ 0,4 s |
| Validation d'un MP4 de 190 Mio (lecture des boîtes et copie) | ≈ 0,2 s |

Limites connues :
- le fichier transite par un répertoire temporaire de l'API (au plus 200 Mio par envoi) ;
- le débit par défaut est de 30 envois par heure et par utilisateur, et de 300 par établissement ;
- une réservation expire après 60 min (TTL réglable de 5 à 1 440 min, borné à 24 h par la base).

## 6. Risques restants

| Risque | Niveau | Traitement |
|---|---|---|
| Aucun antivirus avant 2.2.3-c | Moyen | Aucun média n'est diffusable : tout reste `quarantined`. ClamAV viendra en 2.2.3-c |
| PDF conservé tel quel (pas de réécriture) | Moyen | Analyse structurelle + jetons bruts ; ClamAV en 2.2.3-c ; diffusion en téléchargement seulement (2.2.3-d) |
| MP4 non décodé (structure seule) | Moyen | Pas de lecture côté serveur ; codecs en liste blanche ; ClamAV ; lecture native du navigateur en 2.2.3-d |
| Bac à sable en processus (pas de conteneur dédié) | Faible à moyen | Limites système + délai. Le plan prévoit un worker isolé en 2.2.3-c, qui pourra reprendre la normalisation |
| Redis indisponible | Faible | Débit en **fermeture par défaut** (503) : aucun envoi sans limitation |
| Objets orphelins en quarantaine (panne entre S3 et la base) | Faible | Suppression de l'objet si l'écriture en base échoue. Réconciliateur prévu en 2.2.3-c |
| Mise au point locale sur moto | Faible | Validation RustFS réel exigée en CI et sous Windows avant fusion |

## 7. Validation Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3b.ps1
```

Le script enchaîne la non-régression complète (2.2.3-a → 1.2) puis effectue des contrôles en
**lecture seule** :
- 0007 en tête ;
- aucune migration modifiée ;
- aucun rôle avec `media:scan` ;
- aucun média sain ;
- aucun compteur négatif ;
- API configurée pour le stockage privé ;
- 5 opérations sans diffusion ;
- bibliothèques installées ;
- accès anonymes refusés (401) ;
- quarantaine illisible anonymement ;
- santé des services.
