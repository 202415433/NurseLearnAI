# Validation de la Phase 2.1 — Identité et accès

Branche : `phase-2.1-identity-access` (locale, **non poussée**). Base : `main` @ `f540fce`.
Références : [plan](PHASE2.1-PLAN.md) · [ADR-0007](adr/0007-identite-acces-sessions.md) · [ADR-0008](adr/0008-residence-des-donnees.md) · [sécurité](SECURITY-IDENTITY-PHASE2.1.md) · [préparation 2.2](PHASE2.2-READINESS.md)

## 1. Procédure

```powershell
cd C:\NurseLearnAI-git
git switch phase-2.1-identity-access
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.1.ps1
```

Le script met à jour `package-lock.json`, lance les audits (`npm audit`, `pip-audit`), la qualité (Ruff, mypy, ESLint, Prettier, TypeScript), la non-régression complète `validate-phase1.2.ps1` (pile Docker, pytest dont identité et Mailpit, Vitest, Playwright + axe dont le parcours d'identité FR/EN), puis les contrôles propres à la 2.1 sur la pile démarrée. Il ne supprime aucun volume ni aucune donnée ; il crée seulement un compte étudiant de validation (`@example.test`) et l'établissement `validation-phase-2-1`. Les sondes qui tentent une écriture interdite (rôle de plateforme, modification du journal) s'exécutent dans une transaction annulée.

## 2. Résultats dans l'environnement de développement (conteneur, sans Docker)

| Contrôle | Résultat |
|---|---|
| Migration `0004_identity` montée / retour arrière / remontée sur PostgreSQL 16 réel | ✅ |
| Tests de base (`database/tests`, dont 10 tests d'identité dans `test_identity_schema.py` : garde de rôles, invitations, jetons, audit) | ✅ 55/55 |
| Ruff (lint + format), mypy | ✅ |
| TypeScript (vérification avec déclarations simplifiées), Prettier, parité FR/EN des textes | ✅ |
| Tests API (`tests/api/test_identity.py`, `test_mailpit.py`), Vitest, build Next.js, Playwright + axe | ⏳ à exécuter sur Windows (`validate-phase2.1.ps1`) — dépendances non installables dans cet environnement |

## 3. Résultats sur Windows (`validate-phase2.1.ps1`)

> ⏳ À compléter après exécution : nombre de contrôles, échecs, journal `docs/validation-logs/phase2.1-<date>.log`.

## 4. Critères d'acceptation (Mission C)

| # | Critère | Preuve automatisée |
|---|---|---|
| 1 | Un étudiant peut créer un compte | `test_register_creates_pending_student_and_sends_local_email`, e2e `parcours complet FR`, script `inscription_202` |
| 2 | Il reçoit un courriel local de vérification | `test_mailpit.py`, e2e (API Mailpit), script `courriel_verification_recu_mailpit` |
| 3 | Il peut vérifier son adresse | `test_verification_token_single_use_and_expiry`, e2e, script `verification_adresse`, `jeton_verification_usage_unique` |
| 4 | Il peut se connecter et se déconnecter | `test_session_is_bearer_only_and_logout_revokes`, e2e, script `connexion`, `deconnexion_revoque_la_session` |
| 5 | Il peut réinitialiser son mot de passe | `test_password_reset_flow`, `test_password_reset_token_expires`, e2e EN, script `reinitialisation_mot_de_passe` |
| 6 | Les sessions expirent et peuvent être révoquées | `test_idle_and_absolute_expiry`, `test_sensitive_operations_require_activity_within_30_minutes`, `test_rotation_grace_and_reuse_detection`, `test_list_and_close_own_sessions_only` |
| 7 | Aucun rôle privilégié ne peut être obtenu | `test_register_rejects_role_fields_and_never_grants_privileges`, `test_student_cannot_escalate`, `test_org_admin_least_privilege`, tests de base (garde), script `garde_base_role_plateforme`, `inscription_role_impose_refuse` |
| 8 | Les invitations sont sécurisées | `test_invitation_bound_to_email_single_use_expiry_and_revocation`, `test_invitation_rejected_when_inviter_lost_rights`, tests de base (invitations) |
| 9 | L'isolation des établissements est vérifiée | `test_organization_isolation` (identifiant direct, listes, inexistant, mal formé, modifications) |
| 10 | Les événements de sécurité sont audités | `test_admin_invitation_grants_role_with_audit`, `test_role_revocation_closes_sessions_and_is_audited`, `test_audit_never_contains_secrets`, script `evenements_audites`, `audit_sans_secret`, `audit_immuable` |
| 11 | L'interface fonctionne en FR et EN | Vitest (parité des clés), e2e FR et EN |
| 12 | Les contrôles d'accessibilité passent | axe (WCAG 2.2 AA, aucune violation sérieuse ou critique) sur inscription, connexion, vérification, mot de passe oublié, réinitialisation, compte, conditions, confidentialité |
| 13 | Les migrations fonctionnent sur PostgreSQL réel | `test_migrations.py` (Alembic), tests de base, script `migration_0004_identity_en_tete`, `schema_tete_verifie` |
| 14 | Les services Docker restent opérationnels | non-régression 1.2, script `docker_services_operationnels` |
| 15 | Les tests existants passent toujours | non-régression 1.2 et contrôles 2.0 repris dans le script |
| 16 | Aucun contenu médical non validé n'est publié | script `aucun_contenu_medical_publie`, `api_pilote_en_preparation` (1.2) ; pilote PAB Québec toujours en brouillon |

## 5. Points d'attention

- `package-lock.json` : aucune nouvelle dépendance npm ; le script régénère tout de même le lock pour vérifier sa cohérence.
- Nouvelle dépendance Python : `argon2-cffi` (vérifiée par `pip-audit`).
- `TOKEN_HASH_KEY` : facultative en développement (dérivée), **obligatoire en production**.
