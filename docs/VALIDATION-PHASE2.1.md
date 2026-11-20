# Validation de la Phase 2.1 — Identité et accès

Branche : `phase-2.1-identity-access` (locale, **non poussée**, en attente d'autorisation). Base : `main` @ `f540fce`.
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
| Tests API, Vitest, build Next.js, Playwright + axe | exécutés sur Windows (section 3) |

## 3. Résultats sur Windows (`validate-phase2.1.ps1`)

Machine de développement Windows, Docker Desktop, 19 novembre 2026.

| Exécution | Commit | Résultat | Commentaire |
|---|---|---|---|
| n° 1 — `phase2.1-20261119-192741.log` | `352996a` | 38/42 | mypy (annotation manquante), Prettier 3.10 (mise en forme), collision de nom de module pytest (`test_identity.py` en double, qui bloquait la collecte), requête d'audit du script (`text = uuid`) — corrigés dans `b442441` |
| n° 2 — `phase2.1-20261119-194627.log` | `b442441` | 41/42 | seul échec : délai réseau PyPI pendant l'installation (`Read timed out`), avant l'exécution de Ruff/mypy ; script rendu tolérant (`99b53c2`) |
| Relance ciblée Ruff + mypy | `99b53c2` | ✅ | `ruff check` : aucune erreur ; `ruff format --check` : 74 fichiers conformes ; `mypy` : aucune erreur (29 fichiers) |

**Bilan : 42/42 contrôles Phase 2.1 au vert** (41 dans l'exécution n° 2 + Ruff/mypy relancés seuls), dont :

| Ensemble | Résultat |
|---|---|
| Non-régression Phase 1.2 (`phase1.2-20261119-194826.log`) | ✅ 52/52 |
| pytest (base, migrations Alembic, API dont identité et Mailpit, unitaires) | ✅ 147 réussis, 0 ignoré |
| Vitest | ✅ 34/34 |
| Playwright + axe | ✅ 14 réussis, 1 ignoré (test de démonstration réservé à la CI, `E2E_DEMO`) ; les 4 parcours d'identité passent |
| `npm audit` / `pip-audit` | ✅ 0 vulnérabilité |
| ESLint, Prettier, TypeScript | ✅ |
| Parcours API réel avec Mailpit, garde de rôles en base, audit immuable et sans secret, journaux sans secret | ✅ |
| Services Docker (postgres, redis, s3, api, web sains ; mailpit actif) | ✅ |
| Aucun programme publié | ✅ 0 |

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
