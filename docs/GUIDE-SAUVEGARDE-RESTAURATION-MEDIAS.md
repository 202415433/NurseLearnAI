# Guide — Sauvegarde chiffrée et restauration isolée des médias protégés (D22)

> Étape 2.2.3-e. Outils : [`scripts/backup-restore-media.ps1`](../scripts/backup-restore-media.ps1),
> [`scripts/media_backup.py`](../scripts/media_backup.py), image locale
> [`infra/backup/Dockerfile`](../infra/backup/Dockerfile). Environnement de développement Windows +
> Docker Desktop. **Ce n'est pas encore une procédure de production** (voir § 7).

## 1. Ce qui est sauvegardé

| Élément | Source | Identité utilisée |
|---|---|---|
| Base PostgreSQL complète (schéma, données, droits de la base) | `pg_dump --create`, format personnalisé, une transaction | connexion d'administration (lecture) |
| Rôles PostgreSQL (`nl_owner`, `nl_api`, `nl_media_scanner`, …) | `pg_dumpall --roles-only --no-role-passwords` | idem — **aucun mot de passe** sauvegardé |
| Objets de quarantaine | liste et lecture de `nl-media-quarantine` | `nl-media-scanner` (R3, lecture et liste seulement utilisées) |
| Objets sains | lecture de `nl-media-clean` | `nl-media-reader` (R3, lecture seule) |
| Métadonnées des médias (état, empreinte, clés, conservation, époque) | `media_assets` | lecture |
| Manifeste détaillé (comptes des tables, inventaire des objets avec SHA-256, compartiments, révision Alembic) | calculé | — |

La **clé racine RustFS n'est jamais utilisée**. Aucun objet n'est écrit dans le stockage de travail.

## 2. Format et chiffrement

- Archive `backups\nurselearn-media-<date>.nlbk` : tar chiffré **AES-256-GCM** par blocs de 4 Mio ;
  nonce = préfixe aléatoire + numéro de bloc ; données associées = en-tête, numéro de bloc, indicateur
  de fin. Toute altération, permutation, suppression de bloc ou troncature est détectée.
- Le clair n'existe que dans le **tmpfs** du conteneur d'outillage (mémoire), jamais sur le disque hôte.
- Manifeste public `backups\nurselearn-media-<date>.manifest.json` : empreinte SHA-256 de l'archive,
  taille, comptes, états antivirus, nombre d'objets, empreinte **de la clé** (pas la clé).
- Clé : `BACKUP_ENCRYPTION_KEY` (64 caractères hexadécimaux) dans `.env`, créée au premier passage,
  jamais affichée, jamais archivée, jamais versionnée (`backups/` et `.env` sont hors Git et exclus du
  contexte Docker). **Sans cette clé, aucune restauration n'est possible : conservez-en une copie hors
  du poste (gestionnaire de secrets).**

## 3. Utilisation

```powershell
cd C:\NurseLearnAI-git
docker compose --profile media up -d --wait
powershell -ExecutionPolicy Bypass -File .\scripts\backup-restore-media.ps1
```

Le script enchaîne : sauvegarde → vérifications → restauration isolée → contrôles → démontage. Résultat
détaillé (sans secret) : `docs\validation-logs\backup-restore-<date>.json`.

## 4. Vérifications d'intégrité (à chaque sauvegarde)

| Contrôle | Attendu |
|---|---|
| Empreinte de l'archive = manifeste ; déchiffrement complet authentifié | réussite |
| Archive altérée d'un bit (simulation en mémoire) | **refus** |
| Mauvaise clé | **refus** |

## 5. Restauration isolée

1. Réseau Docker **interne** dédié `nl-restore-<date>` (aucune sortie, aucun accès à `nurselearn_default`).
2. PostgreSQL (`pgvector/pgvector:pg16`) et RustFS (`rustfs/rustfs:1.0.0`) **jetables**, stockage en
   **mémoire** (tmpfs) : aucun volume Docker n'est créé.
3. Rôles (sans mot de passe), puis base recréée sous son nom d'origine **dans le serveur isolé**
   (`pg_restore --create`) ; objets remis dans des compartiments jetables.
4. Contrôles :

| Contrôle | Règle |
|---|---|
| Tables | comptes identiques au manifeste (15 tables, dont médias, verdicts, rattachements, conservations, audit) |
| Révision | `alembic_version` identique |
| Rattachements | aucun rattachement orphelin ; nombre identique |
| Objets | nombre et octets identiques au manifeste |
| Empreintes | SHA-256 **des octets restaurés** = `media_assets.sha256`, taille = `byte_size` (sain et quarantaine) |
| Présence | tout média sain a son objet sain ; tout média reçu non purgé a son objet de quarantaine |
| États antivirus | répartition identique ; tout média sain a un verdict ClamAV « clean » sur les mêmes octets |
| Permissions (D14, 0010) | `verify_head.sql` vert ; rôles sans super-privilège ; `nl_owner` propriétaire ; `nl_api` sans écriture directe ; rôles sans mot de passe |

5. Démontage (conteneurs et réseau jetables) ; contrôle : **aucun volume créé ni supprimé**, base de
   travail **inchangée** et **injoignable** depuis le réseau isolé.

La restauration **ne vise jamais la base de travail** : l'outil refuse de s'exécuter si une connexion à la
base de travail (`SRC_HOST`) est fournie en mode restauration.

## 6. Restaurer réellement (incident)

Hors du périmètre de 2.2.3-e. Principe documenté : restaurer d'abord dans l'environnement isolé, valider
les contrôles du § 5, puis décider (procédure à écrire avant la production) d'une bascule contrôlée, avec
sauvegarde préalable de l'état courant et nouveaux mots de passe des rôles (`ALTER ROLE … PASSWORD`).

## 7. Limites (risques résiduels E3-1 à E3-4)

- Sauvegarde **manuelle**, locale, sans copie hors site ni rétention planifiée.
- Instantané base et lecture des objets **non atomiques** : un téléversement pendant la sauvegarde peut
  produire un objet sans média (signalé dans le manifeste) ; à lancer en période calme.
- La quarantaine est lue avec `nl-media-scanner` (qui peut aussi écrire dans le compartiment sain ; seule
  la lecture est utilisée) : une identité **dédiée en lecture seule** est recommandée.
- Clé dans `.env` local : production = coffre de secrets, rotation, double garde.
