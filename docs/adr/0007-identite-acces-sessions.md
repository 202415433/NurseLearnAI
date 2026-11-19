# ADR-0007 — Identité, rôles, invitations et sessions

**Statut** : accepté (décisions du responsable du projet, 18/11/2026) · mise en œuvre : lot 2.1

## Contexte

Le lot 2.1 introduit les comptes et les premières données personnelles. Les rôles existent déjà en base (`roles`, `permissions`, `role_permissions`, `user_roles`) ; il faut fixer qui peut obtenir quel rôle, comment les établissements invitent leurs membres et comment les sessions sont protégées.

## Décisions

### A. Attribution des rôles

| Rôle (nom d'usage) | Code en base | Obtention |
|---|---|---|
| STUDENT | `student` | **Seul rôle attribué par l'inscription publique** |
| INSTRUCTOR | `instructor` | Invitation nominative (administrateur de la plateforme, ou administrateur d'établissement pour son établissement) |
| MEDICAL_REVIEWER | `medical_reviewer` | Invitation nominative par un administrateur de la plateforme uniquement, après vérification hors ligne de l'habilitation professionnelle |
| ORGANIZATION_ADMIN | `org_admin` | Invitation nominative par un administrateur de la plateforme, rôle limité à un établissement |
| PLATFORM_ADMIN | `admin` | Commande d'exploitation locale (`python -m app.cli grant-platform-admin`), jamais par HTTP ; premier administrateur créé ainsi |

Les codes en base ne sont pas renommés (pas de migration inutile) ; la correspondance ci-dessus fait foi.

Garanties :
- l'API d'inscription n'accepte aucun champ de rôle ; le rôle `student` est ajouté côté serveur ;
- toute attribution d'un autre rôle exige un émetteur authentifié **détenant la permission requise dans le même périmètre** (`user:manage` global, ou `org:manage` pour son établissement) ;
- **défense en profondeur en base** (migration additive `0004`) : un déclencheur refuse toute insertion dans `user_roles` d'un rôle autre que `student` sans `granted_by` habilité ;
- chaque attribution et retrait est écrit dans `audit_logs` (journal chaîné par empreintes, déjà en place).

### B. Invitations d'établissement

- Invitation **nominative** (adresse courriel), rôle et établissement fixés à l'émission.
- Jeton de 256 bits issu d'un générateur cryptographique (`secrets.token_urlsafe(32)`), transmis uniquement dans le lien envoyé par courriel.
- Stockage d'un **condensat HMAC-SHA-256** du jeton (clé serveur `TOKEN_HASH_KEY`), jamais du jeton.
- **Usage unique** (`accepted_at` posé dans la même transaction que l'attribution du rôle), **expiration configurable** (`INVITATION_TTL_HOURS`, défaut 72 h), révocable par l'émetteur.
- L'acceptation exige un compte dont l'adresse **vérifiée** correspond exactement à l'adresse invitée.
- Émission, acceptation, révocation et expiration consultée : écrites dans `audit_logs`.
- Contrôle des autorisations de l'émetteur à l'émission **et** à l'acceptation (un émetteur ayant perdu ses droits invalide ses invitations en attente).

### C. Sessions

Architecture : le navigateur ne parle qu'au serveur Web (même origine) ; celui-ci appelle l'API sur le réseau interne.

- **Session opaque côté serveur** : identifiant aléatoire de 256 bits ; seule son empreinte HMAC est stockée (`auth_sessions`). Pas de JWT de longue durée.
- **Cookie** `__Host-nl_session` : `HttpOnly`, `Secure` en production (préfixe `__Host-` imposé), `SameSite=Lax`, `Path=/`, sans `Domain`.
- **CSRF** : mutations par actions serveur Next.js (vérification d'origine intégrée) ; pour toute route de mutation : contrôle `Origin`/`Host` et jeton synchroniseur. Aucune mutation en `GET`.
- **Durées** (configurables, documentées dans `.env.example`) :

| Paramètre | Défaut | Rôle |
|---|---|---|
| `SESSION_IDLE_TIMEOUT_MINUTES` | 120 | Expiration après inactivité (navigation d'apprentissage) |
| `SESSION_ABSOLUTE_LIFETIME_HOURS` | 168 (7 jours) | Durée maximale, quelle que soit l'activité |
| `SENSITIVE_IDLE_TIMEOUT_MINUTES` | **30 (décision)** | Au-delà de 30 min d'inactivité, toute opération sensible exige une nouvelle authentification (mot de passe) |
| `SESSION_ROTATION_MINUTES` | 15 | Rotation périodique de l'identifiant |

- **Opérations sensibles** : changement de mot de passe ou de courriel, émission ou révocation d'invitation, attribution ou retrait de rôle, export ou suppression du compte, déconnexion des autres appareils.
- **Rotation** de l'identifiant à la connexion, à toute élévation de privilège, après une ré-authentification et périodiquement ; un identifiant périmé réutilisé révoque toute la famille de sessions.
- **Révocation** : déconnexion (session courante), « déconnecter tous les appareils », révocation automatique au changement de mot de passe, à la suspension du compte et au retrait d'un rôle.

### D. Mineurs

Le MVP est réservé aux personnes de **18 ans et plus** : déclaration explicite à l'inscription (case non pré-cochée, horodatée), **aucune pièce d'identité ni date de naissance collectée**. Politique de confidentialité versionnée, acceptation horodatée.

## Conséquences

- Migration **additive** `0004` (nouvelles tables et colonnes, déclencheur) ; les migrations `0001`–`0003` restent inchangées.
- Nouvelles dépendances : `argon2-cffi` (hachage des mots de passe), envoi SMTP (Mailpit en local).
- Le serveur Web devient un intermédiaire d'authentification (BFF) : les jetons ne sont jamais exposés au JavaScript du navigateur.
- Authentification multifacteur : non incluse au MVP ; recommandée pour `admin` et `medical_reviewer` avant la production (lot 2.5).
