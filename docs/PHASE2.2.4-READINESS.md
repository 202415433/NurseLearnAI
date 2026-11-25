# Préparation — Étape 2.2.4 : API apprenant (inscription, lecture, progression)

> Préparation seulement : **aucun développement 2.2.4 n'est engagé**. Il faut d'abord que 2.2.3-e soit
> intégrée, puis une autorisation explicite. Références : [plan 2.2](PHASE2.2-PLAN.md) § 2.2.4 ·
> [validation 2.2.3-e](VALIDATION-PHASE2.2.3E.md).

## 1. Ce que 2.2.3 (a → e) fournit à 2.2.4

| Acquis | Où |
|---|---|
| Médias protégés : réservation, quarantaine, ClamAV réel, états, quotas, rattachement contrôlé aux brouillons (0010), révocation, conservation légale | API `/api/v1/editorial/media*`, `/lesson-versions/{id}/media` |
| Diffusion apprenant : `GET /api/v1/media/{id}/content?release=&lesson_version=` (édition exposée, version citée, média rattaché, session à chaque requête, Range) | service `media-delivery` (2.2.3-d) |
| Relais Web de même origine `/relais/medias/{id}/diffusion?release=&lesson_version=` | `apps/web/app/relais/…` (2.2.3-e) |
| Lecteur réutilisable mode **apprenant** (image, PDF, MP4 + VTT, erreurs annoncées) | `apps/web/components/media/MediaPlayer.tsx` |
| Parcours de bout en bout validés (24 Playwright, pile réelle) ; sauvegarde chiffrée et restauration isolée | [validation 2.2.3-e](VALIDATION-PHASE2.2.3E.md) |

## 2. Ce qui manque pour qu'un apprenant voie un média dans une leçon

1. Une route apprenant qui renvoie, pour une leçon d'une édition publiée, **l'identifiant de l'édition,
   la version de leçon** (selon la langue et la juridiction) et **ses médias** (type, texte alternatif,
   sous-titres) — c'est le cœur de 2.2.4 (`/api/v1/learning/*`).
2. Les **inscriptions** : sans elles, les apprenants d'établissement n'accèdent pas aux contenus privés
   (risque D3-6).
3. La page de lecture de leçon (2.2.5) qui place `MediaPlayer` en mode `learner`.

## 2 bis. Prérequis par sujet (analyse, aucune implémentation)

| Sujet | Existant | Prérequis avant de développer |
|---|---|---|
| Inscriptions pédagogiques | Table et règles d'inscription du modèle de données (Phase 1) ; aucune route | Contrat d'API `/api/v1/learning/enrollments` (inscription, retrait), règles d'unicité, état actif/retiré, audit sans contenu |
| Accès aux programmes privés | Isolation des contenus d'établissement en base (0003) ; diffusion qui exige une édition exposée (2.2.3-d) | Règle : apprenant inscrit **et** membre de l'établissement ; extension de `can_learn` de media-delivery aux inscriptions (D3-6) |
| Droits par établissement | Rôles et portées (`org_admin`, `instructor`), permissions par portée | Matrice : qui inscrit, qui retire, qui consulte la progression ; aucun rôle de plateforme accordé par un établissement |
| Parcours apprenant | Catalogue public (2.2.1), éditions publiées (2.2.2-c) | Résolution langue × juridiction de l'édition, prérequis entre modules, bascule d'édition |
| Page de leçon | Lecteur `MediaPlayer` mode apprenant, relais `/relais/medias/{id}/diffusion` | Route qui renvoie édition, version de leçon et médias (texte alternatif, sous-titres) ; page 2.2.5 |
| Progression pédagogique | Tables de progression (Phase 1) | Événements idempotents, regroupement côté client, limites de débit, reprise, complétion |
| Révocation des inscriptions | — | Effet immédiat : contrôle à chaque requête de diffusion (comme la session), époque d'accès ; audit |
| Isolation entre établissements | Déclencheurs, portée calculée par le serveur, 404 sans divulgation (2.2.3) | Tests adversariaux inter-établissements sur chaque nouvelle route ; aucune fuite d'existence |

## 3. Prérequis avant 2.2.4

1. Fusion de 2.2.3-e après validation Windows (`-Stage e3`) et CI.
2. Décision sur les risques ouverts de 2.2.3 utiles à 2.2.4 : D3-2 (rôle PostgreSQL de lecture dédié à la
   diffusion), D3-4 (nouvelle analyse périodique des médias sains), D3-5 (limites par adresse).
3. Règles de reprise et de progression (événements idempotents, regroupement côté client).

## 4. Hors périmètre de 2.2.4 (inchangé)

Tuteur IA, quiz et évaluations (2.3), attestations, paiements, publication de tout contenu médical réel.
