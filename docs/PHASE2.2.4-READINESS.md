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

## 3. Prérequis avant 2.2.4

1. Fusion de 2.2.3-e après validation Windows (`-Stage e3`) et CI.
2. Décision sur les risques ouverts de 2.2.3 utiles à 2.2.4 : D3-2 (rôle PostgreSQL de lecture dédié à la
   diffusion), D3-4 (nouvelle analyse périodique des médias sains), D3-5 (limites par adresse).
3. Règles de reprise et de progression (événements idempotents, regroupement côté client).

## 4. Hors périmètre de 2.2.4 (inchangé)

Tuteur IA, quiz et évaluations (2.3), attestations, paiements, publication de tout contenu médical réel.
