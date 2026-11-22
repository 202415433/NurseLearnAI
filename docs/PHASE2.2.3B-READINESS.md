# Préparation — Étape 2.2.3-b : téléversement sécurisé et stockage privé

> Préparation seulement : **aucun développement 2.2.3-b n'est engagé**. Il faut d'abord que 2.2.3-a
> soit intégrée, puis une autorisation explicite. Références : [plan 2.2.3](PHASE2.2.3-PLAN.md),
> § 8 · [validation 2.2.3-a](VALIDATION-PHASE2.2.3A.md) · [ADR-0011](adr/0011-medias-antivirus.md).

## 1. Ce que 2.2.3-a fournit

- `media_assets` naît `pending_upload` avec **réservation atomique** du quota (D17). La réception
  (`→ quarantined`) convertit la réservation en occupation réelle.
- La réception est réservée au déposant, avant l'échéance de réservation.
- Expiration (`nl_media_expire_reservations`) et abandon (`upload_aborted`) libèrent l'espace.
- Les contraintes appliquent la liste blanche MIME et les tailles D15, le texte alternatif et la
  cible des sous-titres.
- Erreurs stables (`HINT nl:…`) : `quota_exceeded`, `forbidden`, `organization_scope`,
  `invalid_reservation`, `born_pending`, `incoherent_reference`, `immutable`.

## 2. Périmètre de 2.2.3-b

| # | Élément | Détail |
|---|---|---|
| B1 | Service de stockage | Client S3 vers RustFS. Deux compartiments privés, créés au démarrage s'ils manquent : `nl-media-quarantine`, `nl-media-clean`. Clés aléatoires `uuid/uuid`. Aucune lecture publique. En 2.2.3-b, écriture en quarantaine seulement |
| B2 | Privilèges minimaux (D19) | Vérifier si RustFS permet des clés par rôle (dépôt, analyse, diffusion). Sinon, cloisonnement applicatif documenté |
| B3 | Points d'accès | `POST /api/v1/editorial/media` en deux temps dans une même requête : réservation, puis réception en flux. Session sensible, `content:create` dans la portée. Plus `GET /api/v1/editorial/media` et `/{id}` (métadonnées et état, portée, 404 hors portée) et `DELETE` (suppression logique). **Aucun accès aux octets** |
| B4 | Lecture en flux | Arrêt dès que la taille maximale du type est dépassée (413), fichier temporaire, SHA-256 calculé au fil de l'eau |
| B5 | Validation | Signature réelle et liste blanche. **Images** : décodage complet, limite de pixels, ré-encodage (EXIF supprimé). **PDF** : analyse structurelle (S4), qui refuse JavaScript, `OpenAction`/`Launch`, fichiers joints, XFA et chiffrement. **MP4** : parcours borné des boîtes ISO BMFF (S4). **VTT** : UTF-8 strict, en-tête, balises neutralisées |
| B6 | Débit (D16) | Redis : 30 par heure et par utilisateur, 300 par heure et par établissement |
| B7 | Isolation des traitements (S5) | Limites de mémoire, CPU et durée pour l'analyse des formats, dans un processus séparé ou avec `RLIMIT_*` |
| B8 | Audit | `media.uploaded` et `media.rejected` (code), sans nom de fichier ni contenu |
| B9 | Erreurs API | Codes stables ajoutés au traducteur : `quota_exceeded` (413), `media_type_mismatch` (422), `media_too_large` (413), `rate_limited` (429) |

Hors périmètre : ClamAV et worker (2.2.3-c), diffusion et jetons (2.2.3-d).

## 3. Prérequis

1. 2.2.3-a fusionnée dans `main`, CI 7/7, validation Windows sans échec.
2. **Bibliothèques (D20)**, à confirmer avant le code ; chacune doit être maintenue, à licence
   compatible et sans vulnérabilité connue (`pip-audit` en CI) :
   - Pillow (images) ;
   - pypdf ou pikepdf (analyse PDF) ;
   - client S3 (`boto3` ou `aiobotocore`) ;
   - détection de signature, interne ou via une bibliothèque.
3. RustFS : vérifier la version `1.0.0` (compartiments, politiques, clés par rôle, versionnage).
   Aucun volume existant ne sera supprimé.
4. Fichiers de test **fictifs et générés** : images synthétiques, PDF minimal, MP4 minimal, VTT.
   Aucune donnée réelle.

## 4. Critères d'acceptation

Repris du plan, § 8 :
- aucun fichier hors liste ou incohérent n'atteint la quarantaine ;
- aucun octet n'est lisible par l'API ;
- les quotas restent exacts sous concurrence, réservation faite **avant** l'écriture S3 ;
- isolation en 404 ;
- session sensible exigée ;
- audit sans nom de fichier ni contenu ;
- non-régression complète et validation Windows.

## 5. Tests obligatoires

- Type hors liste, extension ou MIME trompeur, SVG ou HTML déguisé, polyglotte, bombe d'image.
- PDF actif, chiffré ou avec fichier joint ; MP4 malformé ; VTT avec HTML.
- Taille limite et limite plus un octet ; flux interrompu (réservation libérée).
- Débit dépassé ; quotas sous téléversements simultanés.
- Isolation ; session non sensible.
- Audit sans fuite.

## 6. Risques

| Risque | Mesure |
|---|---|
| Fichier piégé exploitant une bibliothèque d'image ou de PDF | Bibliothèques à jour et auditées, traitement isolé et borné (S5), ré-encodage |
| Écriture S3 réussie mais transaction annulée (objet orphelin) | Clé déterministe enregistrée **avant** l'écriture ; rapprochement (2.2.3-e) ; purge des orphelins |
| Performances du flux | Mesure sur des fichiers de 200 Mio en développement |
| Fonctionnalités limitées de RustFS | Repli sur un cloisonnement applicatif documenté |

## 7. Estimation

1,5 jour, tests compris.
