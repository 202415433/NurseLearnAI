# Plan du lot 2.1 — Identité et accès

> Actualisé le 18 novembre 2026 après l'intégration du lot 2.0 dans `main` (`8631c0f`) · **plan seulement : aucun développement avant votre autorisation explicite**
> Décisions de référence : [ADR-0007](adr/0007-identite-acces-sessions.md) (rôles, invitations, sessions, mineurs) · [ADR-0008](adr/0008-residence-des-donnees.md) (résidence des données)

## 1. Objectif et périmètre

Permettre à une personne majeure de créer un compte étudiant, de vérifier son courriel, de se connecter et de gérer sa session ; permettre aux autorités habilitées d'inviter formateurs, relecteurs médicaux et administrateurs d'établissement ; garantir que chacun n'accède qu'à ce que son rôle et son établissement autorisent.

**Hors périmètre** : progression et quiz (lot 2.2), auteur et relecture (2.3), tuteur IA, paiements, application mobile, authentification fédérée, authentification multifacteur (recommandée au lot 2.5), publication de contenu médical.

## 2. Découpage de la Phase 2

| Lot | Contenu | État |
|---|---|---|
| 2.0 | Sécurité et qualité | ✅ intégré (`main` `8631c0f`, CI 7/7) |
| **2.1** | **Identité et accès** (ce plan) | en attente d'autorisation |
| 2.2 | Progression et quiz | à détailler |
| 2.3 | Auteur et relecture médicale | à détailler |
| 2.4 | Formateurs et établissements (cohortes) | à détailler |
| 2.5 | Conformité, MFA, préparation de la production | à détailler |

## 3. Modèle d'identité

Tables **existantes** utilisées (modélisées en SQLAlchemy, vérifiées colonne par colonne) : `users`, `profiles`, `roles`, `permissions`, `role_permissions`, `user_roles`, `auth_sessions`, `audit_logs`, `organizations`.

**Migration additive `0004_identity`** (aucune migration historique modifiée ; retour arrière testé) :

| Élément | Contenu |
|---|---|
| `users` + colonnes | `adult_attested_at` (déclaration 18 ans et plus), `privacy_policy_version`, `privacy_accepted_at`, `terms_version`, `password_changed_at`, `failed_login_count`, `locked_until` |
| `auth_sessions` + colonnes | `family_id` (famille de rotation), `replaced_by`, `last_seen_at`, `last_authenticated_at` (opérations sensibles), `absolute_expires_at` ; l'empreinte devient un HMAC (`session_hash`) |
| Nouvelle table `one_time_tokens` | `purpose` (`email_verification`, `password_reset`), `user_id`, `token_hash` (HMAC-SHA-256, unique), `expires_at`, `used_at`, `created_at` |
| Nouvelle table `invitations` | `email` (citext), `role_id`, `organization_id`, `invited_by`, `token_hash`, `expires_at`, `accepted_at`, `accepted_by`, `revoked_at`, `created_at` ; contrainte : rôle ≠ `student` ; `org_admin` exige un établissement |
| Déclencheur `nl_guard_role_grant` | Refuse dans `user_roles` tout rôle autre que `student` sans `granted_by` habilité (`user:manage` global, ou `org:manage` dans le même établissement pour `instructor`) |
| Rôles | Codes existants conservés (`student`, `instructor`, `medical_reviewer`, `org_admin`, `admin`) ; correspondance avec les noms d'usage dans l'ADR-0007 |

## 4. Authentification

| Élément | Décision |
|---|---|
| Inscription | Courriel, mot de passe, nom affiché, langue ; **case « J'ai 18 ans ou plus »** non pré-cochée ; acceptation de la politique de confidentialité et des conditions (versions horodatées) ; **aucune pièce d'identité ni date de naissance** ; rôle `student` ajouté côté serveur (aucun champ de rôle accepté) |
| Mots de passe | Argon2id (`argon2-cffi`, paramètres OWASP), 12 à 128 caractères, refus des mots de passe courants (liste locale, aucune requête externe), pas de règles de composition arbitraires |
| Connexion | Réponse identique que le compte existe ou non ; compte `pending_verification` non connectable ; verrouillage progressif après échecs (`failed_login_count`, `locked_until`) |
| Limites de débit | Connexion : par adresse IP et **par compte** ; inscription, renvoi de vérification et réinitialisation : par adresse IP et par courriel ; compteurs Redis partagés (lot 2.0) |
| Premier administrateur | Commande locale `python -m app.cli grant-platform-admin <courriel>`, journalisée ; jamais par HTTP |

## 5. Sessions

Conformes à l'ADR-0007 :

- Identifiant opaque de 256 bits, **empreinte HMAC** seule en base ; cookie `__Host-nl_session` (`HttpOnly`, `Secure` en production, `SameSite=Lax`) posé par le serveur Web (BFF), jamais lisible par JavaScript.
- Inactivité : `SESSION_IDLE_TIMEOUT_MINUTES` (120 par défaut) ; durée absolue : `SESSION_ABSOLUTE_LIFETIME_HOURS` (168) ; **opérations sensibles : 30 minutes d'inactivité maximum (`SENSITIVE_IDLE_TIMEOUT_MINUTES=30`), sinon ré-authentification**.
- Rotation : à la connexion, à l'élévation de privilège, après ré-authentification et toutes les 15 minutes ; réutilisation d'un identifiant remplacé → révocation de la famille.
- Révocation : déconnexion, « déconnecter tous les appareils », changement de mot de passe, suspension, retrait de rôle.
- CSRF : actions serveur Next.js (contrôle d'origine intégré) ; contrôle `Origin`/`Host` et jeton synchroniseur pour toute autre mutation ; aucune mutation en `GET`.

## 6. RBAC

- Autorisation par **permission** (`content:review`, `user:manage`, `org:manage`…), jamais par nom de rôle dans le code.
- Dépendance FastAPI `require(permission, organization=…)` : rôle global, ou rôle propre à l'établissement concerné.
- Matrice de tests automatique : chaque route protégée × chaque rôle → statut attendu (200/403/404).
- Les réponses ne révèlent pas l'existence d'une ressource hors périmètre (404 plutôt que 403 entre établissements).

## 7. Invitations

Conformes à l'ADR-0007 : nominatives, jeton de 256 bits, condensat HMAC, usage unique, `INVITATION_TTL_HOURS` (72 par défaut), révocables, adresse vérifiée identique exigée, autorisations de l'émetteur contrôlées à l'émission **et** à l'acceptation, journal d'audit complet.

| Émetteur | Peut inviter |
|---|---|
| `admin` (plateforme) | `instructor`, `medical_reviewer`, `org_admin` (avec établissement) |
| `org_admin` | `instructor` dans **son** établissement uniquement |
| autres | personne |

## 8. Vérification du courriel

- Lien à usage unique (`one_time_tokens`, `email_verification`), `EMAIL_VERIFICATION_TTL_HOURS` (24 par défaut) ; le compte passe à `active` (contrainte existante : `active` exige `email_verified_at`).
- Renvoi limité en débit ; réponse identique que l'adresse existe ou non.
- Envoi par SMTP : Mailpit en local, **aucun envoi réel sans configuration explicite**.

## 9. Réinitialisation du mot de passe

- Demande : réponse identique que le compte existe ou non ; lien à usage unique (`password_reset`), `PASSWORD_RESET_TTL_MINUTES` (60 par défaut).
- Effet : nouveau mot de passe, **révocation de toutes les sessions**, invalidation des autres jetons de réinitialisation, courriel de notification, entrée d'audit.

## 10. Isolation des établissements

- Rôles d'établissement portés par `user_roles.organization_id` ; toute requête filtrée par les établissements de l'utilisateur.
- Règles de base existantes conservées (isolation des contenus et cohortes, lot 1.1) ; nouveau déclencheur sur les attributions de rôles.
- Tests : un `org_admin` de l'établissement A ne peut ni voir, ni inviter, ni modifier quoi que ce soit dans l'établissement B (API **et** base).

## 11. Audit de sécurité (livrable `SECURITY-AUDIT-PHASE2.1.md`)

Énumération de comptes (inscription, connexion, réinitialisation, renvoi), force brute et verrouillage, fixation et vol de session (rotation, `HttpOnly`, `__Host-`), CSRF, élévation de privilège (inscription avec rôle, invitation forgée ou rejouée, émetteur sans droit), isolation entre établissements, jetons (entropie, condensat, usage unique, expiration), journaux (aucun mot de passe, jeton ni identifiant de session), en-têtes et CSP sur les nouveaux écrans, dépendances (`npm audit`, `pip-audit`).

## 12. Tests d'intégration

| Niveau | Contenu |
|---|---|
| Base (PostgreSQL réel) | Migration `0004` : montée, idempotence, retour arrière, réapplication ; déclencheur d'attribution de rôles ; contraintes des invitations et jetons |
| API (PostgreSQL + Redis réels) | Parcours inscription → vérification → connexion → déconnexion ; verrouillage ; réinitialisation ; rotation et réutilisation de session ; opérations sensibles après 30 min ; matrice RBAC ; invitations (succès, expirée, rejouée, mauvaise adresse, émetteur sans droit) ; isolation des établissements ; limites de débit par compte |
| Unitaires | Hachage et politique de mot de passe, génération et condensat des jetons, calcul des expirations (horloge simulée) |
| Web (Vitest) | Formulaires, messages d'erreur génériques, états de chargement |
| E2E (Playwright + axe) | Inscription, vérification via l'API de Mailpit, connexion, déconnexion, mot de passe oublié, acceptation d'invitation ; accessibilité des nouveaux écrans ; FR/EN |
| Docker | Non-régression `validate-phase1.2.ps1` et `validate-phase2.0.ps1` ; nouveau `validate-phase2.1.ps1` |

## 13. Critères d'acceptation

1. L'inscription publique ne produit **que** le rôle `student` ; aucun autre rôle n'est obtenable sans émetteur habilité (API **et** base).
2. Un compte ne peut se connecter qu'après vérification du courriel ; la déclaration « 18 ans et plus » est obligatoire et horodatée ; aucune pièce d'identité ni date de naissance n'est collectée.
3. Aucun mot de passe, jeton ou identifiant de session en clair dans la base, les journaux ou les réponses.
4. Invitations : nominatives, à usage unique, expirées refusées, rejouées refusées, adresse vérifiée exigée, émetteur contrôlé, tout est journalisé.
5. Sessions : `HttpOnly` + `SameSite=Lax` (+ `Secure` en production), inactivité et durée absolue respectées, **opérations sensibles refusées après 30 min d'inactivité**, rotation, révocation individuelle et « tous les appareils ».
6. Un identifiant de session remplacé et réutilisé révoque la famille.
7. Isolation des établissements vérifiée par tests API et base.
8. Réponses identiques face à l'énumération de comptes ; limites de débit par compte et par adresse fonctionnelles avec plusieurs instances.
9. CI verte (7 jobs et plus), `npm audit` et `pip-audit` sans vulnérabilité élevée ou critique, validation Windows sans échec, non-régression 1.2 et 2.0 complète.
10. `DATA_REGION` configurable et documenté ; aucune affirmation de résidence des données sans infrastructure vérifiée.

## 14. Points ouverts (non bloquants pour démarrer)

| Sujet | Proposition |
|---|---|
| Fournisseur SMTP de production | À choisir avec la région d'hébergement (ADR-0008) |
| Textes de la politique de confidentialité et des conditions | Rédaction juridique hors du code ; le lot 2.1 gère versions et acceptations |
| MFA pour `admin` et `medical_reviewer` | Lot 2.5, avant la production |

Estimation : 2 à 3 semaines pour un développeur.
