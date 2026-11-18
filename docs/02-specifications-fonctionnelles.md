# 02 — Spécifications fonctionnelles

> Phase 1 · version 0.1 · La colonne **Phase** indique quand la fonctionnalité est livrée. Chaque exigence porte un identifiant réutilisé dans les tests et tickets.

## Correspondance des termes

| Cahier des charges | Dans NurseLearn AI | Table |
|---|---|---|
| Parcours / formation | **Programme** (pour un métier dans une juridiction) | `programs` |
| Cours | **Module** — réutilisable par plusieurs programmes | `modules`, `program_modules` |
| Leçon | **Leçon** — versionnée par langue et juridiction | `lessons`, `lesson_versions` |
| Spécialité | **Spécialité** (groupes A à H du catalogue) | `specialties` |
| Référentiel pédagogique | **Juridiction** | `jurisdictions` |

## F1 — Comptes, rôles, profils

| ID | Exigence | Critères d'acceptation | Phase |
|---|---|---|---|
| F1.1 | Inscription par e-mail et mot de passe | E-mail vérifié avant activation ; mot de passe ≥ 12 caractères, vérifié contre une liste de mots de passe compromis ; hachage Argon2id | 2 |
| F1.2 | Connexion, déconnexion, renouvellement de session | Jeton d'accès 15 min + jeton de rafraîchissement rotatif (haché en base) ; révocation possible | 2 |
| F1.3 | Réinitialisation du mot de passe | Lien à usage unique, expirant en 30 min ; toutes les sessions révoquées après changement | 2 |
| F1.4 | Choix du métier et de la juridiction à l'inscription | Écrans 3 et 4 ; les métiers proposés dépendent de la juridiction ; changement possible plus tard | 2 |
| F1.5 | Rôles étudiant, formateur, administrateur, relecteur médical, administrateur d'établissement | Contrôle RBAC côté API sur chaque route ; rôles globaux ou par établissement | 2 |
| F1.6 | Export et effacement des données personnelles | Export JSON en libre-service ; effacement = anonymisation immédiate + suppression en cascade des données d'apprentissage ; paiements conservés (obligation légale) | 2 (export), 7 (automatisation) |
| F1.7 | Double authentification | TOTP obligatoire pour administrateurs et relecteurs, facultatif pour les autres | 6 |

## F2 — Catalogue et apprentissage

| ID | Exigence | Critères d'acceptation | Phase |
|---|---|---|---|
| F2.1 | Catalogue filtrable | Filtres métier, juridiction, spécialité, niveau (Débutant → Préparation professionnelle), gratuit/premium ; seul le contenu publié est visible | 2 |
| F2.2 | Fiche module | Objectifs, prérequis, durée, compétences visées, références, niveau d'accès | 2 |
| F2.3 | Lecteur de leçons | Blocs : texte, encadré « à retenir », illustration (texte alternatif obligatoire), vidéo (sous-titres obligatoires), avertissement pédagogique, référence ; navigation clavier complète | 2 |
| F2.4 | Contenu adapté à la juridiction | La version affichée est celle de la juridiction de l'apprenant si elle existe, sinon la version commune ; l'origine est indiquée | 2 |
| F2.5 | Prérequis | Un module dont les prérequis ne sont pas terminés est signalé (non bloquant par défaut, bloquant si l'établissement le décide) | 2 |
| F2.6 | Fiches de révision | Générées à partir des blocs « à retenir » ; imprimables | 2 |
| F2.7 | Répétition espacée | Les questions ratées entrent dans la file de révision (SM-2) ; rappel quotidien | 3 |
| F2.8 | Apprentissage adaptatif | Détection des compétences faibles (taux d'erreur, temps) → révisions ciblées proposées | 3 |
| F2.9 | Hors-ligne | Modules téléchargeables (Premium) ; synchronisation des progrès à la reconnexion | 5 |

## F3 — Évaluations

| ID | Exigence | Critères d'acceptation | Phase |
|---|---|---|---|
| F3.1 | Formats de questions | Choix unique, vrai/faux, choix multiples, réponse courte, association, remise en ordre, identification anatomique, étude de cas | 2 (4 premiers), 4 (autres) |
| F3.2 | Correction détaillée | Chaque question a une explication obligatoire et, si possible, une référence | 2 |
| F3.3 | Quiz de leçon et de module | Seuil de réussite configurable ; tentatives illimitées en entraînement | 2 |
| F3.4 | Examens chronométrés et blancs | Minuterie côté serveur ; soumission automatique à l'expiration ; tirage selon un plan (spécialité, niveau, compétence, difficulté) | 4 |
| F3.5 | Reproductibilité | La tentative conserve la version exacte des questions servies | 2 |
| F3.6 | Correction manuelle | Réponses courtes et ECOS corrigées par un formateur avec barème | 4 |
| F3.7 | Statistiques | Historique personnel ; indice de difficulté et de discrimination par question pour les formateurs | 4 |
| F3.8 | ECOS / OSCE | Stations avec consigne, durée, grille d'observation ; volet virtuel + volet présentiel saisi par le formateur | 4 |

## F4 — Tuteur IA

| ID | Exigence | Critères d'acceptation | Phase |
|---|---|---|---|
| F4.1 | Questions-réponses fondées sur des sources | Chaque réponse factuelle cite au moins une source approuvée ; sinon le tuteur le dit | 3 |
| F4.2 | Refus du conseil clinique personnel | Demandes portant sur un patient réel ou sur soi → réponse type orientant vers un professionnel ; incident journalisé | 3 |
| F4.3 | Adaptation au niveau | Le niveau et le métier de l'apprenant sont fournis au modèle ; registre de langue adapté | 3 |
| F4.4 | Modes | Explication, simplification, génération de quiz (questions marquées « IA », non comptées dans les examens officiels de la plateforme), correction commentée, plan de révision, jeu de rôle patient | 3 |
| F4.5 | Quotas | Messages/jour selon le plan ; compteur visible | 3 |
| F4.6 | Signalement | Bouton « signaler une erreur » sur chaque réponse → file de relecture | 3 |

## F5 — Simulations cliniques et atlas

| ID | Exigence | Critères d'acceptation | Phase |
|---|---|---|---|
| F5.1 | Scénario à embranchements | Patient fictif, constantes, choix, conséquences, progression pas à pas, score, correction argumentée | 4 |
| F5.2 | Erreurs critiques | Certains choix sont marqués critiques (ex. omettre l'hygiène des mains) et plafonnent le score | 4 |
| F5.3 | Actes réglementés | Bandeau de supervision affiché avant et après ; aucune compétence au-delà de `theory_proficient` | 4 |
| F5.4 | Atlas 2D | 11 systèmes ; sélection d'un organe → fonction, pathologies courantes, quiz | 4 |
| F5.5 | Extension 3D | Interface de rendu commune (`renderer: 2d / 3d`) ; aucun changement de modèle de données requis | après v1 |

## F6 — Espace étudiant

Tableau de bord : progression globale, modules en cours/terminés, résultats, compétences (avec niveau), calendrier et objectifs hebdomadaires, historique, recommandations, badges, attestations internes. Gamification : points d'expérience, niveaux, défis — **sans classement public par défaut** (choix de confidentialité). Phases 2 (base) et 5 (gamification).

## F7 — Espace formateur et établissement

Création de modules et de leçons (éditeur de blocs), banque de questions, examens, cohortes, suivi des étudiants, correction, validation de compétences en pratique, rapports d'établissement. Classes virtuelles : lien vers un service de visioconférence externe en v1. Phases 2 (création de contenu), 4 (examens), 6 (établissements).

## F8 — Administration

Utilisateurs et rôles, juridictions, métiers et périmètres d'actes, spécialités, programmes, modération et file de signalements, sources du tuteur IA, plans et abonnements, statistiques, journal d'audit. Phase 2 (minimum : utilisateurs, catalogue, relecture), puis enrichissements.

## F9 — Abonnements

Plans Gratuit / Premium / Établissement ; droits calculés à partir du plan actif ; Stripe Checkout et webhooks signés en Phase 6, **inactifs tant que `PAYMENTS_ENABLED=false`** ; mode test Stripe par défaut.

## Exigences non fonctionnelles

| Domaine | Cible v1 |
|---|---|
| Performance | p95 < 300 ms pour les lectures d'API ; première page web < 2,5 s (LCP) sur 4G |
| Disponibilité | 99,5 % mensuelle |
| Accessibilité | WCAG 2.2 AA (contrastes vérifiés automatiquement, navigation clavier, lecteurs d'écran, cibles ≥ 44 px) |
| Sécurité | OWASP ASVS niveau 2 comme référence ; limitation de débit ; journal d'audit |
| Langues | FR et EN complets ; toute chaîne d'interface externalisée ; ajout d'une langue sans modification du code |
| Sobriété | Mode « données réduites » sur mobile (images compressées, vidéos à la demande) |
