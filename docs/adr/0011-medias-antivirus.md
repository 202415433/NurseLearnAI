# ADR-0011 — Analyse antivirus des médias (D7)

**Statut** : **proposée**, pas encore approuvée. Elle concerne l'étape 2.2.3 ([plan](../PHASE2.2.3-PLAN.md)).
**Contexte de décision** : D7 de [`PHASE2.2-PLAN.md`](../PHASE2.2-PLAN.md) : « ClamAV en conteneur local (gratuit) ; média non publiable tant qu'il n'est pas analysé ».

## Contexte

L'étape 2.2.3 permet de téléverser des images, vidéos, sous-titres et PDF, puis de les diffuser aux
étudiants. Tout fichier téléversé est potentiellement hostile : par compromission du compte d'un
éditeur, par erreur ou par un document tiers piégé.

Contraintes du projet :
- aucun service payant ;
- données hébergées sous notre contrôle (ADR-0008) ;
- fonctionnement en développement local, Docker Desktop sous Windows compris ;
- aucune donnée de patient réelle.

**Règle impérative.** Aucun fichier non analysé, suspect ou rejeté n'est accessible aux étudiants, ni
à personne d'autre. En cas de panne de l'antivirus, l'accès est **refusé** jusqu'à validation.

## Options comparées

Le moteur est le même dans toutes les options, ClamAV (`clamd`, GPL-2.0, gratuit, signatures
mises à jour par `freshclam`). Ce qui change, c'est le **mode d'intégration**.

| Critère | A. ClamAV en Docker, appel direct sans file | B. Analyse **synchrone** pendant la requête de téléversement | C. Analyse **asynchrone** : Redis Streams + worker dédié |
|---|---|---|---|
| Principe | L'API envoie le fichier à `clamd` (INSTREAM) quand elle le juge utile | La requête attend le verdict de `clamd` avant de répondre | La requête dépose en quarantaine et répond `202 pending` ; un worker analyse et publie le verdict |
| Temps de réponse | Variable | Lent pour les gros fichiers : une vidéo de 200 Mo prend plusieurs secondes à quelques minutes | Immédiat ; l'état est consultable |
| Panne de `clamd` | Selon l'implémentation | Le téléversement échoue (fermé) ; l'utilisateur doit recommencer | Fichier conservé en `pending`, nouvelle tentative automatique, puis `scan_failed` ; **jamais servi** |
| Délais d'expiration HTTP (proxy, Next.js) | Risque | **Risque élevé** sur les gros fichiers | Aucun |
| Nouvelle analyse périodique (signatures à jour) | À construire à part | À construire à part | Même file, même worker |
| Montée en charge | Liée aux processus de l'API | Liée aux processus de l'API, qui sont bloqués | Workers indépendants, ajustables |
| Complexité | Faible, mais politique implicite | Faible | Moyenne : file, worker, réconciliation |
| Traçabilité | Faible | Correcte | Complète : `media_scan_events`, tentatives, version des signatures |
| Développement local | Simple | Simple | Simple : un conteneur de plus, même image que l'API |
| Production | Insuffisant | Acceptable pour de petits fichiers seulement | **Adapté** |

**Remarques sur le moteur.**
- `clamd` charge les signatures en mémoire : environ 1,5 à 2 Go de RAM.
- Le premier démarrage télécharge les signatures, plusieurs centaines de Mo ; le volume des
  signatures doit donc être persistant.
- L'image officielle `clamav/clamav` embarque `freshclam`.
- Les miroirs publics limitent le débit : en production, un miroir privé (`cvdupdate`, gratuit) est
  recommandé.

## Décision proposée

1. **Moteur.** ClamAV `clamd`, image officielle en conteneur, accessible seulement sur le réseau
   Compose interne, sans port publié. Les signatures sont dans un volume persistant dédié ; un volume
   existant n'est jamais supprimé.
2. **Mode.** Option **C**, l'analyse **asynchrone**, **identique en développement et en production** :
   - la file est un Redis Stream `media:scan` avec groupe de consommateurs, acquittement explicite et
     nouvelle tentative à délai croissant (5 tentatives maximum), plus une file des échecs
     définitifs ;
   - le worker `media-scan` utilise la même image que l'API avec une commande dédiée, et une identité
     de service non connectable pour l'audit ;
   - le worker lit l'objet en quarantaine, l'envoie à `clamd` en flux (INSTREAM, taille limitée par
     `StreamMaxLength`), puis enregistre le verdict.
3. **Fermeture par défaut.** Seul l'état `clean` est diffusable. Aucun fichier n'est jamais servi, à
   aucun rôle, auteur compris, dans ces cas :
   - `pending`, `rescan_pending`, `infected`, `scan_failed` ;
   - média révoqué ou supprimé ;
   - `clamd` injoignable, délai dépassé, réponse inattendue ;
   - signatures plus anciennes que 48 heures, vérifiées par `VERSION` : le worker refuse alors de
     conclure `clean`.

   Aucun interrupteur, variable d'environnement ou rôle ne permet de passer outre.
4. **Panne de l'antivirus.** Les téléversements restent acceptés en quarantaine et les fichiers
   restent `pending` :
   - le worker réessaie, puis passe le fichier en `scan_failed`, toujours non servi ;
   - l'état de santé de l'API signale `media_scanning: degraded` ;
   - un fichier rattaché à un brouillon empêche sa soumission, `422 media_not_clean`.

   À la reprise, un réconciliateur relance l'analyse des `pending` et `scan_failed` encore récupérables.
5. **Source de vérité.** C'est **PostgreSQL** (`media_assets.scan_status`), et non Redis. Une perte de
   Redis ne rend rien accessible : le réconciliateur reconstruit la file depuis la base.
6. **Passage à `clean`.** Il suppose :
   - un verdict `OK` ;
   - une copie vers le compartiment des fichiers sains, relue et vérifiée par SHA-256 ;
   - puis la mise à jour de l'état, dans une transaction, avec un événement `media_scan_events`.

   Le passage est **idempotent** : un message rejoué ne produit pas de doublon.
7. **Nouvelle analyse périodique.** Tous les fichiers `clean` sont analysés de nouveau chaque semaine
   avec les signatures à jour. Une détection fait passer le fichier à `infected`, incrémente
   `access_epoch` (révocation immédiate des jetons) et produit un audit.
8. **Faux positif.** Aucun passage manuel à `clean`. Un relecteur plateforme peut seulement demander
   une nouvelle analyse ; le verdict reste celui du moteur.
9. **Contrôle complémentaire.** L'antivirus ne remplace pas la validation des formats : liste
   blanche, signature réelle, ré-encodage des images, refus des PDF actifs. Ces contrôles ont lieu
   **avant** la mise en quarantaine.
10. **Développement local et CI.**
    - **Développement :** le service `clamav` est démarré avec un profil Compose `media`. Sans lui,
      les médias restent `pending`, ce qui est conforme à la fermeture par défaut.
    - **CI :** les tests unitaires utilisent un analyseur simulé (verdicts `OK`, `FOUND`, erreur,
      délai). Un job d'intégration démarre `clamd` réel et vérifie le fichier de test **EICAR**
      (chaîne standard, inoffensive), avec un cache des signatures.

## Conséquences

**Positives**
- Aucun fichier non vérifié n'atteint un étudiant, même en cas de panne.
- Les téléversements volumineux ne bloquent pas l'API.
- La nouvelle analyse périodique couvre les menaces découvertes après coup.
- La traçabilité est complète.

**Négatives**
- Un conteneur gourmand en mémoire, d'environ 2 Go, qu'il faut documenter pour Docker Desktop.
- Un service et un worker de plus à superviser.
- Le délai avant disponibilité d'un média, en général quelques secondes, doit être visible dans
  l'interface (état « Analyse en cours »).
- Une dépendance aux mises à jour des signatures, d'où le miroir privé en production.

**Neutres**
- Aucun coût de licence.
- Aucune donnée envoyée à un service externe : l'analyse reste locale.

## Points restant à confirmer

- Seuil de fraîcheur des signatures (48 h proposé) et cadence de nouvelle analyse (hebdomadaire).
- Nombre de tentatives et délais (5 tentatives, de 30 s à 30 min).
- Ressources allouées à `clamd` en production, et miroir `cvdupdate`.
- Job CI avec `clamd` réel, compte tenu du temps de téléchargement des signatures (D23).
