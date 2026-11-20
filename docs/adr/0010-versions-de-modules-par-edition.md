# ADR-0010 — Versionnement indépendant des modules par édition de programme (D12)

**Statut** : décision **approuvée** le 2026-11-19 (D12) ; mise en œuvre **proposée** pour la migration additive `0006_module_versions` (non développée).
**Remplace** les points 6 et 8 d'[ADR-0009](0009-editions-figees-et-workflow-editorial.md) pour toute nouvelle édition ; les autres points d'ADR-0009 restent en vigueur.

## Contexte

Avec la migration `0005`, une édition (`program_releases.manifest`, JSON) cite directement des versions de leçon, et l'index du lot 1 `ux_lesson_versions_one_published` n'autorise qu'**une** version publiée par leçon × langue × juridiction. Conséquence : quand un module est partagé par deux programmes, publier une nouvelle version d'une leçon dans le programme A archiverait la version encore suivie par le programme B ; 0005 refuse donc la publication (`nl_program_release_after`). Le choix approuvé (D12) est le **versionnement indépendant des modules par édition** : deux programmes peuvent suivre des versions différentes d'un même module sans se bloquer.

### Contraintes de 0005 concernées

| # | Élément de 0005 (ou antérieur) | Effet aujourd'hui | Sort proposé |
|---|---|---|---|
| C1 | Index `ux_lesson_versions_one_published` (0001) | Une seule version publiée par leçon × langue × juridiction : cause du blocage | Supprimé par 0006 ; « publiée » signifie désormais « diffusée par au moins une édition publiée » |
| C2 | `nl_program_release_after` : archivage des versions remplacées, refus si une autre édition les suit | Couplage entre programmes | Remplacé : plus aucun archivage implicite, plus de blocage croisé |
| C3 | `nl_validate_release` : manifeste JSON modules → leçons → versions | Liens vérifiés par déclencheur, pas par clés étrangères | Conservé pour les éditions existantes (`manifest_v1`) ; nouvelles éditions en tables relationnelles |
| C4 | `nl_manifest_version_ids`, `nl_learning_event_check`, `nl_enrollment_guard`, `nl_student_progress_check`, `nl_module_catalog_guard`, `nl_lesson_version_delete` | Lisent le manifeste JSON | Redéfinis (`CREATE OR REPLACE` dans 0006) pour lire une fonction unique `nl_release_lessons(release)` couvrant les deux structures |
| C5 | `module_prerequisites` (0001), globaux par module | Prérequis non figés par édition | Figés par édition (`program_release_prerequisites`) |
| C6 | Source « active » exigée pour un programme médical | Une référence créée sans vérification est `active` par défaut | 0006 exige aussi `last_verified_at` (vérifiée par un relecteur) |

## Décision

1. **Version de module** (`module_versions`) : instantané immuable d'un module — liste ordonnée de ses leçons, de leur caractère obligatoire et de la **version exacte** de chaque leçon par langue. Numérotation continue par module ; même workflow à cinq états que les leçons (`draft → medical_review → approved → published → archived`), même acteur déclaré, mêmes contrôles d'habilitation dans la portée de l'établissement du module, auteur ≠ relecteur, empreinte SHA-256.
2. **Liens vérifiés par clés étrangères** : `module_version_lessons (module_version_id, module_id, lesson_id, locale, lesson_version_id, position, required)` avec clés composées vers `module_versions (id, module_id)`, `lessons (id, module_id)` et `lesson_versions (id, lesson_id)` — une leçon d'un autre module ou une version d'une autre leçon est impossible par construction ; langue et juridiction contrôlées par déclencheur.
3. **Édition = liste explicite de versions de modules** : `program_release_modules (release_id, module_version_id, position, required)` et `program_release_prerequisites`. Une édition ne cite jamais « la dernière version » : uniquement des identifiants de versions immuables.
4. **Indépendance entre programmes** : deux éditions peuvent citer des versions différentes d'un même module ; publier une édition ne modifie ni n'archive rien d'un autre programme. Une version de leçon ou de module n'est archivable que si **aucune** édition publiée ne la cite encore.
5. **Aucun changement implicite** : l'inscription reste sur son édition initiale ; seule une action explicite la fait passer à une édition publiée plus récente du même programme (règle 0005 conservée) ; aucune édition ne « suit » automatiquement une nouvelle version de module.
6. **Circuit de validation** : une édition n'est approuvable que si chaque version de module est approuvée et chaque version de leçon approuvée ; pour un programme médical, chaque version de leçon cite au moins une source active **et vérifiée** ; le relecteur de l'édition n'est l'auteur d'aucune version de module ni de leçon incluse.
7. **Isolation** : une version de module hérite de l'établissement de son module ; une édition ne cite que des versions de modules publics ou de son établissement.
8. **Compatibilité** : les éditions créées sous 0005 restent valides (`program_releases.structure = 'manifest_v1'`, figées) ; toute nouvelle édition est en `module_versions` (refus d'en créer en `manifest_v1`). Aucune donnée n'est réécrite.

## Stratégie de migration (0006, additive, réversible)

1. Ajouts : tables `module_versions`, `module_version_lessons`, `program_release_modules`, `program_release_prerequisites` ; colonnes `program_releases.structure`, `learning_events.module_version_id`, `student_progress.module_version_id` ; contraintes d'unicité support `lessons (id, module_id)` et `lesson_versions (id, lesson_id)`.
2. Fonction `nl_release_lessons(release_id)` (modules, versions de modules, leçons, langues, versions de leçons) commune aux deux structures ; redéfinition des fonctions de 0005 listées en C4 pour l'utiliser.
3. Remplacement de C1 et C2 (suppression de l'index ; nouvelle règle d'archivage : refus tant qu'une édition publiée cite la version).
4. Les états `in_review` des questions et simulations (lot 2.3) ne sont pas touchés.
5. Retour arrière : suppression des ajouts et restauration des corps de fonction de 0005 ; l'index C1 n'est recréé que si aucune leçon n'a plusieurs versions publiées — sinon le retour arrière s'arrête avec un message explicite au lieu de supprimer des données.
6. Tests : montée/retour/remontée avec signature de schéma identique ; deux programmes sur deux versions d'un même module ; publication de l'un sans effet sur l'autre ; inscriptions conservées ; refus de tout lien incohérent (clés étrangères) ; éditions `manifest_v1` toujours lisibles ; non-régression complète.

## Conséquences

- L'API éditoriale (2.2.2) manipule des versions de modules en plus des versions de leçons et des éditions.
- Le catalogue public (lot 1.2) doit résoudre les leçons par l'édition publiée du programme, et non plus par « la » version publiée d'une leçon (plusieurs peuvent coexister).
- Volume de données accru (une ligne par leçon × langue par version de module), sans impact notable à l'échelle visée.
