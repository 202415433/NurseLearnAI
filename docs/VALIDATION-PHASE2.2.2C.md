# Validation — Étape 2.2.2-c : catalogue public lu à travers l'édition publiée

Branche `phase-2.2.2c-public-catalog`, partie de `main` = `130054c` (fusion de #12, CI 7/7).

- **Aucune migration** : la base reste en tête à `0006_independent_versions`, avec 71 tables.
- **Aucune donnée modifiée** : `program_modules` n'est plus lue par le catalogue, mais elle est
  conservée.
- **Aucune publication médicale.**

## 1. Modifications

| Fichier | Changement |
|---|---|
| `services/api/app/domains/catalog/editions.py` (nouveau) | Règle d'exposition unique (`EXPOSABLE_SQL`) : résolution de l'édition publiée et contrôle de complétude et de cohérence |
| `services/api/app/domains/catalog/router.py` | Liste et détail lus **exclusivement** via l'édition publiée (`nl_release_lessons`), prérequis figés, `release_no` |
| `services/api/app/domains/reference/router.py` | Disponibilités des métiers et compteurs des spécialités alignés sur la même règle |
| `apps/web` (types, page programme, dictionnaires FR/EN) | Affichage des prérequis d'un module (titres des modules requis) |
| `database/tests/test_program_versions.py` | Test de compatibilité des éditions historiques `manifest_v1` |
| `services/api/tests/api/test_catalog_editions.py` (nouveau) | 8 scénarios de catalogue |

Le contrat de réponse évolue de façon **additive** :
- `ProgramDetail.release_no` : numéro de l'édition servie ;
- `ModuleOut.prerequisites` : codes des modules requis.

Aucun champ n'est retiré.

## 2. Comportement définitif du catalogue

1. **Édition résolue explicitement.** Un programme est servi par son unique édition `published`
   (index 0005). Une édition en brouillon, en relecture, approuvée ou archivée n'est jamais servie.
2. **Contenu de l'édition uniquement.** La fonction `nl_release_lessons(release)` (0006), commune aux
   structures `manifest_v1` et `module_versions`, fournit :
   - les modules, leur position et leur caractère obligatoire ;
   - les leçons et leur position **figée par la version de module** ;
   - la version exacte de chaque leçon, par langue.
3. **Aucun changement implicite de version.** Pour chaque leçon, l'API sert la version de la langue
   demandée **si l'édition la cite**, sinon la version française citée. Une autre version publiée de
   la même leçon n'est jamais sélectionnée, qu'elle soit plus récente, citée par un autre programme ou
   d'une autre langue.
4. **Prérequis figés.** Ils sont lus dans `program_release_prerequisites` de l'édition servie. Les
   éditions historiques n'en ont pas.
5. **Refus d'exposer une édition incomplète ou incohérente (404, absent des listes et des compteurs).**
   Le programme n'est exposé que si toutes ces conditions sont réunies :
   - le programme est `published` et n'appartient à aucun établissement ;
   - l'édition est publiée et non vide ;
   - aucune version de module n'est vide ;
   - chaque leçon a une version française ;
   - chaque module est public et `published` ;
   - chaque version de module est `published` et appartient au bon module ;
   - chaque version de leçon est `published`, appartient à la bonne leçon et à la langue annoncée ;
   - chaque version de leçon est commune ou propre à la juridiction du métier du programme ;
   - pour un **programme médical**, chaque version de leçon cite une source active et vérifiée.
6. **Disponibilités cohérentes.** « Publié » pour un métier, ou compteur d'une spécialité, signifie
   « exposé par le catalogue ». Un programme `published` mais incohérent n'est plus compté.

La protection contre les parcours incomplets fait donc partie du comportement définitif. Le cas
observé en 2.2.2-b (programme publié par l'API servi sans ses modules) est corrigé : le programme est
désormais servi **avec** les modules de son édition.

## 3. Compatibilité

| Cas | Résultat |
|---|---|
| Éditions historiques `manifest_v1` (0005) | Exposées après 0006 via `nl_release_lessons` (`test_catalog_exposes_historical_0005_editions_then_the_new_edition`) |
| Remplacement d'une édition historique par une édition relationnelle | La nouvelle édition est servie dès sa publication ; l'ancienne est archivée par la base |
| Fixture e2e `e2e_demo_published.sql` (édition relationnelle + `program_modules`) | Toujours exposée : 1 module, lecture FR et EN vérifiée sur une base de type CI |
| `program_modules` (structure de travail historique) | Ignorée par le catalogue, sans être supprimée ni modifiée |
| Inscriptions et éditions existantes | Aucune écriture : le catalogue reste en lecture seule |
| Pilote PAB Québec | Toujours en brouillon : `published = 0`, `in_preparation = 1` |
| Parcours Pays → Juridiction → Métier → Programme | Inchangé : contrat additif, tests d'API, tests Vitest et build réussis ; e2e en CI et sous Windows |

## 4. Cas d'erreur

| Situation | Réponse publique |
|---|---|
| Programme inconnu, en brouillon, privé, sans édition publiée | `404 not-found` (`problem+json`), absent des listes |
| Édition publiée incohérente (ex. module devenu privé, version manquante, source médicale retirée) | `404`, absent des listes et des disponibilités |
| Langue demandée non citée par l'édition | Version française **citée** (`locale = "fr"`) |
| Identifiant malformé, filtres invalides | `422` (inchangé) |

## 5. Tests (locaux, PostgreSQL 16.15 + pgvector 0.8.0, Redis 7.0.15)

| Suite | Résultat |
|---|---|
| `tests/api/test_catalog_editions.py` | **8 réussis** |
| API complète + unitaires | **125 réussis, 1 ignoré** ; second passage sur la même base : stable |
| `database/tests` | **122 réussis** (dont la compatibilité `manifest_v1`) |
| Ruff (dépôt, 107 fichiers) et mypy (45 fichiers) | OK |
| Web : typecheck, ESLint, Prettier, Vitest (34), `next build` | OK |
| Base de type CI + fixture e2e, API réelle | Programme de démonstration servi avec 1 module, `release_no = 1`, leçon EN ; disponibilités du pilote inchangées |
| Playwright + axe | Non exécutables dans l'environnement de développement (navigateur bloqué par le proxy) ; **réussis sous Windows** (14 réussis, 1 ignoré : démonstration réservée à la CI) |

Les scénarios couverts sont :
- **Édition complète** : ordre des modules, positions de leçons figées, prérequis, retrait après
  archivage.
- **Deux programmes partageant un module à deux versions** : chacun sert la sienne. Une version plus
  récente ou un brouillon ne s'y substituent pas.
- **Nouvelle édition** : l'ancienne est archivée et sa version, restée `published`, n'est plus
  servie. Une édition approuvée mais non publiée n'est jamais servie.
- **Langues** : seules les versions citées sont servies ; une version anglaise publiée mais non
  citée est ignorée ; repli sur la version française citée.
- **Juridictions** : une version locale INTL est servie ; une version d'une autre juridiction est
  refusée à la soumission ; filtres respectés.
- **Brouillons et structure de travail** : `program_modules` remplie sans édition publiée n'expose
  rien.
- **Édition devenue incohérente** (module rendu privé) : 404 et décompte des disponibilités.
- **Établissement** : une édition privée publiée n'est jamais dans le catalogue public.
- **Historique** : l'édition `manifest_v1` reste exposée, puis est remplacée par l'édition
  relationnelle.

## 6. Risques restants

| Risque | Niveau | Traitement |
|---|---|---|
| Titres et résumés des modules lus dans `modules` (non versionnés), le contenu des leçons étant versionné | Faible | Documenté ; versionner ces métadonnées relèverait d'une décision ultérieure |
| Caractère obligatoire d'une leçon (`module_version_lessons.required`) non exposé | Faible | Ajout prévu avec le lecteur (2.2.4) |
| Coût de `nl_release_lessons` sur un gros catalogue | Faible | Fonction `STABLE` indexée ; à mesurer avant production, matérialisation possible |
| MFA des relecteurs absente (D9) | Élevé avant production | Publication médicale réelle désactivée (`medical_publication_enabled=false`) jusqu'à sa mise en place |
| Relecteur médical qualifié non désigné (D10) | Bloquant pour la mise en ligne | Aucun relecteur fictif ; pilote PAB Québec en brouillon ; aucun contenu médical publié |

## 6 bis. Validation Windows

- Journal `phase2.2.2c-20261120-212943.log`, branche `phase-2.2.2c-public-catalog` au commit `da55532`.
- **11/11 contrôles 2.2.2-c réussis, 0 échec.**
- Non-régression 2.2.2-b (12/12), qui enchaîne 2.2.2-a, 2.2.1, 2.1, 2.0 et 1.2.
- pytest complet sur les bases jetables : **253 réussis**.
- Vitest : 34 réussis. Playwright + axe : 14 réussis, 1 ignoré.
- Contrôles 2.2.2-c :
  - aucune nouvelle migration, toujours 71 tables ;
  - aucun programme médical publié ;
  - pilote PAB non exposé (0 publié, 1 en préparation) ;
  - aucun programme exposé incomplet ;
  - programme inconnu en 404 ;
  - services Docker sains.

## 7. État Git

- Branche locale `phase-2.2.2c-public-catalog`, sur `130054c`.
- Commits `da55532` (catalogue), `7c7f07c` (résultats Windows) et cette mise à jour documentaire. Push et PR autorisés ; **fusion non autorisée**.
- Aucun développement 2.2.3, aucune modification des migrations historiques, aucune donnée ni aucun
  volume supprimés.
