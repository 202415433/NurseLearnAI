# ADR-0008 — Résidence des données et régions d'hébergement

**Statut** : accepté (décision du responsable du projet, 18/11/2026) · **infrastructure non encore configurée**

> **Avertissement** : aucune résidence des données n'est garantie à ce jour. Toute l'application tourne sur des postes de développement et dans la CI GitHub, avec des données fictives. La résidence ne pourra être affirmée qu'après configuration **et vérification** de l'infrastructure réelle (contrats, régions effectives, sauvegardes, journaux).

## Décision

- Architecture **compatible avec plusieurs régions** : chaque déploiement est rattaché à une région unique, déclarée par configuration ; aucune donnée personnelle ne circule entre régions.
- **Pilote Québec : région canadienne en priorité** (par exemple une région infonuagique située au Canada), à confirmer lors du choix de l'hébergeur.

## Paramètres de région (lot 2.1 : configuration seulement)

| Paramètre | Exemple | Usage |
|---|---|---|
| `DATA_REGION` | `ca-central` | Région déclarée du déploiement, exposée dans `/ready` (sans autre détail) et dans les journaux d'exploitation |
| `DATABASE_URL`, `REDIS_URL`, `S3_ENDPOINT`, `S3_REGION` | — | Doivent pointer vers des services de la même région |
| `SMTP_HOST` | — | Fournisseur de courriel : région à documenter (voir « Services externes ») |
| `BACKUP_REGION` | `ca-central` (ou seconde région canadienne) | Région des sauvegardes |

Au démarrage en production, l'API refusera de démarrer si `DATA_REGION` n'est pas défini (lot 2.5).

## Inventaire des flux (à tenir à jour)

| Élément | Contenu personnel | Localisation actuelle | Cible pilote Québec |
|---|---|---|---|
| PostgreSQL | Comptes, profils, sessions (empreintes), journal d'audit | Poste local (Docker) | Région canadienne |
| Redis | Compteurs de limitation de débit (empreintes tronquées, 70 s) | Poste local | Même région que l'API |
| Stockage S3 (RustFS) | Aucun pour l'instant (futurs médias) | Poste local | Région canadienne |
| **Sauvegardes** | Copie de la base | Aucune (développement) | Région canadienne, chiffrées, durée de conservation définie |
| **Journaux** applicatifs | Aucune donnée personnelle (méthode, chemin, statut, durée, identifiant de requête ; pas d'IP en clair) | Sortie standard des conteneurs | Même région ; conservation limitée |
| Journal d'audit (`audit_logs`) | Identifiant d'utilisateur, action, IP | Base de données | Avec la base |
| **Courriel** (vérification, réinitialisation, invitations) | Adresse, lien à usage unique | Mailpit (local, aucun envoi réel) | Fournisseur SMTP à choisir ; région et sous-traitants à documenter |
| CI GitHub | Aucune (données fictives uniquement) | États-Unis (GitHub) | Inchangé : jamais de données réelles en CI |
| Tuteur IA, paiements | Hors périmètre | — | Évaluation requise avant toute activation |

## Transferts internationaux

Aucun transfert de données personnelles n'est prévu. Avant toute communication de renseignements personnels hors du Québec (fournisseur de courriel, hébergeur, sous-traitant), une **évaluation des facteurs relatifs à la vie privée** est requise (Loi 25), ainsi que les garanties contractuelles appropriées ; le transfert est alors inscrit dans ce tableau.

## Conséquences

- Le lot 2.1 ajoute `DATA_REGION` et la documentation ; il n'engage aucun hébergeur.
- La vérification effective (régions, sauvegardes, journaux, sous-traitants) est un critère de passage en production (lot 2.5).
