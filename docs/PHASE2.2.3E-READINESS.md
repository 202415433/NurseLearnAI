# Préparation — Étape 2.2.3-e : intégration de bout en bout, interfaces, sécurité, restauration

> Préparation seulement : **aucun développement 2.2.3-e n'est engagé**. Il faut d'abord que 2.2.3-d
> soit intégrée, puis une autorisation explicite. Références : [plan 2.2.3](PHASE2.2.3-PLAN.md) § 8 ·
> [validation 2.2.3-d](VALIDATION-PHASE2.2.3D.md).

## 1. Ce que 2.2.3-d fournit

- Service séparé `media-delivery` : `GET /api/v1/media/{id}/content?release=&lesson_version=` (apprenant)
  et `GET /api/v1/media/{id}/preview` (aperçu éditorial), session Bearer obligatoire à chaque requête.
- Identité S3 de lecture seule `nl-media-reader` ; aucune URL de stockage, aucun jeton autonome.
- HTTP Range (vidéo), en-têtes de sécurité, flux à mémoire bornée, revérification des flux longs.
- Révocation (`POST /api/v1/editorial/media/{id}/revoke`), conservation légale (commande d'administration),
  rapport de conservation en lecture seule, limites de débit.

## 2. Prérequis avant 2.2.3-e

1. Fusion de 2.2.3-d après validation Windows et CI.
2. Décision sur la **migration de privilèges** (risque D3-3) : rattachement des médias par l'API
   (`PUT /api/v1/editorial/lesson-versions/{id}/media`, `If-Match`) et `legal_hold` par l'administration.
3. Décision sur le **relais Web** : route serveur Next.js qui lit le cookie HttpOnly et appelle
   `media-delivery` avec `Authorization: Bearer` (le navigateur ne voit jamais le jeton).
4. Politique de conservation par juridiction (durées, purge) : sans elle, aucune purge.

## 3. Travaux proposés pour 2.2.3-e (à autoriser)

1. **Scénario de bout en bout** : dépôt → analyse ClamAV → rattachement → relecture → publication
   **non médicale** → diffusion → révocation → coupure, sur la pile Docker réelle.
2. **Interfaces** (édition et apprenant) : téléversement, état d'analyse, aperçu, rattachement ;
   affichage « média indisponible » dans la leçon ; lecteur vidéo avec sous-titres obligatoires ;
   accessibilité (axe, WCAG 2.2 AA).
3. **Relais Web** des octets (flux, `Range`, en-têtes conservés, aucune mise en cache partagée).
4. **Campagne de sécurité** : en-têtes, CSP de la page hôte, tentatives de contournement par le relais,
   charge (plages vidéo, flux simultanés), mesure mémoire.
5. **Sauvegarde chiffrée et restauration** (D22, S11, S12) : restauration jetable, rapprochement base
   ↔ objets, diffusion échantillonnée.
6. **Nouvelle analyse périodique** des médias `clean` (si non livrée avant).
7. Documentation `VALIDATION-PHASE2.2.3.md` et script Windows de bout en bout.

## 4. Tests obligatoires (rappel)

- Parcours complet réel, y compris révocation pendant la lecture vidéo.
- Aucune requête du navigateur vers RustFS ; aucun jeton dans le DOM, l'URL ou les journaux.
- Interfaces accessibles (clavier, lecteur d'écran, sous-titres).
- Restauration : tout média dont l'objet manque ou diffère n'est jamais servi.

## 5. Interdits maintenus

- Aucune URL S3 publique ou présignée ; aucun jeton de diffusion autonome.
- Aucun contournement de ClamAV ; aucun passage manuel à `clean`.
- Aucune suppression automatique sans politique approuvée.
- Aucune publication médicale sans le workflow éditorial complet et l'autorisation dédiée.
- Aucun service payant.
