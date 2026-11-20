# Préparation du sous-lot 2.2.2 — API éditoriale

> État : **décision en attente**. Le sous-lot 2.2.2 ne démarre qu'avec votre autorisation, après validation Windows du sous-lot 2.2.1 (`validate-phase2.2.1.ps1` sans échec) et son intégration.

## 1. Ce que 2.2.1 fournit à 2.2.2

| Besoin de l'API éditoriale | Fourni par la base (0005) |
|---|---|
| Créer et modifier des brouillons | Versions et éditions insérées en `draft`, modifiables librement tant qu'elles le restent |
| Soumettre, demander des modifications, approuver, publier, archiver | Une mise à jour de `status` dans une transaction qui déclare `nl.actor_id` (et `nl.decision_comment` pour un motif) |
| Contrôle des habilitations et de l'indépendance | Vérifiés par la base, dans la portée de l'établissement ; l'API ajoute ses propres contrôles et des messages clairs |
| Historique et comparaison | `editorial_decisions` (acteur, portée, empreinte, motif) ; versions numérotées et immuables |
| Éditions de programme | `program_releases.manifest`, validé à chaque transition |
| Modèles SQLAlchemy | `ProgramRelease`, `EditorialDecision`, colonnes de `LessonVersion` et `Program` |

## 2. Contrat d'intégration attendu de l'API

1. Chaque requête éditoriale ouvre une transaction, exécute `SELECT set_config('nl.actor_id', :user, true)` avec l'utilisateur authentifié (session du lot 2.1, jamais une valeur fournie par le client), puis la mise à jour.
2. Les erreurs PostgreSQL (`check_violation`, `insufficient_privilege`, `foreign_key_violation`) sont traduites en `problem+json` (409, 403, 422) sans exposer le texte SQL brut.
3. Les opérations éditoriales sont des **opérations sensibles** (`require_sensitive`, 30 minutes) et sont journalisées dans `audit_logs` en plus d'`editorial_decisions`.
4. Le schéma de blocs `blocks/v1` (plan 2.2, § 2.2) est validé par l'API : identifiant de bloc stable et unique, types autorisés, aucun HTML brut.

## 3. Décisions à confirmer avant ou pendant 2.2.2

| # | Sujet | État | Bloquant pour 2.2.2 ? |
|---|---|---|---|
| D2 | Code `medical_review` à la place de `in_review` | Appliqué en 2.2.1 (imposé par les états approuvés) — confirmation formelle souhaitée | Non |
| D11 | ADR-0009 | Rédigé, statut « proposé » | Non (à approuver) |
| D12 | Modules partagés entre programmes : publication refusée si une version remplacée reste suivie par une autre édition publiée | Comportement prudent appliqué | **Oui pour l'ergonomie** : choisir entre coordination des éditions ou versions par programme |
| D13 | Statuts `programs`/`modules` : gardés à trois valeurs, dérivés des éditions (la relecture porte sur les versions et les éditions) | Appliqué (écart au plan § 1.2, justifié dans ADR-0009) | Non |
| D14 | Séparation des rôles PostgreSQL (rôle applicatif sans désactivation des déclencheurs) | Prévu au lot 2.5 | Non (requis avant production) |
| D1, D4–D10 | Voir le plan 2.2 | Non confirmées | Non pour 2.2.2 ; D7 bloque 2.2.3, D9 et D10 bloquent toute publication réelle |

## 4. Prérequis

1. PR #6 (Phase 2.1) fusionnée ; plan 2.2 intégré à `main`.
2. Sous-lot 2.2.1 validé sur Windows, poussé et fusionné sur votre autorisation.
3. Réponse à D12.

## 5. Recommandation

**GO conditionnel** pour 2.2.2 après les prérequis ci-dessus. Aucun développement de 2.2.2 n'a été commencé.
