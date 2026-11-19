# Procédure d'archivage de l'ancien dossier `C:\NurseLearnAI`

> Préparée le 18 novembre 2026. **Rien n'a été archivé ni supprimé automatiquement** : c'est à vous de l'exécuter.

## Constat (comparaison du 18/11/2026)

`C:\NurseLearnAI` est une copie de travail de la Phase 1, **sans dépôt Git, sans `.env` et sans journal** (51 fichiers, 256 Ko). Le dossier de référence est `C:\NurseLearnAI-git` (dépôt Git, synchronisé avec GitHub).

| Catégorie | Nombre | Détail |
|---|---|---|
| Fichiers dont le contenu exact existe déjà dans l'historique Git | 50 / 51 | dont `infra/ci/github-actions-ci.yml` (identique à `.github/workflows/ci.yml` de la Phase 1) et `services/api/alembic/versions/_sql.py` (remplacé en Phase 1.1 par `alembic/nl_sql.py`) |
| Contenu absent de Git | 1 | `docs/VALIDATION-PHASE1.md` : seule différence, une phrase obsolète demandant de déplacer `infra/ci/github-actions-ci.yml` vers `.github/workflows/` (fait depuis) |

**Fichiers à conserver : aucun** n'est nécessaire au projet. Une archive compressée est néanmoins recommandée par prudence.

## Procédure (PowerShell)

```powershell
# 1. Vérifier qu'aucun programme n'utilise l'ancien dossier (fermer VS Code, terminaux, etc.)
# 2. Créer une archive datée À CÔTÉ du dossier (rien n'est supprimé)
Compress-Archive -Path C:\NurseLearnAI -DestinationPath C:\NurseLearnAI-archive-20261118.zip
# 3. Vérifier l'archive : 51 fichiers attendus
Add-Type -AssemblyName System.IO.Compression.FileSystem
([System.IO.Compression.ZipFile]::OpenRead("C:\NurseLearnAI-archive-20261118.zip")).Entries.Count
# 4. Renommer l'ancien dossier pour vérifier que rien n'en dépend encore
Rename-Item C:\NurseLearnAI C:\NurseLearnAI-ancien-NE-PAS-UTILISER
# 5. Après quelques jours sans problème, et seulement si vous le décidez : supprimer le dossier renommé
#    (l'archive .zip reste disponible).
```

Les volumes Docker (`nurselearn_pgdata`, `nurselearn_redisdata`, `nurselearn_s3data`) ne sont **pas** liés à l'ancien dossier : ne les supprimez pas.
