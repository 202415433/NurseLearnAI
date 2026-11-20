# Préparation de la Phase 2.2 — Progression et quiz

> État : **décision en attente**. La Phase 2.2 ne démarre qu'avec votre autorisation explicite, après validation locale complète de la Phase 2.1 (`validate-phase2.1.ps1` sans échec) et, le cas échéant, son intégration dans `main`.

## 1. Ce que la Phase 2.1 fournit à la 2.2

| Besoin de la 2.2 | Fourni par la 2.1 |
|---|---|
| Identifier l'apprenant | Comptes étudiants vérifiés, `AuthContext` (`require_auth`) côté API, `currentUser()` côté Web |
| Rattacher une progression à un établissement | `user_roles.organization_id`, `ctx.member_of(org)`, isolation 404 testée |
| Restreindre l'accès aux programmes privés | `/organizations/{id}/programs` et le modèle de permissions par établissement |
| Formateurs (cohortes, suivi) | Rôle `instructor` attribuable par invitation (administrateur d'établissement ou de la plateforme) |
| Tracer les événements sensibles | `app/core/audit.py` (clés interdites, pseudonymisation) et `audit_logs` immuable |
| Opérations sensibles | `require_sensitive` (30 min d'inactivité au plus) |

## 2. Prérequis avant de démarrer

1. `validate-phase2.1.ps1` : 0 échec sur Windows (non-régression 1.2 et 2.0 incluse).
2. Votre autorisation de pousser la branche `phase-2.1-identity-access`, puis CI verte sur la PR et fusion autorisée.
3. Plan détaillé de la 2.2 (à rédiger, puis à valider) : modèle de progression, quiz et tentatives, règles d'attestation interne (jamais présentée comme diplôme, licence ou autorisation d'exercer).
4. Aucun contenu médical publié : les quiz de démonstration resteront fictifs et non médicaux tant que la relecture à quatre yeux (lot 2.3) n'est pas en place.

## 3. Points à trancher pour la 2.2

| Sujet | Proposition |
|---|---|
| Progression hors ligne (mobile, Phase 5) | Modèle d'événements horodatés, idempotents, prêt pour la synchronisation |
| Visibilité de la progression par les formateurs | Limitée aux cohortes de leur établissement ; consentement de l'étudiant affiché |
| Conservation des tentatives de quiz | Durée à fixer avec la politique de confidentialité |
| Données de démonstration | Fixtures fictives en CI seulement, jamais en base de développement par défaut |

## 4. Risques reportés de la 2.1

- MFA pour `admin` et `medical_reviewer` (lot 2.5, avant production).
- Fournisseur SMTP de production et textes juridiques définitifs.
- Hébergement régional non opérationnel : `DATA_REGION` reste une préparation (ADR-0008).

## 5. Recommandation

**GO conditionnel** pour rédiger le plan de la 2.2 dès que la validation locale de la 2.1 est sans échec. Aucun développement de la 2.2 n'a été commencé.
