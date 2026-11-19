# Plan du lot 2.1 — Identité et accès (proposition, non commencée)

> 18 novembre 2026 · **à valider avant tout développement** · prérequis : lot 2.0 validé ([`VALIDATION-PHASE2.0.md`](VALIDATION-PHASE2.0.md))

## 1. Objectif

Permettre à un étudiant, un formateur, un relecteur médical et un administrateur d'établissement de **créer un compte, se connecter et n'accéder qu'à ce que leur rôle autorise**, avant l'arrivée des données personnelles d'apprentissage (progression, quiz) au lot 2.2.

Aucune fonctionnalité pédagogique n'est incluse. Aucun contenu médical n'est publié.

## 2. Découpage proposé de la Phase 2 (lots 2.0 à 2.5)

| Lot | Contenu | État |
|---|---|---|
| 2.0 | Sécurité et qualité (dépendances, linters, CSP, limites de débit, maintenance) | en validation |
| **2.1** | **Identité et accès** (ce document) | proposé |
| 2.2 | Progression et quiz (données personnelles d'apprentissage) | à détailler |
| 2.3 | Auteur et relecture médicale à quatre yeux, publication | à détailler |
| 2.4 | Espaces formateur et établissement (cohortes, isolation) | à détailler |
| 2.5 | Stabilisation, conformité (export, suppression des données), préparation de la production | à détailler |

## 3. Périmètre du lot 2.1

| Domaine | Contenu |
|---|---|
| Modèles | Modélisation SQLAlchemy des tables d'identité **déjà présentes** : `users`, `profiles`, `roles`, `permissions`, `role_permissions`, `user_roles`, `auth_sessions`, `audit_logs`, `organizations` ; test de cohérence modèle ↔ schéma étendu ; aucune modification de migration historique (toute évolution passe par une nouvelle révision `0004+`) |
| Inscription | E-mail + mot de passe (Argon2id), politique de mot de passe (longueur, listes de mots de passe compromis hors ligne), consentements explicites, langue et juridiction de préférence |
| Vérification | Lien à usage unique et durée limitée envoyé par Mailpit en local ; aucun envoi réel hors configuration explicite |
| Connexion | Jeton d'accès court (15 min) + jeton de rafraîchissement **rotatif** stocké haché (`auth_sessions`), révocation, détection de réutilisation ; cookies `HttpOnly`, `Secure` (HTTPS), `SameSite=Lax` côté Web |
| Réinitialisation | Mot de passe oublié : jeton à usage unique, réponse identique que le compte existe ou non |
| Autorisation | RBAC applicatif sur les rôles existants (`student`, `instructor`, `medical_reviewer`, `organization_admin`, `admin`…) ; contrôle par permission (`content:review`, `competency:verify`…) ; isolation par établissement |
| Sécurité | Limitation de débit renforcée sur la connexion et l'inscription (par adresse et par compte), verrouillage progressif, journal d'audit sans mot de passe ni jeton, CSRF (double cookie) pour les mutations Web |
| Web | Écrans inscription, connexion, vérification, mot de passe oublié, profil minimal, déconnexion ; FR/EN ; WCAG 2.2 AA |
| Données personnelles | Minimisation, finalités documentées, durée de conservation, export JSON du compte (Loi 25 / RGPD) |

Hors périmètre : paiements, tuteur IA, application mobile, fournisseurs d'identité externes (SSO), progression et quiz.

## 4. Critères d'acceptation proposés

1. Un compte ne peut se connecter qu'après vérification de l'e-mail.
2. Aucun mot de passe ni jeton n'apparaît en clair dans la base, les journaux ou les réponses.
3. Un jeton de rafraîchissement réutilisé révoque toute la famille de sessions.
4. Un utilisateur n'accède qu'aux ressources de son rôle et de son établissement (tests d'isolation).
5. Les limites de débit de connexion et d'inscription fonctionnent avec plusieurs instances (Redis).
6. Tests : unitaires, intégration PostgreSQL + Redis, e2e Playwright + axe des nouveaux écrans, tests de sécurité (énumération de comptes, force brute, CSRF, fixation de session).
7. CI verte (7 jobs, dont l'audit de dépendances) et validation Windows sans échec.

## 5. Prérequis et décisions à prendre avant de démarrer

| Sujet | Décision attendue |
|---|---|
| Rôles par défaut à l'inscription | `student` seulement ? Les autres rôles attribués par un administrateur |
| Comptes d'établissement | Création par invitation uniquement ? |
| Durées | Session (30 jours glissants ?), jetons de vérification (24 h ?), réinitialisation (1 h ?) |
| Mineurs | Âge minimal d'inscription et consentement parental selon la juridiction |
| Hébergement des données | Région d'hébergement cible (Canada ?) pour la préparation de la production |

Estimation : 2 à 3 semaines pour un développeur.
