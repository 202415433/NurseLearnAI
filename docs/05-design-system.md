# 05 — Design system

> Phase 1 · source : [`packages/design-tokens/src/tokens.json`](../packages/design-tokens/src/tokens.json) · générés : `dist/tokens.css`, `dist/tailwind-preset.cjs`, `dist/tokens.native.json`

## 1. Principes

1. **Clarté clinique** — une information par bloc, hiérarchie nette, vocabulaire simple d'abord, terme technique ensuite (« tension artérielle (TA) »).
2. **Confiance** — chaque contenu affiche sa source, sa date de révision et, si besoin, un bandeau de limite d'exercice ; les contenus générés par IA sont toujours étiquetés.
3. **Accessible par défaut** — WCAG 2.2 AA vérifié par des tests, cibles tactiles ≥ 44 px, focus toujours visible, animations coupées si `prefers-reduced-motion`.
4. **Sobre** — bleu médical, blanc, turquoise ; animations discrètes (120–320 ms) ; pas de décor gratuit.
5. **Couleur jamais seule** — un état (correct, erreur, avertissement) est toujours doublé d'une icône et d'un texte.

## 2. Couleurs

Trois familles : **bleu médical** (`primary`, actions et liens), **turquoise** (`accent` pour le texte, `accent-fill` pour les surfaces et badges de progression), **neutres** bleutés. Les états `success`, `warning`, `danger` ont chacun une variante `-soft` pour les fonds d'encadrés.

| Jeton | Clair | Sombre | Usage |
|---|---|---|---|
| `bg` | `#FFFFFF` | `#0B1422` | Fond de page |
| `surface` | `#F4F7FB` | `#111D2E` | Cartes, panneaux |
| `text` | `#0F1E33` | `#E8EEF6` | Texte principal |
| `text-muted` | `#4A5A70` | `#A9B6C8` | Texte secondaire |
| `primary` | `#0B5CAD` | `#6CB2F2` | Boutons principaux, liens |
| `accent` | `#0F766E` | `#3DD9C5` | Texte turquoise, icônes |
| `accent-fill` | `#2CC5B5` | `#2CC5B5` | Barres de progression, badges |
| `warning` | `#8A5A00` | `#F2C14E` | Bandeaux « acte réglementé / supervision » |
| `danger` | `#B42318` | `#FF8A80` | Erreurs, signes d'alerte |

### Contrastes vérifiés automatiquement (`npm test`)

Seuils : 4,5:1 pour le texte, 3:1 pour les bordures d'interface et l'anneau de focus.

| Paire (texte / fond) | Seuil | Clair | Sombre |
|---|---|---|---|
| text / bg | 4.5 | 16.75 | 15.82 |
| text / surface | 4.5 | 15.59 | 14.52 |
| text / surface-raised | 4.5 | 16.75 | 13.07 |
| text-muted / bg | 4.5 | 7.03 | 8.98 |
| text-muted / surface | 4.5 | 6.54 | 8.24 |
| text-muted / surface-raised | 4.5 | 7.03 | 7.42 |
| primary / bg | 4.5 | 6.67 | 8.17 |
| primary / surface | 4.5 | 6.21 | 7.50 |
| on-primary / primary | 4.5 | 6.67 | 7.90 |
| on-primary / primary-hover | 4.5 | 8.60 | 9.65 |
| primary / primary-soft | 4.5 | 5.72 | 5.94 |
| accent / bg | 4.5 | 5.47 | 10.49 |
| accent / surface | 4.5 | 5.09 | 9.63 |
| accent / accent-soft | 4.5 | 4.83 | 7.57 |
| on-accent-fill / accent-fill | 4.5 | 7.93 | 7.93 |
| success / bg | 4.5 | 5.08 | 10.05 |
| success / surface | 4.5 | 4.73 | 9.23 |
| warning / bg | 4.5 | 5.93 | 11.00 |
| warning / warning-soft | 4.5 | 5.41 | 7.93 |
| danger / bg | 4.5 | 6.57 | 8.09 |
| danger / danger-soft | 4.5 | 5.59 | 6.91 |
| text / warning-soft | 4.5 | 15.28 | 11.40 |
| text / danger-soft | 4.5 | 14.25 | 13.52 |
| border-strong / bg | 3 | 3.47 | 4.73 |
| border-strong / surface | 3 | 3.23 | 4.34 |
| focus / bg | 3 | 6.67 | 9.97 |
| focus / surface | 3 | 6.21 | 9.15 |

## 3. Typographie, espacements, formes

- Police : **Inter** (repli Segoe UI / système) ; chiffres tabulaires pour les constantes vitales et les doses.
- Échelle : 12 · 14 · **16 (base)** · 18 · 20 · 24 · 30 · 36 px ; interligne 1,5 (1,7 dans le lecteur de leçons) ; largeur de lecture ≤ 72 caractères.
- Grille de 4 px (`space-1` = 4 px … `space-16` = 64 px) ; rayons 6 / 10 / 16 px ; trois ombres.
- Thèmes : clair, sombre, ou système (`data-theme` sur `<html>`).

## 4. Composants (bibliothèque Phase 2)

| Catégorie | Composants |
|---|---|
| Base | Bouton (primaire, secondaire, fantôme, danger), lien, champ, sélecteur, case, interrupteur, onglets, infobulle, modale accessible, toast |
| Pédagogie | Carte de module, barre de progression, pastille de niveau, bloc « À retenir », bloc de référence, **bandeau de limite d'exercice**, étiquette « Contenu généré par IA », lecteur vidéo avec sous-titres |
| Évaluation | Question (8 formats), minuterie accessible (annonces ARIA espacées), feuille de correction |
| Simulation | Fiche patient fictif, moniteur de constantes, choix d'action, journal des décisions, débriefing |
| Tableau de bord | Tuile de statistique, graphique de progression, calendrier, carte de compétence (5 niveaux) |

## 5. Écrans (cahier des charges §10)

| # | Écran | Contenu essentiel | Phase |
|---|---|---|---|
| 1 | Accueil | Proposition de valeur, choix de langue et de pays, mention « formation, pas un diplôme » | 2 |
| 2 | Inscription / connexion | E-mail + mot de passe, consentement, vérification | 2 |
| 3 | Choix du métier | Métiers **de la juridiction choisie**, avec rôle et statut réglementaire | 2 |
| 4 | Choix de la spécialité | Groupes A–H, recommandés selon le métier | 2 |
| 5 | Catalogue | Filtres, cartes de modules, accès gratuit/premium | 2 |
| 6 | Détail d'un cours | Objectifs, prérequis, compétences, plan, références | 2 |
| 7 | Lecteur de leçons | Blocs, progression, signalement d'erreur, accès au tuteur | 2 |
| 8 | Tuteur IA | Conversation, sources citées, niveau d'incertitude, quota | 3 |
| 9 | Simulations | Liste, scénario pas à pas, débriefing | 4 |
| 10 | Atlas anatomique | Systèmes, organes, fiches, quiz | 4 |
| 11 | Examens | Liste, consignes, passation chronométrée | 2 (quiz), 4 (examens) |
| 12 | Résultats | Score, correction détaillée, compétences touchées | 2 |
| 13 | Tableau de bord | Progression, objectifs, recommandations, badges | 2 → 5 |
| 14 | Profil | Informations, juridiction, langue, thème, données personnelles | 2 |
| 15 | Abonnements | Plans, quota IA, gestion | 6 |
| 16 | Administration | Utilisateurs, catalogue, relecture, signalements, audit | 2 → 6 |
