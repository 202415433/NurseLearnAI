# Sécurité de l'identité et des accès — Phase 2.1

Références : [ADR-0007](adr/0007-identite-acces-sessions.md) (identité, sessions), [ADR-0008](adr/0008-residence-des-donnees.md) (résidence des données), [plan 2.1](PHASE2.1-PLAN.md).
Ce document décrit les mesures en place et les risques résiduels. Il complète [`SECURITY-AUDIT-PHASE2.0.md`](SECURITY-AUDIT-PHASE2.0.md).

## 1. Mots de passe

| Mesure | Mise en œuvre |
|---|---|
| Hachage | Argon2id (`argon2-cffi`, paramètres par défaut de la bibliothèque, ré-hachage automatique à la connexion si les paramètres évoluent) |
| Politique | 12 à 128 caractères, normalisation Unicode NFKC, refus des mots de passe courants (liste locale) et de ceux qui reprennent la partie locale de l'adresse ; aucune règle de composition imposée (approche NIST SP 800-63B) |
| Égalité de temps | Vérification contre une empreinte factice quand le compte n'existe pas |
| Verrouillage | Toutes les 5 erreurs (`LOGIN_MAX_FAILURES`), compte verrouillé 15 min, courriel d'avertissement ; réponse 401 identique au mauvais mot de passe |
| Changement | Exige le mot de passe actuel et une session « fraîche » ; ferme les autres sessions |
| Réinitialisation | Jeton à usage unique, 60 min, invalide les jetons précédents, ferme **toutes** les sessions, courriel de confirmation |

## 2. Sessions (BFF, ADR-0007)

- Identifiant opaque de 256 bits ; la base ne conserve que son HMAC-SHA256 (`TOKEN_HASH_KEY`, obligatoire en production, ≥ 32 caractères).
- Le navigateur ne détient qu'un cookie `__Host-nl_session` (`HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`) posé par le serveur Next.js. En HTTP local seulement : `nl_session` sans `Secure` (`SESSION_COOKIE_SECURE=false`).
- L'API **n'accepte que** `Authorization: Bearer` et ignore les cookies : elle n'est pas exposée au CSRF.
- Inactivité : 120 min ; durée absolue : 7 jours (héritée par les rotations) ; **opérations sensibles refusées après 30 min d'inactivité** (changement de mot de passe, déconnexion de tous les appareils, invitations, retrait de rôles) jusqu'à confirmation du mot de passe.
- Rotation toutes les 15 min (nouvelle ligne dans la même famille, ancienne révoquée) ; délai de grâce de 60 s pour les requêtes concurrentes ; **réutilisation d'un identifiant remplacé = révocation de toute la famille** et événement `auth.session_reuse`.
- Révocation : déconnexion, déconnexion de tous les appareils, fermeture d'une session depuis le compte, réinitialisation, changement de mot de passe, retrait de rôle, suspension.
- Le middleware Next.js rafraîchit la session au plus une fois par minute (cookie `nl_chk`) et efface le cookie d'une session refusée.

## 3. CSRF et origine

- Formulaires en actions serveur Next.js (protection d'origine intégrée) **plus** contrôle explicite `Origin`/`Host`/`Sec-Fetch-Site` dans chaque action (`lib/csrf.ts`).
- `SameSite=Lax` sur le cookie ; aucune mutation en GET.
- Redirection après connexion limitée aux chemins internes de la langue courante (`safeNext`) : pas de redirection ouverte.
- Les messages affichés proviennent d'une liste fermée de codes (`?etat=`/`?erreur=`) : aucun texte de l'adresse n'est réfléchi.

## 4. Anti-énumération et anti-automatisation

- Inscription, renvoi de vérification et mot de passe oublié répondent toujours 202 avec le même corps ; une inscription sur une adresse existante envoie un courriel « compte existant » au titulaire.
- Connexion : 401 identique (adresse inconnue, mauvais mot de passe, compte verrouillé). Seul le cas « adresse non vérifiée » est distingué, **après** un mot de passe correct.
- Limites Redis par adresse IP **et** par adresse courriel (compartiments séparés : inscription, connexion, renvoi, mot de passe oublié), partagées entre instances ; dépassement silencieux pour les actions par courriel.
- Champ piège `website` (invisible, `aria-hidden`, hors tabulation) ; rempli = rien n'est créé, réponse identique, événement `user.register_blocked`.

## 5. Rôles, permissions et élévation de privilège

- L'inscription publique ne donne que `student` ; tout champ inattendu (`role`, etc.) est refusé (422, `extra="forbid"`).
- Permissions vérifiées dans l'API (`AuthContext.can(permission, organisation)`), jamais seulement dans l'interface.
- **Garde en base** (`trg_user_roles_guard`, `nl_role_grant_allowed`) : un rôle autre que `student` n'est inséré ou modifié qu'avec un émetteur habilité — `user:manage` global (administrateur de la plateforme) ou, pour `instructor` uniquement, `org:manage` dans le **même** établissement. Pas d'auto-attribution. Un administrateur d'établissement **ne peut jamais** attribuer `admin`, `medical_reviewer` ni `org_admin`.
- Contournement réservé à l'exploitation et aux tests : `SELECT set_config('nl.role_grant_bypass','on', true)` dans une transaction (CLI `grant-platform-admin`, journalisée).
- Invitations : nominatives, jeton HMAC à usage unique, 72 h, adresse vérifiée identique exigée, droits de l'émetteur revérifiés **à l'acceptation**, une seule invitation en attente par (adresse, rôle, établissement), révocables ; `student` et `admin` ne s'obtiennent jamais par invitation (déclencheur `trg_invitations_check`).
- Retrait de rôle : ferme les sessions de la personne concernée.

## 6. Isolation des établissements

Toute ressource d'établissement (membres, invitations, programmes privés, retrait de rôle) répond **404** hors du périmètre de l'utilisateur — identifiant direct, liste, identifiant inexistant ou mal formé — pour ne rien divulguer. La liste globale des établissements (`/admin/organizations`) est réservée à `user:manage`. Les règles de base existantes (lot 1.1) restent en place.

## 7. Journal d'audit

- `audit_logs` est en ajout seul avec chaînage d'empreintes (déclencheurs existants) : ni modification, ni suppression.
- Événements : `user.register`, `user.register_blocked`, `user.email_verified`, `auth.login`, `auth.login_failed` (dont le verrouillage, motif en métadonnée), `auth.reauthenticated`, `auth.reauth_failed`, `auth.logout`, `auth.logout_all`, `auth.session_reuse`, `session.revoked`, `password.reset_requested`, `password.reset`, `password.changed`, `invitation.created`, `invitation.revoked`, `invitation.accepted`, `invitation.rejected` (adresse différente), `role.granted`, `role.revoked`.
- `app/core/audit.py` **refuse** les clés `password`, `token`, `session_token`, `secret`, `hash`, `password_hash`, `email` ; une adresse tentée n'est enregistrée que pseudonymisée (HMAC tronqué).
- Aucun mot de passe, jeton ou identifiant de session dans les journaux applicatifs (vérifié par tests et par `validate-phase2.1.ps1`).

## 8. Courriels

Envoi SMTP local vers **Mailpit** (aucun service payant). Modèles FR/EN, liens vers `PUBLIC_WEB_URL`. Une page de confirmation exige un clic avant de consommer un jeton (les analyseurs de liens des messageries ne l'invalident pas). Le fournisseur de production reste à choisir avec la région d'hébergement (ADR-0008).

## 9. Âge et consentements

Déclaration « 18 ans et plus » obligatoire et horodatée (`adult_attested_at`) ; aucune date de naissance ni pièce d'identité. Acceptation explicite et horodatée des conditions et de la politique de confidentialité avec leur version (`terms_version`, `privacy_policy_version`). Les textes sont des versions de travail à faire valider juridiquement.

## 10. Résidence des données

`DATA_REGION` (défaut `local-dev`) est exposé par `/ready` et prévu pour l'acheminement futur ; **aucun hébergement multirégional n'est opérationnel** et aucune résidence n'est affirmée tant que l'infrastructure de production n'est pas configurée et vérifiée (ADR-0008).

## 11. Risques résiduels

| Risque | Niveau | Traitement prévu |
|---|---|---|
| Pas d'authentification multifacteur pour `admin` et `medical_reviewer` | Élevé avant production | Lot 2.5 (avant toute mise en production) |
| Limites de débit en mode « fail-open » si Redis est indisponible | Moyen | Alerte de supervision (lot 2.5) ; verrouillage de compte en base reste actif |
| Liste locale de mots de passe courants limitée | Faible | Vérification k-anonyme type HIBP envisageable (sans service payant) |
| Textes juridiques non validés | Moyen | Validation juridique avant production |
| Fournisseur SMTP de production non choisi (SPF/DKIM/DMARC) | Moyen | Avec la région d'hébergement |
| `TOKEN_HASH_KEY` dérivée de `DATABASE_URL` en développement | Faible (dev seulement) | Obligatoire en production (démarrage refusé sinon) |
