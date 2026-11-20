# ADR-0009 — Éditions de programme figées et workflow éditorial à cinq états

**Statut** : proposé · 2026-11-19 (sous-lot 2.2.1, migration `0005_learning_engine`) — à approuver avec le plan du lot 2.2 (décision D11).

## Contexte

Le lot 1 prévoyait des versions de leçon relues (ADR-0004) mais aucune notion de ce qu'un étudiant suit exactement : les programmes et modules portaient un simple statut `draft/published/archived`, et une version pouvait être créée directement « publiée ». Le lot 2.2 exige qu'un étudiant suive une édition déterminée, qu'une édition publiée soit immuable, que toute modification d'un contenu approuvé repasse en relecture, et que l'auteur ne puisse jamais approuver son propre contenu (plan 2.2, § 1 et § 4).

## Décision

1. **Workflow strict** sur les versions de leçon et les éditions de programme : `draft → medical_review → approved → published → archived`, plus `medical_review → draft` (modifications demandées, motif obligatoire) et `draft → archived` (abandon). Toute autre transition est refusée par PostgreSQL. L'ancien code `in_review` devient `medical_review`.
2. **Naissance en brouillon** : une version ou une édition s'insère toujours en `draft`, sans aucun champ de relecture ; les champs `reviewed_by`, `reviewed_at`, `published_by`, `published_at` et l'empreinte sont posés **uniquement** par les déclencheurs de transition.
3. **Acteur déclaré** : chaque transition s'exécute dans une transaction où l'application déclare l'utilisateur (`set_config('nl.actor_id', …, true)`) ; la base vérifie son habilitation dans la **portée** du contenu (établissement du module ou du programme, ou rôle global) : `content:create` pour soumettre ou abandonner, `content:review` pour approuver ou demander des modifications, `content:publish` pour publier, `content:review` ou `content:publish` pour archiver.
4. **Indépendance** : le relecteur n'est ni l'auteur de la version, ni l'auteur de l'édition, ni l'auteur d'une version incluse dans l'édition qu'il approuve.
5. **Immuabilité** : une version ou une édition hors brouillon n'est plus modifiable (contenu, sources, structure) ni supprimable ; une empreinte SHA-256 est figée à l'approbation et revérifiée à la publication. Toute modification passe par une nouvelle version (`version_no` continu) ou une nouvelle édition (`release_no` continu).
6. **Édition = manifeste** (`program_releases.manifest`) : liste ordonnée des modules, des leçons et de l'identifiant exact de la version suivie par langue. Validé par la base à chaque étape : appartenance leçon ↔ module ↔ version, langue, juridiction compatible avec le métier du programme, isolation des modules privés, unicité, version française obligatoire.
7. **Programme médical** (`programs.is_medical`, vrai par défaut) : une édition n'est approuvable et publiable que si chaque version incluse est approuvée **et** cite au moins une source médicale active.
8. **Catalogue dérivé** : un programme ou un module ne devient `published` que par la publication d'une édition qui le contient ; la publication d'une nouvelle édition archive la précédente et les versions qu'elle remplace (une seule version publiée par leçon × langue × juridiction, index du lot 1). Si une version remplacée est encore suivie par une autre édition publiée (module partagé), la publication est refusée.
9. **Journal** `editorial_decisions` en ajout seul, écrit exclusivement par les déclencheurs (acteur, portée, empreinte, motif).
10. **Inscriptions et historique** : `enrollments` rattachées à une édition publiée (clé étrangère composée édition × programme), jamais supprimées, changement d'édition seulement vers une édition publiée plus récente ; `learning_events` en ajout seul, idempotents par `(enrollment_id, client_event_id)`, cohérents avec l'édition suivie (module, leçon, version, bloc).

## Conséquences

- Les invariants critiques ne dépendent pas de l'API : un accès SQL direct sans acteur déclaré ou sans habilitation est refusé.
- Les données de test et la fixture de démonstration CI suivent désormais le workflow complet (elles ne peuvent plus insérer de contenu « publié »).
- Limite : la base fait confiance à l'acteur déclaré par l'application ; la séparation des rôles PostgreSQL (rôle applicatif sans droits de superutilisateur ni de désactivation des déclencheurs) est prévue avant la production (lot 2.5).
- Les questions et simulations (lot 2.3) gardent provisoirement `in_review` ; elles seront alignées quand elles seront mises en œuvre.
- Un module partagé entre plusieurs programmes impose de coordonner leurs éditions (décision D12 du rapport 2.2.1).
