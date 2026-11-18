# 01 — Analyse des besoins

> Phase 1 · version 0.1 · 18 novembre 2026 · statut : **à valider par le porteur du projet**

## 1. Vision

NurseLearn AI est une plateforme d'apprentissage (web, Android, iOS) qui mène une personne sans connaissances médicales jusqu'au niveau théorique — et à une préparation pratique encadrée — correspondant à un programme de formation en soins, **dans une juridiction et pour un métier précis**.

La plateforme **forme et prépare** ; elle ne délivre ni diplôme, ni licence, ni autorisation d'exercer. Cette limite est une contrainte de conception, pas une simple mention légale : elle est appliquée dans le modèle de données (voir ADR-0004 et §5 ci-dessous).

## 2. Utilisateurs et besoins

| Persona | Besoin principal | Ce qui compte pour elle |
|---|---|---|
| **Awa, 24 ans, Dakar** — reconversion, aucune base médicale | Comprendre les fondamentaux et savoir si le métier lui convient | Langage simple, progression visible, mobile, coût faible, données mobiles limitées |
| **Kevin, 31 ans, Montréal** — futur préposé aux bénéficiaires (PAB) | Réviser avant et pendant son DEP | Contenu aligné sur le référentiel québécois, quiz, simulations de signes vitaux |
| **Inès, 19 ans, Lyon** — élève aide-soignante (DEAS) | Préparer ses évaluations | Examens blancs, fiches de révision, répétition espacée |
| **Mme Diallo** — formatrice dans un institut | Suivre une cohorte, créer des évaluations | Tableau de bord de groupe, banque de questions, correction des réponses courtes, ECOS |
| **Dr Martin** — relecteur médical | Valider des contenus fiables | File de relecture, comparaison de versions, références, dates de révision |
| **Administrateur plateforme** | Exploiter, modérer, facturer | Gestion des comptes, référentiels, statistiques, journal d'audit |

## 3. Constats structurants

### 3.1 Les titres de métier ne se traduisent pas
« Assistant infirmier », « aide-soignant », « préposé aux bénéficiaires », « infirmier auxiliaire », « infirmier autorisé » recouvrent des formations, des actes permis et des statuts réglementaires différents selon le pays et la province. Au Québec, par exemple, « assistant(e) infirmier(ère)-chef » désigne même un poste d'encadrement. Conséquence : **un métier est toujours défini dans une juridiction** (`professions.jurisdiction_id` obligatoire) et les actes permis sont décrits compétence par compétence (`competency_scopes`), avec la valeur `to_verify` par défaut lorsqu'on ne sait pas.

### 3.2 Le contenu est le vrai goulot d'étranglement
Le logiciel peut être construit en quelques mois. Un catalogue de plus de 100 thèmes × 2 langues × plusieurs juridictions, **validé par des professionnels qualifiés**, demande un effort éditorial bien supérieur. L'architecture doit donc :
- rendre la création, la relecture et la publication de contenu efficaces (outils formateur/relecteur dès la Phase 2) ;
- permettre de publier progressivement, module par module, sans bloquer la plateforme ;
- séparer le contenu commun (anatomie, hygiène des mains) du contenu propre à une juridiction (actes permis, terminologie réglementaire).

### 3.3 L'IA est utile mais doit être bornée
Le tuteur IA apporte explication, reformulation et entraînement 24 h/24. Les risques — affirmations inexactes, conseils cliniques personnalisés, sortie du champ d'exercice — sont traités par conception : réponses fondées uniquement sur des sources **approuvées** (RAG), citations obligatoires, signalement d'incertitude, refus des demandes de conseil clinique personnel, journalisation et signalement d'erreur par l'utilisateur.

### 3.4 Données personnelles multi-juridictions
Les utilisateurs relèvent notamment de la **Loi 25** (Québec), de la **LPRPDE** (Canada), du **RGPD** (France) et de la **loi sénégalaise n° 2008-12** sur les données personnelles (CDP). Exigences retenues : minimisation (pas de date de naissance ni de données de santé de l'apprenant), consentement explicite, droit d'accès et d'effacement, durée de conservation configurable, hébergement à choisir avant la mise en production (voir risques). Une analyse d'impact (AIPD / EFVP) sera menée en Phase 7 avec un conseiller juridique.

## 4. Périmètre

### Inclus dans la v1 (Phases 2 à 7)
Catalogue structuré par métier/juridiction/spécialité/niveau, lecteur de leçons, quiz et examens (12 formats), suivi de progression et de compétences, répétition espacée, tuteur IA RAG, simulations 2D à embranchements, atlas anatomique 2D, application mobile avec hors-ligne, gamification, abonnements (Stripe désactivé par défaut), espace établissement.

### Exclu de la v1 (préparé dans l'architecture)
Simulations et atlas 3D, classes virtuelles en visioconférence (intégration d'un service tiers prévue), langues au-delà de FR/EN, délivrance de toute certification reconnue.

### Jamais
Présentation d'une attestation interne comme diplôme ; utilisation de données réelles de patients par défaut ; conseil clinique personnalisé par l'IA ; validation d'un geste à risque par la seule simulation virtuelle.

## 5. Exigences non négociables et où elles sont garanties

| Exigence du cahier des charges | Mécanisme | Vérifié par |
|---|---|---|
| Attestations ≠ diplômes (§1, §13) | `certificates.kind` verrouillé à `internal_attestation`, mention de non-équivalence obligatoire | `test_certificates_are_internal_attestations_only` |
| Titres non équivalents (§9) | métier rattaché à une juridiction ; périmètre par compétence | `test_professions_are_scoped_per_jurisdiction` |
| Contenu validé par un professionnel (§13) | statut `approved/published` impossible sans relecteur ; relecteur ≠ auteur | `test_lesson_cannot_be_published_without_review`, `test_author_cannot_review_own_lesson` |
| Versionnement + dates de révision (§13) | `lesson_versions` + `next_review_due` obligatoire à la publication | idem |
| Références vérifiables (§13) | URL, DOI ou ISBN obligatoire | `test_medical_reference_must_be_verifiable` |
| Pas de données réelles de patients (§11) | `patient_data_origin = 'fictional'` par défaut ; dé-identifié seulement avec référence d'approbation | `test_deidentified_data_requires_approval_reference` |
| Actes réglementés : rappel de supervision (§4) | notice obligatoire si `involves_regulated_act` | `test_regulated_act_requires_supervision_notice` |
| Geste à risque non validé par simulation seule (§13) | niveau `verified_in_practice` exige un vérificateur humain distinct | `test_practice_verification_requires_another_person` |
| Tuteur IA sur sources vérifiées (§3) | source `approved` impossible sans approbateur | `test_ai_source_needs_approver_and_one_active_prompt` |
| Pas de paiement réel sans configuration (§12) | aucun `stripe_price_id` ; `PAYMENTS_ENABLED=false` ; `livemode=false` par défaut | `test_free_plan_cannot_be_priced_and_no_live_stripe_by_default` |
| Journal d'audit (§11) | table en ajout seul, chaînage SHA-256 | `test_audit_log_is_append_only_and_hash_chained` |
| WCAG 2.2 AA (§13) | contrastes vérifiés ; texte alternatif et sous-titres obligatoires | `contrast.test.mjs`, `test_images_require_alt_text_and_videos_captions` |

## 6. Hypothèses (à confirmer)

1. **Équipe** : la roadmap suppose 3 à 4 développeurs, 1 designer à temps partiel, et un comité de relecture médicale d'au moins 2 professionnels par juridiction lancée.
2. **Juridictions de lancement** : Québec puis France ; Sénégal et autres pays francophones ensuite, sur un référentiel général tant qu'aucun relecteur local n'est engagé.
3. **Fournisseur IA** : configurable ; aucun n'est imposé. Le choix dépend du lieu d'hébergement des données.
4. **Hébergement** : un hébergement au Canada est recommandé pour la clientèle québécoise (Loi 25) ; à arbitrer avec les clients institutionnels français.
5. **Prix** : les montants des plans dans les données de départ (19,99 $ CA/mois) sont des valeurs de remplacement.

## 7. Risques principaux

| Risque | Impact | Probabilité | Réponse |
|---|---|---|---|
| Contenu médical inexact | Très élevé | Moyenne | Relecture à quatre yeux, signalement d'erreur, dates de révision, sources citées |
| Perception de « diplôme » | Élevé (juridique) | Moyenne | Mentions verrouillées, vocabulaire contrôlé dans l'interface (« attestation interne ») |
| Hallucination du tuteur IA | Élevé | Moyenne | RAG sur sources approuvées, citations obligatoires, refus hors corpus, évaluation continue |
| Retard éditorial | Élevé (planning) | Élevée | Lancement sur un parcours restreint (PAB Québec, 6 modules), outils auteur dès la Phase 2 |
| Non-conformité données personnelles | Élevé | Faible à moyenne | Minimisation, conservation configurable, AIPD en Phase 7 |
| Droits sur les illustrations et vidéos | Moyen | Moyenne | Licence et attribution obligatoires par ressource |
