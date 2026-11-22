# Préparation — Étape 2.2.3-c : ClamAV, worker d'analyse et reprise après panne

> Préparation seulement : **aucun développement 2.2.3-c n'est engagé**. Il faut d'abord que 2.2.3-b
> soit intégrée, puis une autorisation explicite. Références : [plan 2.2.3](PHASE2.2.3-PLAN.md) § 8 ·
> [validation 2.2.3-b](VALIDATION-PHASE2.2.3B.md) · [ADR-0011](adr/0011-medias-antivirus.md).

## 1. Ce que 2.2.3-b fournit

- Des médias en état `quarantined`, validés et normalisés :
  - images ré-encodées sans métadonnées ;
  - VTT réécrits ;
  - PDF et MP4 contrôlés structurellement.
- L'empreinte SHA-256 du fichier stocké, dans `media_assets.sha256` et en métadonnée S3.
- Un objet privé dans `nl-media-quarantine` sous une clé aléatoire (`quarantine_key`).
- Le compartiment `nl-media-clean`, configuré mais **vide**.
- Le client S3 interne (`app/domains/media/storage.py`) et le modèle de bac à sable borné (`sandbox.py`).
- Un journal d'audit sans données sensibles.

## 2. Prérequis ClamAV

| Élément | Exigence |
|---|---|
| Image | `clamav/clamav` (GPL-2.0, gratuite), version **épinglée**, profil Compose `media` (D7) |
| Ressources | ≈ 1,5 à 2 Go de mémoire avec signatures : à documenter pour Docker Desktop (WSL 2). Limites `mem_limit` et `cpus` |
| Signatures | Volume **persistant** dédié (jamais supprimé). `freshclam` intégré. Santé « dégradée » si les signatures ont plus de 48 h (S9) |
| Configuration `clamd` | `StreamMaxLength` ≥ 200 Mio ; `MaxFileSize`, `MaxScanSize`, `MaxScanTime` bornés ; `AlertEncrypted`, `AlertBrokenExecutables`, `AlertOLE2Macros` activés |
| Réseau | `clamd` joignable **seulement** par le worker (réseau interne) ; aucun port publié |
| CI | Job d'intégration avec **ClamAV réel** et le fichier de test **EICAR** (D23), produit à l'exécution et jamais versionné ; analyseur simulé pour les tests unitaires |
| Windows | Premier téléchargement des signatures long (plusieurs minutes) : attente de santé dans le script de validation |

## 3. Travaux de 2.2.3-c (à autoriser)

1. **Migration 0008** (additive) :
   - rôle système `media_scanner` portant seul `media:scan` (S14) ;
   - compte technique du worker, sans session interactive ;
   - éventuels index du réconciliateur.

   Aucune réécriture de 0007.
2. **Worker `media-scan`** :
   - conteneur dédié, sans privilège, système de fichiers en lecture seule hors répertoire temporaire (S5) ;
   - Redis Streams avec groupe de consommateurs ;
   - idempotence par `(media_id, sha256)` (S7) ;
   - reprise des messages non acquittés (S8).
3. **Transitions automatiques seulement** : `quarantined → scanning → clean | infected | scan_failed`.
   - Chaque verdict est inscrit dans `media_scan_events` (ajout seul), avec la version des signatures.
   - **Aucune** transition manuelle vers `clean`.
4. **Copie vers `nl-media-clean`** : uniquement après un verdict OK avec des signatures fraîches.
   - Contrôle préalable que le SHA-256 relu est égal à `media_assets.sha256`.
   - La clé saine est aléatoire et distincte.
5. **Refus par défaut** (S9) : `clamd` arrêté, délai dépassé, réponse inattendue ou signatures périmées
   ⇒ `scan_failed`, jamais `clean`.
6. **Réconciliateur** :
   - médias `quarantined` sans message (perte de Redis) ;
   - objets orphelins en quarantaine ;
   - réservations expirées.
7. **Nouvelle analyse hebdomadaire** : une menace détectée entraîne la **révocation**.
8. Option à trancher : déplacer le ré-encodage des images dans le worker isolé (le bac à sable de
   l'API reste alors un premier filtre).

## 4. Tests obligatoires (rappel du plan)

- EICAR → `infected` (ClamAV réel) ;
- `clamd` arrêté → `scan_failed` ;
- délai dépassé ;
- signatures périmées ;
- message rejoué sans effet ;
- worker tué puis repris ;
- perte de Redis puis réconciliation ;
- nouvelle analyse détectant une menace → révocation ;
- limites de ressources respectées ;
- **aucun** chemin ne produit `clean` sans verdict OK.

## 5. Interdits maintenus

- Aucune diffusion avant 2.2.3-d.
- Aucune URL publique ou présignée.
- Aucune lecture étudiante d'un média non `clean`.
- Aucun service payant.
- Aucune suppression de volume.
- Aucune publication médicale.
