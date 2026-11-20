# Plan du lot 2.2 — Moteur pédagogique

> **Statut : plan proposé, aucun développement commencé.** Ce plan démarre après la fusion de la Phase 2.1 (identité et accès) et votre autorisation explicite.
> Références : [ADR-0002](adr/0002-schema-sql-source-de-verite.md) (schéma SQL source de vérité) · [ADR-0003](adr/0003-metiers-par-juridiction.md) (métiers par juridiction) · [ADR-0004](adr/0004-relecture-quatre-yeux-et-attestations.md) (relecture à quatre yeux) · [ADR-0006](adr/0006-textes-i18n-jsonb.md) (textes i18n) · [ADR-0007](adr/0007-identite-acces-sessions.md) (identité, sessions) · [ADR-0008](adr/0008-residence-des-donnees.md) (résidence des données) · [plan 2.1](PHASE2.1-PLAN.md)

## 0. Objectif et périmètre

Permettre à un étudiant de **s'inscrire à un programme publié, le suivre leçon par leçon dans un lecteur accessible, reprendre là où il s'était arrêté et voir sa progression** ; permettre à un auteur de **rédiger** un contenu, à un relecteur médical habilité **distinct de l'auteur** de l'**approuver**, puis de le **publier**, avec un historique complet et immuable des décisions.

**Dans le périmètre** : structure des contenus (programmes, modules, leçons, unités, objectifs, compétences, prérequis), versions et éditions publiées, workflow éditorial à cinq états, lecteur de cours FR/EN, inscription à un programme, progression, reprise, historique, conditions de complétion, documents protégés, préparation du pilote PAB Québec (structure, objectifs, compétences, workflow — **sans contenu médical publié**).

**Hors périmètre** : quiz, examens et banques de questions (proposés au lot 2.3), simulations cliniques, tuteur IA, attestations internes (émission), paiements, application mobile, MFA (conçue ici, livrée avant toute production — voir § 6.8), publication de tout contenu médical réel.

**Recadrage proposé du découpage de la Phase 2** (à valider, décision D1) : le lot 2.2 absorbe « auteur et relecture » (prévu en 2.3 dans le plan 2.1) car un programme ne peut pas être suivi sans workflow de publication ; « quiz et évaluations » passe en 2.3.

| Lot | Contenu |
|---|---|
| 2.0 | Sécurité et qualité ✅ |
| 2.1 | Identité et accès ✅ intégré dans `main` (#6) |
| **2.2** | **Moteur pédagogique : contenus, workflow éditorial, lecteur, progression** (ce plan) |
| 2.3 | Quiz, évaluations, compétences évaluées |
| 2.4 | Formateurs et établissements (cohortes, suivi) |
| 2.5 | Conformité, MFA, préparation de la production |

---

## 1. Architecture du moteur pédagogique

### 1.1 Principe

Le schéma 0001 contient déjà l'essentiel (60 tables, lot 1). Le lot 2.2 **réutilise** ces tables et n'ajoute qu'une migration additive et réversible, `0005_learning_engine`. Aucune migration historique n'est modifiée ; les contraintes à faire évoluer le sont par la migration 0005 (suppression puis recréation de la contrainte, avec retour arrière).

```
Juridiction ─ Métier ─ Spécialité
                 │
             Programme ──(édition publiée figée)── Édition de programme
                 │ program_modules (ordre, obligatoire)
              Module ── module_prerequisites
                 │ lessons (ordre)
              Leçon ── lesson_versions (langue × juridiction × n° de version, statut éditorial)
                 │ body.blocks[]  = unités pédagogiques
                 │ lesson_version_references → medical_references
                 │ learning_resources (images, vidéos, documents)
              Objectifs d'apprentissage ── objective_competencies ── Compétences ── competency_scopes (par métier)
Étudiant ── Inscription (enrollment) ── Progression (student_progress) + Historique (learning_events)
```

### 1.2 Réutilisation des tables existantes

| Concept | Table(s) existante(s) | Évolution par 0005 |
|---|---|---|
| Programmes | `programs` (métier, spécialité, établissement, niveau, accès, statut) | Statut éditorial à 5 états ; `author_id`, `reviewed_by`, `reviewed_at`, `is_medical` (vrai par défaut) |
| Modules | `modules`, `program_modules` (ordre, obligatoire) | Statut éditorial à 5 états ; `reviewed_by`, `reviewed_at` |
| Prérequis | `module_prerequisites` | Contrôle d'absence de cycle (déclencheur) ; prérequis de leçon : non retenu (D4) |
| Leçons | `lessons` (ordre, type, durée) | — |
| Versions des contenus | `lesson_versions` (langue, juridiction, n° de version, statut, auteur, relecteur, dates, prochaine révision) | Statut `in_review` → `medical_review` ; empreinte `content_sha256` ; immuabilité après approbation |
| Unités pédagogiques | `lesson_versions.body.blocks[]` (JSONB) | Schéma de blocs versionné, validé par l'API (§ 2.2) |
| Sources | `medical_references`, `lesson_version_references` | Au moins une source active exigée pour approuver une leçon médicale |
| Médias et documents | `learning_resources` (texte alternatif obligatoire pour les images, sous-titres obligatoires pour les vidéos, licence) | `mime_type`, `size_bytes`, `sha256`, `scan_status`, `organization_id` |
| Objectifs | `learning_objectives` (module ou leçon, taxonomie de Bloom) | — |
| Compétences | `competencies`, `competency_scopes`, `objective_competencies` | — |
| Progression | `student_progress` (programme, module ou leçon ; statut ; pourcentage ; temps) | `enrollment_id`, `lesson_version_id`, `last_block_id`, index d'unicité |
| Signalements | `content_issue_reports` | Utilisé tel quel (signaler une erreur dans le lecteur) |
| Cohortes | `cohorts`, `cohort_members` | Lecture seule en 2.2 (gestion au lot 2.4) |

### 1.3 Nouvelles tables (migration 0005)

| Table | Rôle | Points clés |
|---|---|---|
| `program_releases` | **Édition publiée et figée** d'un programme : liste ordonnée des modules, leçons et **identifiants exacts** des versions de leçons par langue | `release_no`, statut éditorial à 5 états, `manifest` JSONB, `content_sha256`, auteur, relecteur distinct, dates ; une seule édition `published` par programme ; immuable après approbation |
| `enrollments` | Inscription d'un étudiant à un programme | `(user_id, program_id)` unique ; `release_id` suivie ; `organization_id`/`cohort_id` facultatifs ; statut `active`, `completed`, `withdrawn` ; dates |
| `learning_events` | **Historique** en ajout seul (leçon ouverte, bloc atteint, leçon terminée, module terminé, programme terminé, inscription, retrait) | Clé d'idempotence (UUID client) unique ; horodatage client et serveur ; prêt pour la synchronisation hors ligne (Phase 5) |
| `editorial_decisions` | **Journal des décisions éditoriales** : soumission, demande de modifications, approbation, refus, publication, archivage | Ajout seul (déclencheurs existants `nl_forbid_modification`), empreinte du contenu au moment de la décision, rôle de l'acteur, commentaire obligatoire en cas de refus |

### 1.4 Versions et éditions

- **Leçon** : chaque modification crée une nouvelle `lesson_version` (`version_no + 1`) en `draft`. Une version `approved` ou `published` est **immuable** (déclencheur : titre, corps, sources et médias ne peuvent plus changer).
- **Programme** : ce que l'étudiant suit est une **édition** (`program_releases`), pas l'état courant des tables. Toute modification de structure (ajout, retrait, réordonnancement de modules ou de leçons, nouveau prérequis, nouvelle version de leçon) se fait dans une **nouvelle édition** en `draft`, qui repasse par la relecture.
- **Étudiant inscrit** : il reste sur son édition tant qu'elle est publiée ; lorsqu'une nouvelle édition est publiée, il bascule à la prochaine leçon non commencée ; les leçons terminées restent terminées (la version lue est conservée dans `student_progress.lesson_version_id`). Retrait d'urgence d'un contenu erroné : archivage de l'édition, l'étudiant voit un avis et l'édition précédente si elle reste valide (D5).

### 1.5 Résolution langue × juridiction

Pour une leçon de l'édition : version de la langue demandée et de la juridiction du programme → sinon version commune (`jurisdiction_id NULL`) de la langue → sinon **aucune substitution silencieuse** : la leçon est signalée « non disponible dans cette langue » (le français reste la langue de base). Aucune équivalence implicite entre juridictions (ADR-0003).

---

## 2. Lecteur de cours

### 2.1 Fonctions

| Fonction | Conception |
|---|---|
| Contenus textuels structurés | Rendu serveur (React Server Components) des blocs ; HTML sémantique (titres hiérarchisés, listes, tableaux avec en-têtes) ; aucune injection de HTML brut |
| Images et illustrations | `next/image`, texte alternatif obligatoire (contrainte existante), légende, licence et attribution affichées ; agrandissement accessible au clavier |
| Vidéos | Lecteur HTML5 natif, **sous-titres obligatoires** (contrainte existante), transcription textuelle proposée, aucune lecture automatique, aucune plateforme tierce de suivi |
| Documents pédagogiques | PDF servis par URL signée à courte durée après contrôle d'accès (§ 6.6), alternative texte exigée pour les documents essentiels |
| Navigation entre les leçons | Table des matières du programme (modules, leçons, état : non commencée, en cours, terminée, verrouillée) ; boutons Précédent/Suivant ; fil d'Ariane ; lien d'évitement |
| Sauvegarde de progression | Événements envoyés à l'ouverture, à l'atteinte d'un bloc (IntersectionObserver, regroupés) et à la fin ; idempotents |
| Reprise de lecture | « Reprendre » sur l'accueil et le programme : dernière leçon et dernier bloc atteint (`last_block_id`), défilement jusqu'à l'ancre |
| Affichage responsive | Colonne de lecture 65–75 caractères, table des matières repliable sur mobile, cibles ≥ 44 px, zoom 400 % sans perte |
| Accessibilité WCAG 2.2 AA | Contraste du design system, focus visible et non masqué (2.4.11), ordre de lecture logique, `lang` par bloc si nécessaire, mouvement réduit respecté, aucun contenu limité dans le temps, glisser-déposer jamais obligatoire (2.5.7) |
| Français et anglais | Interface traduite (dictionnaires existants) ; contenu par version de langue ; changement de langue conservant la position |

### 2.2 Schéma des unités pédagogiques (blocs)

Chaque bloc a un `id` stable (UUID) pour la reprise et le signalement, un `type` et des champs validés par l'API (schéma versionné `blocks/v1`) :

`heading` (niveau 2–4), `paragraph` (texte enrichi limité : gras, italique, liens internes ou https), `list`, `table`, `callout` (`info`, `important`, `safety` — encadré de sécurité du patient), `definition`, `steps` (procédure numérotée), `key_points`, `image` (→ `learning_resources`), `video` (→ `learning_resources`), `document` (→ `learning_resources`), `reference` (→ `medical_references`, citation affichée).

Règle : un bloc `steps` ou `callout safety` dans un contenu médical exige au moins une source citée dans la version.

---

## 3. Parcours pédagogiques

| Sujet | Conception |
|---|---|
| Inscription à un programme | `POST /programs/{code}/enrollment` : programme ayant une édition publiée, accès (`free` pour le pilote ; `premium` et paiements hors périmètre ; `organization` réservé aux membres de l'établissement) ; idempotente ; événement `enrollment.created` |
| Organisation des modules | Ordre et caractère obligatoire de `program_modules`, figés dans l'édition |
| Prérequis | Module verrouillé tant que ses prérequis obligatoires ne sont pas terminés ; l'API refuse l'ouverture (403 `prerequisite-not-met`) ; l'interface affiche le cadenas et les modules à terminer ; contrôle d'absence de cycle en base |
| Progression | Leçon : pourcentage = blocs atteints / blocs ; module : leçons obligatoires terminées / total ; programme : modules obligatoires terminés / total ; temps passé plafonné par événement (anti-gonflement) |
| Reprise après interruption | Dernière activité par inscription (`last_activity_at`, leçon, bloc) ; reprise multi-appareils |
| Historique | `learning_events` consultable par l'étudiant (« Mon historique ») ; jamais modifié |
| Conditions de complétion | Leçon : dernier bloc atteint **et** action « J'ai terminé cette leçon » ; module : toutes les leçons obligatoires terminées (+ quiz réussi à partir du lot 2.3) ; programme : tous les modules obligatoires terminés. Aucune attestation émise en 2.2 ; une future attestation interne ne sera **jamais** présentée comme diplôme, licence ou autorisation d'exercer |
| Retrait | L'étudiant peut se retirer ; sa progression est conservée (réinscription possible) |

---

## 4. Gestion éditoriale

### 4.1 États (stricts)

| État | Code en base | Signification | Qui peut y faire passer |
|---|---|---|---|
| DRAFT | `draft` | Brouillon modifiable | Auteur (`content:create`) |
| MEDICAL_REVIEW | `medical_review` | Soumis, figé, en attente de relecture | Auteur (soumission) |
| APPROVED | `approved` | Validé par un relecteur habilité distinct de l'auteur | Relecteur (`content:review`) ≠ auteur |
| PUBLISHED | `published` | Visible des étudiants | Personne habilitée `content:publish`, sur un contenu `approved` |
| ARCHIVED | `archived` | Retiré, conservé pour l'historique | Relecteur ou administrateur |

Transitions autorisées uniquement : `draft → medical_review → approved → published → archived` ; `medical_review → draft` (modifications demandées, commentaire obligatoire) ; `approved → draft` interdit (une modification crée une **nouvelle version**). Toute autre transition est refusée par un déclencheur en base **et** par l'API.

Décision D2 : la migration 0005 renomme le code `in_review` en `medical_review` dans la contrainte de `lesson_versions` (aucune ligne existante n'est dans cet état ; le pilote est en `draft`) et étend aux programmes, modules et éditions les cinq états.

### 4.2 Règles imposées par la base

1. **Un auteur ne peut pas approuver son propre contenu** : contrainte existante `reviewed_by <> author_id` sur `lesson_versions`, étendue aux éditions de programme et aux modules ; l'auteur d'une édition est aussi comparé aux auteurs des versions qu'elle contient.
2. **Relecteur habilité** : déclencheur existant `nl_check_reviewer` (permission `content:review`), étendu aux nouvelles tables.
3. **Programme médical non publiable sans validation** : une édition d'un programme `is_medical` ne passe en `published` que si elle est `approved` par un relecteur habilité, **et** si chaque version de leçon qu'elle référence est `approved` ou `published`, a au moins une source active, et une date de prochaine révision.
4. **Toute modification substantielle déclenche une nouvelle validation** : versions approuvées immuables ; empreinte SHA-256 du contenu canonique enregistrée à l'approbation et revérifiée à la publication ; toute nouvelle version ou tout changement de structure crée une nouvelle édition en `draft`. Le lot 2.2 ne prévoit **aucune** catégorie de « modification mineure » dispensée de relecture (D3).
5. **Échéance de révision** : une version publiée dont `next_review_due` est dépassée est signalée aux relecteurs ; elle reste visible avec un avis, sauf archivage.
6. **Signalement critique** (`content_issue_reports`, gravité `critical`, catégorie `medical_error`) : notification aux relecteurs ; archivage rapide possible.

### 4.3 Interface éditoriale (minimale en 2.2)

Éditeur de blocs (formulaires accessibles, pas d'éditeur WYSIWYG lourd), aperçu identique au lecteur, liste « À relire » pour les relecteurs, **comparaison** entre la version soumise et la version publiée précédente, champ de commentaire, gestion des sources et des médias. Toutes les décisions sont journalisées (`editorial_decisions` + `audit_logs`).

---

## 5. Pilote PAB Québec — « Hygiène des mains et prévention des infections »

> Tout ce qui suit est une **architecture de travail**. Les intitulés sont pédagogiques ; **aucune affirmation clinique** n'est rédigée ici. Les objectifs, compétences et contenus seront rédigés par un auteur qualifié, puis validés par un relecteur médical habilité. Le pilote reste en **DRAFT**.

### 5.1 Existant (lot 1.2, seed `0002_pilot_pab_structure.sql`)

Programme `pab-qc-socle` (brouillon, gratuit) → module `hygiene-mains-prevention-infections` (brouillon, 60 min) → 4 leçons squelettes FR/EN (`l01-introduction`, `l02-principes`, `l03-mise-en-pratique`, `l04-revision`), corps vide.

### 5.2 Structure proposée du parcours (D6)

| Module | Leçons de travail | Durée indicative |
|---|---|---|
| M1 — Comprendre la transmission des infections | Chaîne de transmission ; infections associées aux soins : enjeux pour la personne soignée | 25 min |
| M2 — Hygiène des mains *(module existant, réutilisé)* | Introduction ; principes et moments d'hygiène des mains ; mise en pratique (techniques) ; fiche de révision | 60 min |
| M3 — Pratiques de base et équipement de protection | Pratiques de base ; choix, mise et retrait de l'équipement de protection individuelle | 40 min |
| M4 — Situations de soins | Études de cas fictives en milieu de soins de longue durée ; synthèse | 30 min |

Prérequis : M1 → M2 → M3 → M4 (M2 reste le cœur du pilote ; M1, M3, M4 sont ajoutés par un nouveau seed additif `0003`, en brouillon).

### 5.3 Objectifs d'apprentissage proposés (à valider)

| Module | Objectif (formulation de travail) | Niveau de Bloom |
|---|---|---|
| M1 | Décrire les maillons de la chaîne de transmission d'une infection | comprendre |
| M1 | Expliquer pourquoi les infections associées aux soins concernent le rôle du PAB | comprendre |
| M2 | Identifier les moments où l'hygiène des mains est requise selon le référentiel retenu | se souvenir |
| M2 | Choisir entre friction hydroalcoolique et lavage selon la situation décrite par le référentiel | appliquer |
| M2 | Décrire les étapes de la technique d'hygiène des mains retenue | comprendre |
| M3 | Relier une situation de soins aux pratiques de base applicables | appliquer |
| M3 | Décrire l'ordre de mise et de retrait de l'équipement de protection selon le référentiel | comprendre |
| M4 | Analyser une situation fictive pour repérer les occasions manquées d'hygiène des mains | analyser |

Référentiels à confirmer par le relecteur (sources de `medical_references`, à vérifier et dater) : lignes directrices de l'OMS sur l'hygiène des mains dans les soins, publications de l'INSPQ et du ministère de la Santé et des Services sociaux du Québec, guide de l'Agence de la santé publique du Canada sur les pratiques de base et précautions additionnelles. Aucune source n'est considérée comme approuvée tant qu'un relecteur ne l'a pas vérifiée (`last_verified_at`).

### 5.4 Compétences proposées (à valider)

| Code | Intitulé de travail | Domaine | Risque | Portée PAB (`competency_scopes`) |
|---|---|---|---|---|
| `pab.pci.chain` | Comprendre la transmission des infections | knowledge | low | `to_verify` |
| `pab.pci.hand_hygiene` | Pratiquer l'hygiène des mains aux moments requis | skill | moderate | `to_verify` |
| `pab.pci.standard_precautions` | Appliquer les pratiques de base | skill | moderate | `to_verify` |
| `pab.pci.ppe` | Utiliser l'équipement de protection individuelle | skill | moderate | `to_verify` |
| `pab.pci.report` | Signaler un manquement ou un risque infectieux | attitude | low | `to_verify` |

Toutes les portées restent `to_verify` jusqu'à confirmation par un relecteur à partir d'une source québécoise ; le PAB n'étant pas une profession réglementée au Québec, aucune portée n'est déduite d'un autre métier ou d'une autre juridiction.

### 5.5 Workflow éditorial du pilote

1. Rédaction FR par un auteur qualifié (rôle `instructor`, permission `content:create`), sources citées.
2. Traduction EN, version distincte, relue séparément.
3. Soumission → `medical_review`.
4. Relecture par un professionnel habilité (rôle `medical_reviewer` obtenu **par invitation**, lot 2.1), distinct de l'auteur ; approbation ou demande de modifications motivée.
5. Constitution de l'édition 1 du programme, relecture de l'ensemble, approbation.
6. Publication **uniquement sur votre autorisation explicite**, après validation des textes juridiques et de la MFA des relecteurs (§ 6.8).

En 2.2, seuls des contenus **fictifs et non médicaux** (programme de démonstration « Découvrir la plateforme ») sont publiés, et uniquement dans les bases de test et de CI.

---

## 6. Sécurité et confidentialité

### 6.1 Permissions par rôle (existantes, vérifiées dans l'API **et** en base)

| Action | Étudiant | Formateur | Relecteur médical | Admin. d'établissement | Admin. plateforme |
|---|---|---|---|---|---|
| Lire les éditions publiées accessibles | ✅ | ✅ | ✅ | ✅ | ✅ |
| S'inscrire, progresser, voir son historique | ✅ | ✅ | — | — | — |
| Créer et modifier des brouillons | — | ✅ (`content:create`) | — | — | ✅ |
| Soumettre à la relecture | — | ✅ (ses contenus) | — | — | ✅ |
| Approuver / demander des modifications | — | — | ✅ (`content:review`, jamais ses propres contenus) | — | ✅ (jamais ses propres contenus) |
| Publier un contenu approuvé | — | — | ✅ (`content:publish`) | — | ✅ |
| Archiver | — | — | ✅ | — | ✅ |
| Voir la progression d'autrui | — | ✅ ses cohortes (`student:view_progress`, lot 2.4) | — | ✅ son établissement | ✅ |
| Contenus privés d'établissement | membres | auteurs de l'établissement | relecteurs | ✅ son établissement | ✅ |

### 6.2 Isolation des établissements

Programmes et modules privés (`organization_id`) : visibles et modifiables seulement dans l'établissement (règles 0003 existantes + contrôles API, réponse 404 hors périmètre comme au lot 2.1). Une édition ne peut référencer que des modules publics ou du même établissement (déclencheur existant étendu). Les inscriptions à un programme privé exigent l'appartenance à l'établissement.

### 6.3 Accès aux brouillons

Un brouillon, un contenu en relecture ou approuvé non publié n'est **jamais** servi par les points d'accès étudiants (filtre `published` côté API, édition publiée seulement). Les points d'accès éditoriaux exigent `content:create` ou `content:review` et le périmètre d'établissement ; l'aperçu porte la mention « Brouillon — non validé ».

### 6.4 Protection des documents et médias

- Stockage dans un **compartiment privé** RustFS/S3 ; aucune URL publique permanente.
- Diffusion par **URL présignée de 5 minutes** après contrôle d'accès (édition publiée accessible, ou droit éditorial).
- Liste blanche de types (`image/png`, `image/jpeg`, `image/webp`, `video/mp4`, `text/vtt`, `application/pdf`), taille maximale, empreinte SHA-256, métadonnées EXIF supprimées des images.
- Analyse antivirus locale gratuite (ClamAV en conteneur) avant mise à disposition (`scan_status`) — D7.
- Licence et attribution obligatoires ; aucune photo de patient réel ; aucune donnée de patient réelle.

### 6.5 Historique des modifications

Versions de leçon immuables et numérotées ; éditions de programme immuables ; `editorial_decisions` en ajout seul avec empreinte du contenu ; comparaison de versions dans l'interface.

### 6.6 Audit des décisions éditoriales

Événements `content.created`, `content.submitted`, `content.changes_requested`, `content.approved`, `content.published`, `content.archived`, `release.created`, `release.approved`, `release.published`, `media.uploaded`, `media.rejected`, `enrollment.created`, `enrollment.withdrawn` dans `audit_logs` (chaîné, immuable), avec les mêmes garanties qu'au lot 2.1 (aucun secret, pseudonymisation).

### 6.7 Confidentialité de la progression

La progression est une donnée personnelle : visible par l'étudiant, par les formateurs de ses cohortes et par l'administration de son établissement seulement ; jamais publique ; export et suppression traités au lot 2.5 ; durée de conservation à fixer (D8).

### 6.8 Authentification multifacteur

**Exigée avant toute mise en production** pour `medical_reviewer`, `admin` et `org_admin` (et proposée aux formateurs) : TOTP (RFC 6238, bibliothèque libre, sans service payant) + codes de secours à usage unique hachés ; WebAuthn/clés d'accès ensuite. Conception compatible avec les sessions du lot 2.1 (facteur exigé à la connexion et pour les opérations sensibles). Livraison prévue au lot 2.5 ; **aucune publication de contenu médical réel** tant que la MFA des relecteurs n'est pas en place (D9 : possibilité de l'avancer en sous-lot 2.2.7).

---

## 7. Tests et critères d'acceptation

### 7.1 Tests

| Niveau | Contenu |
|---|---|
| Unitaires | Validation du schéma de blocs ; calcul de progression et de complétion ; résolution langue × juridiction ; empreinte canonique ; transitions d'état ; politique des types de médias |
| PostgreSQL | Contraintes et déclencheurs : transitions interdites, auteur ≠ relecteur, relecteur habilité, immuabilité des versions approuvées et des éditions, publication d'un programme médical incomplet refusée, cycles de prérequis refusés, isolation d'établissement, journal éditorial immuable |
| Migrations | 0005 : montée, idempotence, retour arrière, remontée sur PostgreSQL réel ; renommage `in_review` → `medical_review` sans perte ; seed 0003 du pilote idempotent et en brouillon |
| API | Inscription, lecture d'une édition, progression idempotente, reprise, prérequis, complétion, retrait ; points d'accès éditoriaux ; médias (URL présignée, refus hors droits) |
| Permissions | Matrice complète du § 6.1 ; élévation (étudiant qui soumet ou approuve, auteur qui s'approuve, administrateur d'établissement qui publie) ; brouillons jamais servis aux étudiants ; isolation (identifiant direct, listes, filtres, modifications) |
| Workflow éditorial | Parcours complet draft → publié ; demande de modifications ; nouvelle version après approbation ; nouvelle édition ; archivage d'urgence ; signalement critique |
| Progression | Événements en double ou désordonnés (idempotence) ; bascule d'édition ; multi-appareils ; temps plafonné |
| Navigation Web (Playwright) | Inscription au programme de démonstration, lecture, navigation précédente/suivante, verrou de prérequis, reprise après déconnexion, FR/EN, mobile |
| Accessibilité | axe (WCAG 2.2 AA) sur le lecteur, la table des matières, l'éditeur et la relecture ; navigation au clavier ; zoom 400 % ; mouvement réduit |
| Non-régression | `validate-phase1.2.ps1`, `validate-phase2.1.ps1`, nouveau `validate-phase2.2.ps1` ; CI 7 jobs et plus ; `npm audit` et `pip-audit` |

### 7.2 Critères d'acceptation du lot

1. Un étudiant s'inscrit au programme de démonstration publié, suit ses leçons, reprend après déconnexion au bon bloc et voit sa progression.
2. Les prérequis verrouillent les modules et l'API refuse leur ouverture.
3. Les conditions de complétion de leçon, module et programme sont respectées et historisées.
4. Aucun brouillon, contenu en relecture ou approuvé non publié n'est servi aux étudiants.
5. Les cinq états éditoriaux sont stricts ; toute transition interdite est refusée par l'API **et** la base.
6. Un auteur ne peut jamais approuver son propre contenu (API et base).
7. Un programme médical ne peut pas être publié sans validation par un relecteur habilité de toutes ses composantes.
8. Toute modification d'un contenu approuvé crée une nouvelle version soumise à relecture.
9. Toutes les décisions éditoriales sont journalisées, immuables et consultables.
10. Les documents et médias ne sont accessibles que par URL présignée à courte durée après contrôle d'accès.
11. L'isolation des établissements est vérifiée sur les contenus, éditions, inscriptions et médias.
12. Le lecteur fonctionne en FR et EN, en clair et sombre, sur mobile, sans violation axe sérieuse ou critique.
13. Le pilote PAB Québec est structuré (modules, leçons, objectifs, compétences) et reste **entièrement en DRAFT** ; aucun contenu médical publié.
14. La migration 0005 fonctionne sur PostgreSQL réel (montée, retour arrière, remontée).
15. Non-régression complète des phases 1.2, 2.0 et 2.1 ; CI verte ; audits sans vulnérabilité élevée ou critique.

---

## 8. Découpage du développement

Estimation totale : **5 à 7 semaines** pour un développeur. Chaque sous-lot fait l'objet d'une branche, d'une validation locale (`validate-phase2.2.ps1` enrichi au fil des sous-lots) et d'une PR distincte, sur votre autorisation.

### 2.2.1 — Modèle de données et règles éditoriales en base

| Rubrique | Contenu |
|---|---|
| Objectif | Rendre impossibles, au niveau de la base, les publications non validées et les modifications silencieuses |
| Fonctionnalités | Migration `0005_learning_engine` : cinq états sur leçons, modules, programmes, éditions ; `program_releases`, `enrollments`, `learning_events`, `editorial_decisions` ; colonnes de `student_progress` et `learning_resources` ; déclencheurs de transition, d'immuabilité, de publication médicale, d'absence de cycle ; modèles SQLAlchemy |
| Dépendances | Phase 2.1 fusionnée (rôles, permissions, garde des rôles) |
| Livrables | `0005_learning_engine.sql` + `_down.sql`, révision Alembic, `verify_head.sql` mis à jour, documentation du modèle |
| Tests | Tests PostgreSQL et de migration (§ 7.1) |
| Critères | 5, 6, 7, 8, 9 (niveau base), 14 |
| Risques | Complexité des déclencheurs (performance, messages d'erreur) ; renommage `in_review` — atténués par des tests exhaustifs et un retour arrière testé |

### 2.2.2 — API éditoriale

| Rubrique | Contenu |
|---|---|
| Objectif | Permettre de rédiger, soumettre, relire, approuver, publier et archiver |
| Fonctionnalités | CRUD des brouillons (programmes, modules, leçons, versions, objectifs, sources) ; validation du schéma de blocs ; transitions ; éditions de programme ; liste « À relire » ; comparaison de versions ; audit |
| Dépendances | 2.2.1 |
| Livrables | Routeur `/api/v1/editorial/*`, schémas, référence API |
| Tests | API, permissions, workflow éditorial |
| Critères | 5, 6, 7, 8, 9, 11 |
| Risques | Ergonomie de la comparaison ; surface d'attaque des contenus riches (aucun HTML brut accepté) |

### 2.2.3 — Médias et documents protégés

| Rubrique | Contenu |
|---|---|
| Objectif | Héberger images, vidéos, sous-titres et PDF sans exposition publique |
| Fonctionnalités | Téléversement contrôlé, liste blanche, taille, SHA-256, suppression EXIF, analyse antivirus locale, URL présignées de 5 minutes, texte alternatif et sous-titres obligatoires |
| Dépendances | 2.2.1 ; service RustFS existant ; conteneur ClamAV (D7) |
| Livrables | Service de stockage, points d'accès médias, compartiment privé configuré dans Compose |
| Tests | Types refusés, fichiers piégés, URL expirées, accès hors droits, isolation |
| Critères | 10, 11 |
| Risques | Poids des vidéos en développement ; disponibilité de ClamAV (mode dégradé : média en attente, jamais publié) |

### 2.2.4 — API apprenant : inscription, lecture, progression

| Rubrique | Contenu |
|---|---|
| Objectif | Suivre un programme publié de bout en bout |
| Fonctionnalités | Inscription et retrait, lecture d'une édition (résolution langue × juridiction), prérequis, événements idempotents, reprise, complétion, historique, bascule d'édition |
| Dépendances | 2.2.1 ; 2.2.3 pour les médias |
| Livrables | Routeur `/api/v1/learning/*`, référence API |
| Tests | API, progression, permissions, isolation |
| Critères | 1, 2, 3, 4, 11 |
| Risques | Volume d'événements (regroupement côté client, limites de débit) ; cohérence lors d'un changement d'édition |

### 2.2.5 — Lecteur de cours Web

| Rubrique | Contenu |
|---|---|
| Objectif | Lecteur accessible, bilingue et responsive |
| Fonctionnalités | Pages programme, table des matières, leçon (rendu des blocs), navigation, reprise, verrous, « Mon apprentissage », historique, signalement d'erreur |
| Dépendances | 2.2.4 |
| Livrables | Pages `app/[locale]/apprendre/*`, composants de blocs, tests Vitest et Playwright |
| Tests | Navigation Web, accessibilité, FR/EN, mobile |
| Critères | 1, 2, 3, 12 |
| Risques | Accessibilité des médias (transcriptions) ; performance du rendu des longues leçons |

### 2.2.6 — Interface éditoriale Web

| Rubrique | Contenu |
|---|---|
| Objectif | Outil minimal mais complet pour auteurs et relecteurs |
| Fonctionnalités | Éditeur de blocs par formulaires, aperçu, sources, médias, soumission, liste « À relire », comparaison, approbation ou demande de modifications motivée, publication, archivage, historique des décisions |
| Dépendances | 2.2.2, 2.2.3 |
| Livrables | Pages `app/[locale]/edition/*` |
| Tests | Workflow éditorial de bout en bout (Playwright), permissions, accessibilité |
| Critères | 5, 6, 8, 9, 12 |
| Risques | Ergonomie de l'édition par formulaires (acceptable pour le pilote ; éditeur riche plus tard) |

### 2.2.7 — Pilote PAB Québec (structure) et programme de démonstration

| Rubrique | Contenu |
|---|---|
| Objectif | Préparer le pilote sans rien publier, et prouver le moteur avec un contenu fictif |
| Fonctionnalités | Seed additif `0003` : modules M1, M3, M4, leçons squelettes, objectifs et compétences de travail, portées `to_verify`, sources candidates non vérifiées ; fixture CI « Découvrir la plateforme » (non médicale, `is_medical = false`) publiée **uniquement** dans les bases de test |
| Dépendances | 2.2.1 à 2.2.6 |
| Livrables | Seed, fixture, guide éditorial du pilote (rôles, étapes, gabarit de relecture) |
| Tests | Seed idempotent ; vérification « aucun programme publié » en base de développement ; parcours e2e sur la démonstration |
| Critères | 13 (et 1–3 via la démonstration) |
| Risques | Disponibilité d'un relecteur médical qualifié ; sources à vérifier et dater |

**Option D9** : sous-lot 2.2.8 « MFA TOTP des rôles sensibles » si vous souhaitez l'avancer avant le lot 2.5.

### Ordre et parallélisme

```
2.2.1 ──┬── 2.2.2 ──┬── 2.2.6 ──┐
        ├── 2.2.3 ──┤           ├── 2.2.7
        └── 2.2.4 ──┴── 2.2.5 ──┘
```

---

## 9. Décisions à prendre

| # | Sujet | Proposition |
|---|---|---|
| D1 | Recadrage des lots 2.2 / 2.3 | Moteur pédagogique et workflow éditorial en 2.2 ; quiz et évaluations en 2.3 |
| D2 | Code d'état `in_review` | Renommé `medical_review` par la migration 0005 (contrainte remplacée, retour arrière testé) |
| D3 | Modifications « mineures » sans relecture | Aucune en 2.2 : toute modification d'un contenu approuvé repasse en relecture |
| D4 | Prérequis de leçon | Non retenus : prérequis au niveau module seulement ; ordre conseillé des leçons |
| D5 | Retrait d'urgence d'un contenu publié | Archivage de l'édition + avis aux inscrits ; retour à l'édition précédente si elle reste valide |
| D6 | Structure du pilote | Quatre modules (§ 5.2), le module existant devenant M2 |
| D7 | Analyse antivirus des médias | ClamAV en conteneur local (gratuit) ; média non publiable tant qu'il n'est pas analysé |
| D8 | Conservation de la progression et de l'historique | À fixer avec la politique de confidentialité (proposition : durée du compte + 1 an, puis anonymisation) |
| D9 | Calendrier de la MFA | Lot 2.5 avant production, ou sous-lot 2.2.8 |
| D10 | Relecteur médical du pilote | Personne qualifiée à identifier, invitée par l'administrateur de la plateforme |
| D11 | ADR | Rédiger ADR-0009 « Éditions de programme figées et workflow éditorial à cinq états » au début du sous-lot 2.2.1 |

## 10. Risques

| Risque | Niveau | Atténuation |
|---|---|---|
| Publication involontaire d'un contenu médical non validé | Élevé | Règles en base + API + tests ; pilote en DRAFT ; publication réelle uniquement sur autorisation explicite |
| Absence de relecteur médical qualifié pour le pilote | Élevé (calendrier) | D10 à anticiper ; le moteur se valide avec un contenu fictif non médical |
| Complexité du versionnement (éditions, bascule des inscrits) | Moyen | ADR-0009, tests de progression, migration réversible |
| Médias lourds et accessibilité (sous-titres, transcriptions) | Moyen | Contraintes existantes ; liste blanche ; vérifications axe |
| MFA absente avant production | Élevé avant production | Barrière de mise en production (D9) |
| Dérive du périmètre (quiz, attestations) | Moyen | Hors périmètre explicite ; lot 2.3 |
| Volume d'événements de progression | Faible | Regroupement côté client, idempotence, limites de débit |
