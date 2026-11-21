# Préparation — Étape 2.2.2-c : catalogue public lu depuis l'édition publiée

> Document de préparation uniquement : **aucun développement 2.2.2-c** n'est engagé. Prérequis : la
> fusion de 2.2.2-b, après autorisation.

## 1. Pourquoi 2.2.2-c

Depuis 0006 (D12), plusieurs versions publiées d'une même leçon peuvent coexister, chacune diffusée
par l'édition qui la cite.

Le catalogue public de la Phase 1.2 (`app/domains/catalog/router.py`) suit encore le modèle d'avant
D12 :
- il lit la structure du programme dans `program_modules`, la table historique du catalogue ;
- pour chaque leçon, il retient **la meilleure version `published`**, selon la langue puis la
  juridiction, quelle que soit l'édition.

Conséquences si rien ne change :
- Un programme publié par l'API 2.2.2-b, donc par une édition relationnelle, n'apparaît dans le
  catalogue sans ses modules, puisque `program_modules` n'est pas alimentée.
- Deux éditions qui citent deux versions différentes d'une leçon donneraient un affichage ambigu.
- Une version restée `published` après l'archivage de son édition pourrait réapparaître dans un autre
  programme qui partage le module.

### Constat vérifié (2.2.2-b)

Un programme publié par l'API est servi par le catalogue avec `modules = []` et `module_count = 0`.
Une **mesure transitoire** est proposée, sans être appliquée : n'exposer un programme que si son
édition publiée et `program_modules` sont cohérentes (voir `VALIDATION-PHASE2.2.2B.md`, § 7 ter).
2.2.2-c la retirera en lisant directement l'édition publiée.

## 2. Périmètre proposé

| # | Élément | Détail |
|---|---|---|
| C1 | Lecture par l'édition publiée | `GET /api/v1/programs/{id}` lit l'édition `published` du programme (au plus une, index 0005) via `nl_release_lessons(release)` : modules, positions, caractère obligatoire, version exacte par langue |
| C2 | Choix de la langue | Version de la langue demandée si l'édition la cite, sinon `fr` (toujours présente, garantie par 0006) ; plus de calcul « meilleure version » global |
| C3 | Liste des programmes | `module_count` calculé sur l'édition publiée ; filtres inchangés |
| C4 | Compatibilité | Les éditions `manifest_v1` restent lisibles par la même fonction ; `program_modules` n'est plus lue par le catalogue et n'est **pas** supprimée (aucune suppression de données) |
| C5 | Disponibilités | `availability` des métiers : `published` = programmes ayant une édition publiée ; inchangé pour `in_preparation` |
| C6 | Détail d'une leçon (si inclus) | Lecture d'une version **citée par l'édition publiée** uniquement, jamais d'un brouillon ni d'une version d'une autre édition |
| C7 | Tests | Deux éditions successives, ancienne archivée et nouvelle publiée : seule la nouvelle est servie. Deux programmes partageant un module à des versions différentes : chacun voit la sienne. Éditions `manifest_v1`. Pilote PAB toujours invisible |
| C8 | e2e | Fixture `e2e_demo_published.sql` adaptée à une édition relationnelle (non médicale, fictive) |

Hors périmètre : interface éditoriale, inscriptions et progression (lot 2.3), MFA des relecteurs (D9),
publication médicale réelle.

## 3. Prérequis

1. **2.2.2-b intégrée dans `main`**, après autorisation de pousser et de fusionner, avec la CI 7/7 au vert.
2. Fixture e2e : une édition relationnelle publiée, fictive et **non médicale**, créée par le workflow.
   La garde `nl.allow_demo` sert uniquement à la CI.
3. Aucune migration n'est nécessaire a priori : `nl_release_lessons()` existe déjà (0006). Il faudra le
   confirmer au démarrage, sans jamais modifier 0001 à 0006.
4. Décision à confirmer : ajouter ou non le détail d'une leçon (C6) au catalogue public dès 2.2.2-c.
   Ce document recommande de le reporter au lot lecteur (2.3).

## 4. Points de vigilance

- **Ne rien publier de médical** : le drapeau `medical_publication_enabled` reste `False`.
- **Non-divulgation** : un programme sans édition publiée répond 404, comme aujourd'hui. Aucun détail de
  brouillon n'est exposé.
- **Performance** : `nl_release_lessons` est une fonction SQL `STABLE`. Pour un programme, une requête
  suffit. Pour la liste, le comptage passe par `program_release_modules` (index existant).
- **Retour arrière** : purement applicatif ; il suffit de rétablir la lecture précédente.

## 5. Estimation

1 à 2 jours, tests compris.
