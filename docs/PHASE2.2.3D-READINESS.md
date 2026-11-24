# Préparation — Étape 2.2.3-d : diffusion autorisée des médias, révocation, conservation

> **Statut : implémentée** sur la branche `phase-2.2.3d-secure-media-delivery` — voir
> [VALIDATION-PHASE2.2.3D.md](VALIDATION-PHASE2.2.3D.md) (décisions retenues, notamment : aucun jeton
> autonome, service de diffusion séparé, aucune purge). Texte d'origine conservé ci-dessous.
>
> Préparation seulement : **aucun développement 2.2.3-d n'est engagé**. Il faut d'abord que 2.2.3-c soit
> intégrée, puis une autorisation explicite. Références : [plan 2.2.3](PHASE2.2.3-PLAN.md) § 8 ·
> [validation 2.2.3-c](VALIDATION-PHASE2.2.3C.md) · [ADR-0011](adr/0011-medias-antivirus.md).

## 1. Ce que 2.2.3-c fournit

- Des médias `clean` uniquement après un verdict ClamAV traçable : même tentative, mêmes octets, signatures de moins de 48 h.
- Une copie dans le compartiment privé `nl-media-clean` (`clean_key` aléatoire) ; empreinte SHA-256 en base et en métadonnée.
- `access_epoch` : incrémentée par la suppression, la purge, la révocation et toute détection a posteriori (`clean → infected`). C'est la base de l'invalidation des jetons de diffusion.
- Les états `infected`, `scan_failed` et `revoked` sont hors diffusion.
- Rôle de service `media_scanner` et connexion `nl_media_scanner` à privilèges minimaux.
- Worker idempotent et réconciliation non destructive.

## 2. Prérequis bloquants avant toute diffusion

1. **D14 — CONDITION BLOQUANTE : l'API ne doit plus se connecter à PostgreSQL en superutilisateur ni en propriétaire privilégié du schéma.** *Implémentée (migration 0009, [VALIDATION-D14-POSTGRES-ROLES.md](VALIDATION-D14-POSTGRES-ROLES.md)) ; levée à la fusion, après la validation Windows et la CI.* Sans cela, les gardes de base restent contournables par l'API. Proposition détaillée : [PROPOSAL-D14-POSTGRES-ROLES.md](PROPOSAL-D14-POSTGRES-ROLES.md). Elle prévoit :
   - les rôles `nl_owner`, `nl_migrator`, `nl_api` et `nl_media_scanner` ;
   - des permissions minimales ;
   - la protection des fonctions et des déclencheurs ;
   - des tests de refus des opérations privilégiées.
2. **Clés S3 séparées** — *implémentée (correctif R3, [VALIDATION-R3-S3-IDENTITIES.md](VALIDATION-R3-S3-IDENTITIES.md)) ; levée à la fusion, après la validation Windows et la CI. La diffusion devra utiliser l'identité `nl-media-reader` (lecture seule du compartiment sain), jamais une clé capable d'écrire ni la clé racine* :
   - API : écriture en quarantaine seulement ;
   - worker : lecture de la quarantaine et écriture du compartiment sain ;
   - diffusion : **lecture seule** du compartiment sain.
3. **Validation Windows 2.2.3-c** avec ClamAV réel, et mémoire de clamd mesurée.
4. **Décisions à prendre** :
   - durée de vie des jetons de diffusion (proposition : 5 min, liés à `access_epoch`) ;
   - téléchargement ou affichage en ligne par type ;
   - en-têtes de sécurité (`Content-Disposition: attachment` pour les PDF, `X-Content-Type-Options: nosniff`, CSP `sandbox`) ;
   - limites de débit par utilisateur.

## 3. Travaux de 2.2.3-d (à autoriser)

1. **Route de diffusion authentifiée** : jamais d'URL S3 directe ni présignée exposée au navigateur.
   - Contrôle à chaque requête : état `clean`, non supprimé, non purgé, `access_epoch` inchangée.
   - Droit de lecture : leçon publiée visible de l'apprenant, ou auteur et relecteur sur un brouillon.
   - Isolation par établissement (404).
2. **Jetons courts signés** (HMAC), invalidés par `access_epoch`.
3. **Flux contrôlé** depuis `nl-media-clean` uniquement, avec un type MIME issu de la base et jamais du stockage.
4. **Révocation** par un relecteur plateforme ou l'administration : effet immédiat (époque), audit.
5. **Conservation** : `legal_hold`, signalements, éditions figées. Purge seulement selon une politique validée.
6. **Nouvelle analyse périodique** (par exemple hebdomadaire) des médias `clean` : une menace détectée donne `infected`, avec révocation immédiate.
7. **Politique d'orphelins** : suppression éventuelle uniquement après validation explicite (délai de grâce, double contrôle, audit).

## 4. Tests obligatoires (rappel)

- Média non `clean` jamais servi : `quarantined`, `scanning`, `infected`, `scan_failed`, `revoked`.
- Étudiant d'un autre établissement : 404. Brouillon invisible des apprenants.
- Jeton expiré, rejoué après révocation, ou signé autrement : refusé.
- Détection a posteriori : diffusion coupée immédiatement.
- En-têtes de sécurité présents ; aucun lien S3 dans les réponses.
- Charge : débit, Range HTTP pour la vidéo, mémoire constante en streaming.

## 5. Interdits maintenus

- Aucune URL publique.
- Aucun contournement de ClamAV.
- Aucun passage manuel à `clean`.
- Aucune suppression automatique non validée.
- Aucune publication médicale sans le workflow éditorial complet.
- Aucun service payant.
