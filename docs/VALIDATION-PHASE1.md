# Rapport de validation — Phase 1

> 18 novembre 2026 · statut : **livrée, en attente de votre validation avant la Phase 2**

## 1. Objectifs de la phase et résultat

| Objectif (§15) | Livrable | Statut |
|---|---|---|
| Analyse des besoins | `docs/01-analyse-besoins.md` | ✅ |
| Spécifications fonctionnelles | `docs/02-specifications-fonctionnelles.md` | ✅ |
| Architecture technique | `docs/03-architecture-technique.md` + 6 ADR | ✅ |
| Modèle de données | `database/schema/*.sql`, migrations Alembic, données de référence, `docs/04-modele-donnees.md` | ✅ testé |
| Design system | `packages/design-tokens`, `docs/05-design-system.md` | ✅ testé |
| Roadmap | `docs/06-roadmap.md`, `docs/07-plan-phase-2.md` | ✅ |
| Socle du dépôt | docker-compose, `.env.example`, CI GitHub Actions, scripts PowerShell/Bash | ✅ partiellement vérifié (voir §3) |

## 2. Validations exécutées

Environnement : PostgreSQL 16.15, Python 3.13, pytest 9, Node.js 22.

| Vérification | Commande | Résultat |
|---|---|---|
| Schéma + contraintes métier | `pytest database/tests` | **30 tests réussis** |
| Chaîne de migrations 0001 (montée → descente → montée) | `pytest services/api/tests` | **2 tests réussis**, 1 ignoré (pgvector, voir §3) |
| Contrastes WCAG 2.2 AA, thèmes clair et sombre | `npm test` (design-tokens) | **58 tests réussis** |
| Syntaxe des migrations Alembic | `python -m py_compile` | OK |
| Syntaxe docker-compose et CI | analyse YAML | OK |
| Données de référence | requêtes de contrôle | 60 tables, 9 juridictions, 12 métiers, 27 spécialités, 16 permissions, 3 plans |

Les tests ont détecté et fait corriger deux défauts pendant la phase : 7 clés étrangères sans stratégie de suppression explicite, et une contrainte d'unicité de version de leçon qui laissait passer des doublons quand la juridiction est nulle.

## 3. Non vérifié ou incomplet — à connaître

1. **Migration 0002 (pgvector)** : non exécutée ici (extension absente de l'environnement de test). Elle est exécutée par la CI avec l'image `pgvector/pgvector:pg16`.
2. **docker-compose** : syntaxe YAML vérifiée, mais **pas démarré** (Docker indisponible dans l'environnement de travail). La CI exécute `docker compose config`. À tester sur votre poste avec `docker compose up -d`.
3. **CI GitHub Actions** : écrite, jamais exécutée (le dépôt n'est pas encore sur GitHub).
4. **Alembic réel** : les révisions ont été testées avec un module Alembic simulé qui capture et exécute leur SQL ; Alembic lui-même n'a pas pu être installé (pas d'accès réseau aux dépôts de paquets).
5. **Scripts PowerShell** (`db-apply.ps1`, `test-db.ps1`) : non exécutés (pas de Windows dans l'environnement de travail).
6. **Données réglementaires** : les 12 métiers et leurs organismes sont des données de départ marquées `pending_expert_review` ; ils doivent être validés par des professionnels de chaque juridiction.
7. **Aucun contenu pédagogique** n'est encore rédigé : le module de démonstration est prévu en Phase 2 et ne pourra être publié qu'après relecture par un professionnel qualifié.
8. **Prix des plans** : valeurs de remplacement.

## 4. Décisions attendues de votre part

1. Valider (ou amender) l'architecture, le modèle de données et la roadmap.
2. Confirmer le parcours pilote — recommandation : **PAB Québec**, module « Hygiène des mains et prévention des infections ».
3. Indiquer si un comité de relecture médicale est déjà identifié.
4. Accepter les deux rôles ajoutés au cahier des charges : **relecteur médical** et **administrateur d'établissement**.
