# Préparation — Étape 2.2.3 : médias et documents protégés

> Plan détaillé : [`PHASE2.2.3-PLAN.md`](PHASE2.2.3-PLAN.md) · décision antivirus : [ADR-0011](adr/0011-medias-antivirus.md) (approuvée). Les propositions ci-dessous sont remplacées par les décisions confirmées du plan (§ 7).
>
> Document de préparation uniquement : **aucun développement 2.2.3** n'est engagé. Prérequis : la
> fusion de 2.2.2-c, après autorisation.

## 1. Objectif (plan `PHASE2.2-PLAN.md`)

Héberger images, vidéos, sous-titres et PDF **sans exposition publique**, pour les citer dans les
versions de leçon :
- blocs `image`, `video` et `document` de `blocks/v1`, qui portent déjà un `resource_id` depuis 2.2.2-b ;
- table `learning_resources` (0001).

## 2. État de départ

| Élément | État |
|---|---|
| Table `learning_resources` (0001) | Existe : `module_id` ou `lesson_id`, `kind`, `locale`, `storage_key` **ou** `external_url` (https), `alt_text_i18n` obligatoire pour les images, `captions_key` obligatoire pour les vidéos, `license` |
| Stockage S3 compatible (service `s3`, volume `s3data`) | Présent dans Compose, en bonne santé, aucun compartiment applicatif |
| Blocs médias dans `blocks/v1` | Acceptés par l'API éditoriale (`resource_id`), mais **non vérifiés** contre `learning_resources` |
| Analyse antivirus (D7) | Non installée : ClamAV en conteneur local, gratuit |
| Catalogue public (2.2.2-c) | N'expose aucun média |

## 3. Périmètre proposé

| # | Élément | Détail |
|---|---|---|
| M1 | Migration **0007 additive** | Colonnes `sha256`, `byte_size`, `mime_type`, `scan_status` (`pending` / `clean` / `infected` / `failed`), `uploaded_by`, `organization_id` (portée), `status` (`draft` → `approved` → `archived`). Aucune modification de 0001 à 0006 |
| M2 | Téléversement contrôlé | `POST /api/v1/editorial/resources` (session sensible, `content:create` dans la portée). Liste blanche MIME et signature réelle : JPEG, PNG, WebP, MP4, WebVTT, PDF. Taille maximale par type. SHA-256. Métadonnées EXIF supprimées des images |
| M3 | Analyse ClamAV | Conteneur local `clamav`, hors réseau public. Un média reste `pending` et jamais publiable tant que l'analyse n'est pas `clean`. Mode dégradé : média en attente, jamais publié |
| M4 | Stockage privé | Compartiment privé dédié, sans lecture anonyme ; clés aléatoires, jamais dérivées du nom de fichier |
| M5 | Accès | URL présignées de 5 minutes, délivrées seulement pour un média cité par une édition publiée accessible (2.2.4) ou à un éditeur de la portée |
| M6 | Intégration éditoriale | À la soumission d'une version de leçon : chaque `resource_id` doit exister, être de la même portée, être `clean`, avoir un texte alternatif pour une image et des sous-titres pour une vidéo. À la publication : revérification |
| M7 | Accessibilité | Texte alternatif FR obligatoire pour les images (WCAG 1.1.1) et sous-titres WebVTT pour les vidéos (1.2.2), déjà imposés par 0001 |
| M8 | Tests | Types refusés, extension trompeuse, fichier piégé (EICAR), taille excessive, URL expirée, accès hors droits, isolation des établissements, EXIF retiré, média `pending` refusé à la soumission |

Hors périmètre : lecteur de leçons et progression (2.2.4), transcodage vidéo, CDN public.

## 4. Prérequis et décisions à confirmer

1. **2.2.2-c intégrée dans `main`**, avec la CI 7/7 au vert.
2. **D7 confirmée** : image ClamAV officielle, mise à jour des signatures (`freshclam`) en local,
   aucun service payant.
3. **Tailles maximales**, proposées : image 5 Mo, PDF 20 Mo, vidéo 200 Mo en développement.
4. **Bibliothèques**, à vérifier en sécurité et en licence avant ajout :
   - détection de type par signature ;
   - nettoyage EXIF ;
   - client S3.
5. **Compose** : nouveau service `clamav` et compartiment privé. **Aucun volume existant ne sera
   supprimé.**

## 5. Risques

| Risque | Niveau | Mesure |
|---|---|---|
| Fichier malveillant | Élevé | Signature réelle, ClamAV, stockage privé, aucune exécution, `Content-Disposition` forcé |
| Fuite par URL | Moyen | Présignées de 5 minutes, clés aléatoires, contrôle de portée à chaque délivrance |
| Poids des vidéos en développement | Moyen | Limites de taille, mode dégradé |
| Indisponibilité de ClamAV | Moyen | Média `pending`, jamais publiable |
| Données personnelles dans les images (EXIF, visages, patients) | Élevé | EXIF supprimé ; **aucune donnée réelle de patient** (règle du projet) ; contrôle en relecture médicale |

## 6. Estimation

4 à 6 jours, tests compris.
