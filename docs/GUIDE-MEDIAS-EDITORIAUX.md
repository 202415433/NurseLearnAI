# Guide — Gestion éditoriale des médias protégés

> Étape 2.2.3-e. Public : auteurs, relecteurs, éditeurs et administrateurs de NurseLearn AI.
> Interfaces : `/{fr|en}/edition`. Toutes les données de ce guide sont fictives.

## 1. Principes

- Un fichier téléversé n'est **jamais** visible ni diffusé avant le verdict de l'antivirus ClamAV.
- Le navigateur ne parle qu'au serveur NurseLearn (`/relais/medias/…`) : aucun lien de stockage, aucun
  jeton, aucune adresse présignée. La session est revérifiée à chaque requête.
- Chacun ne voit que les médias de sa **portée** (catalogue public, ou son établissement). Un média d'un
  autre établissement répond « introuvable », sans révéler son existence.
- Aucune publication ne se fait depuis l'espace d'édition.

## 2. Les sept états d'un média

| État | Affichage | Sens |
|---|---|---|
| `pending_upload` | En attente du fichier | Réservation faite, fichier non reçu (expire seule) |
| `quarantined` | En quarantaine — analyse antivirus à venir | Fichier reçu et isolé |
| `scanning` | Analyse antivirus en cours | ClamAV analyse le fichier |
| `clean` | **Sain — disponible** | Seul état prévisualisable et diffusable |
| `infected` | Infecté — bloqué | Menace détectée ; jamais diffusé |
| `scan_failed` | Échec de l'analyse — bloqué | Téléverser à nouveau |
| `revoked` | Révoqué — bloqué | Retiré de la diffusion, définitivement |

L'état est toujours écrit en toutes lettres (jamais signalé par la seule couleur).

## 3. Téléverser

`Édition → Téléverser` : choisir ou déposer un fichier, renseigner le nom affiché, la licence et, pour
une image, le **texte alternatif en français** (obligatoire). Pour des sous-titres, choisir la vidéo.

| Format | Taille maximale |
|---|---|
| Image JPEG, PNG, WebP | 5 Mio |
| PDF | 20 Mio |
| Vidéo MP4 | 200 Mio |
| Sous-titres WebVTT | 1 Mio |

Quotas : 1 Gio par utilisateur, 5 Gio par établissement, 20 Gio pour le catalogue public (affichés dans
la médiathèque). La progression affichée est réelle ; **Annuler** abandonne la réservation (quota libéré).
L'analyse continue si vous quittez la page. Si la session sensible a expiré (30 min d'inactivité),
confirmez votre mot de passe dans l'encadré « Confirmation requise ».

## 4. Médiathèque

Recherche par nom, filtres par format et par état, médias supprimés (case à cocher), pagination. Chaque
ligne indique la taille, la date, l'état antivirus, la conservation légale et le nombre de versions de
leçon qui rattachent le média.

## 5. Rattacher des médias à une leçon

`Édition → Modules → (module) → Médias de la version n` :
- **brouillon** : rattacher (médias de la même portée que la leçon) et retirer ; un média encore en
  analyse peut être rattaché, mais la **soumission exige des médias sains** (et des sous-titres sains
  pour une vidéo) ;
- **version soumise, approuvée, publiée ou archivée** : affichage en lecture seule (« figée »).

## 6. Révoquer (relecteurs plateforme, administration)

Fiche du média → **Révoquer définitivement**, avec un motif (5 à 1 000 caractères). La diffusion est
coupée dès la requête suivante, y compris pendant une lecture vidéo (l'interface l'annonce). Irréversible.

## 7. Conservation légale (administration de la plateforme)

Fiche du média → **Poser** ou **Lever** la conservation, motif obligatoire. Chaque action est journalisée
(acteur, date, motif) dans un historique en ajout seul, visible de l'administration. La conservation
empêche toute purge ; elle **ne rend jamais diffusable** un média infecté ou révoqué.

## 8. Accessibilité

Interfaces FR/EN, navigables au clavier, compatibles lecteurs d'écran (états et progression annoncés),
cibles tactiles ≥ 44 px, sans débordement horizontal sur mobile ; contrôlées par axe (WCAG 2.2 AA).
