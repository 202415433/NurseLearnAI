# Plan — Étape 2.2.3 : médias et documents protégés

> **Statut** : architecture **validée** et décisions **confirmées le 2026-11-21** (§ 7). **Aucun développement engagé** : le code 2.2.3 attend une autorisation distincte. Décision antivirus : [ADR-0011](adr/0011-medias-antivirus.md).
> Point de départ : `main` = `58279d3` (2.2.2-c intégrée, CI 7/7), migration en tête `0006_independent_versions`.

## 1. Objectif

Permettre aux équipes éditoriales de joindre des images, vidéos, sous-titres et PDF aux versions de
leçon, et de les diffuser aux étudiants **sans jamais exposer** :
- un fichier non analysé, suspect ou rejeté ;
- un fichier d'un autre établissement ;
- un fichier hors d'une édition à laquelle l'étudiant a accès.

**Règle impérative.** Un fichier n'est servi, à **qui que ce soit**, étudiant comme éditeur, que s'il
est à l'état `clean`, établi par l'antivirus, et que l'accès est autorisé au moment de la demande.
Toute panne, tout doute ou tout état intermédiaire vaut **refus**.

## 2. Existant réutilisé

| Brique | État | Usage en 2.2.3 |
|---|---|---|
| FastAPI + SQLAlchemy async | En place | Points d'accès médias (éditorial et diffusion) |
| PostgreSQL 16 | En place, 0001 → 0006 | Source de vérité des médias, états et rattachements ; migration **0007 additive** |
| RustFS (S3 compatible, `infra/docker-compose.yml`, volume `s3data`) | En bonne santé, aucun compartiment applicatif | Deux compartiments **privés** : quarantaine et sains |
| Redis 7 | En place (limitation de débit) | File d'analyse (Redis Streams), jetons d'accès temporaires et révocables |
| Identité, sessions sensibles, RBAC (2.1, ADR-0007) | En place | `content:create` / `content:review` dans la portée ; session sensible pour l'écriture |
| Isolation des établissements | En place (scope 2.2.2-b, déclencheurs) | Un média porte l'établissement de son déposant ; 404 hors portée |
| Audit (`audit_logs`, `record()` sans contenu) | En place | Actions `media.*`, sans nom de fichier, jeton ni contenu |
| Éditions figées (ADR-0009, ADR-0010), catalogue 2.2.2-c | En place | Un média rattaché à une version publiée devient immuable ; diffusion seulement via une édition accessible |
| `blocks/v1` (blocs `image`, `video`, `document` avec `resource_id`) | En place, `resource_id` non vérifié | Vérification contre `media_assets` à la soumission et à la publication |
| `learning_resources` (0001) | Vide, non utilisée | **Inchangée** (aucune modification de migration historique) ; remplacée fonctionnellement par `media_assets` |

## 3. Architecture

```
 Éditeur ──(session sensible, content:create)──► POST /api/v1/editorial/media
                                                    │ 1. limites (taille, débit, quota)
                                                    │ 2. signature réelle + liste blanche
                                                    │ 3. normalisation (EXIF, ré-encodage image)
                                                    │ 4. SHA-256
                                                    ▼
                            RustFS  nl-media-quarantine  (privé, jamais servi)
                                                    │ ligne media_assets : scan_status = pending
                                                    ▼
                            Redis Stream  media:scan  ──►  worker « media-scan » (conteneur dédié)
                                                                │ INSTREAM (TCP interne)
                                                                ▼
                                                           clamd (ClamAV, Docker)
                                                                │
              clean ──► copie vers nl-media-clean, scan_status = clean
              infected ──► reste en quarantaine, scan_status = infected (jamais servi)
              erreur / panne / signatures périmées ──► nouvelle tentative, puis scan_failed (jamais servi)

 Étudiant ──(session)──► POST /api/v1/media/{id}/access   (contrôle : clean + édition accessible + portée)
                          ◄── jeton opaque 5 min, lié à l'utilisateur et à l'époque du média
          ──► GET /api/v1/media/t/{jeton}  (revérifie tout, diffuse en flux depuis nl-media-clean)
```

### 3.1 Téléversement sécurisé

- `POST /api/v1/editorial/media` en multipart, **session sensible**, `content:create` dans la portée
  (`organization_id` facultatif ; une valeur nulle exige la permission plateforme). L'acteur vient de
  la session, jamais du corps.
- Champs obligatoires :
  - `kind` (`image`, `video`, `captions`, `document`) ;
  - `license` et `attribution` ;
  - `alt_text` (FR obligatoire) pour une image ;
  - `captions_for` pour des sous-titres.
- Le corps est lu **en flux** vers un fichier temporaire, avec arrêt dès que la taille maximale du
  type est dépassée (413). Aucun fichier n'est chargé entièrement en mémoire.
- Le nom d'origine n'est **jamais** utilisé comme clé. Il est réduit à un libellé d'affichage
  assaini, sans chemin ni caractère de contrôle, et n'entre jamais dans l'audit.
- **Limitation de débit** Redis par utilisateur et par établissement : par exemple 30
  téléversements par heure (D16).

### 3.2 Validation des formats et des tailles

| Type | MIME autorisé | Contrôle de signature réelle | Normalisation | Taille max (D15) |
|---|---|---|---|---|
| Image | `image/jpeg`, `image/png`, `image/webp` | Octets magiques + décodage complet (Pillow), limite de pixels anti-bombe | **Ré-encodage** dans le même format : supprime EXIF, GPS et charges utiles ajoutées (polyglottes) | 5 Mo, 8 000 × 8 000 px |
| Vidéo | `video/mp4` | Boîte `ftyp` (familles ISO BMFF autorisées) ; analyse structurelle des boîtes (S4, D20) | Aucune (pas de transcodage en 2.2.3) | 200 Mo (développement) ; production à fixer |
| Sous-titres | `text/vtt` | UTF-8 strict, en-tête `WEBVTT`, aucune balise HTML hors balises WebVTT autorisées | Réécriture texte (balises neutralisées) | 1 Mo |
| Document | `application/pdf` | En-tête `%PDF-`, analyse de structure : refus si chiffré, JavaScript, `OpenAction`/`Launch`, fichiers joints, formulaires XFA | Aucune (pas de réécriture du PDF) | 20 Mo |

Refusés par construction :
- SVG, HTML, archives (ZIP, RAR, 7z), exécutables ;
- Office avec macros, et tout type hors liste blanche ;
- tout fichier dont la signature contredit le type annoncé ou l'extension (`422 media_type_mismatch`).

Le type servi est celui **déterminé par l'analyse**, jamais celui annoncé par le client.

### 3.3 Stockage privé et quarantaine

- **Deux compartiments RustFS privés**, sans accès anonyme ni liste publique :
  - `nl-media-quarantine` : dépôt initial et fichiers infectés ou en échec ;
  - `nl-media-clean` : fichiers sains, seule source de diffusion.
- **Clés** aléatoires (`<uuid>/<uuid>`), jamais dérivées d'un nom ni d'un identifiant métier.
- **Transitions de stockage** :
  - le passage à `clean` se fait par **copie** vers `nl-media-clean`, puis l'objet de quarantaine est
    retiré ;
  - l'état en base n'est mis à jour qu'**après** la copie vérifiée (SHA-256 relu).
- **Accès aux compartiments** :
  - en production, clés d'accès distinctes si RustFS le permet (D19) : téléversement en écriture sur
    la quarantaine ; worker en lecture sur la quarantaine et en écriture sur les sains ; diffusion en
    lecture seule sur les sains ;
  - en développement, une clé applicative unique, comme aujourd'hui.
- **Chiffrement au repos** : chiffrement du volume ou du disque en production ; chiffrement côté
  serveur RustFS à évaluer (D19).
- **Privilèges minimaux** (D19) : si RustFS ne permet pas des clés par rôle, le cloisonnement est
  applicatif (seul le worker écrit dans `nl-media-clean`, seul le service de diffusion y lit) et
  l'écart est documenté avant la production.

### 3.4 Analyse antivirus

Le détail et la comparaison sont dans [ADR-0011](adr/0011-medias-antivirus.md). Synthèse de la
recommandation :
- **ClamAV `clamd` en conteneur** (gratuit, local), **analyse asynchrone** avec une file Redis
  Streams et un **worker dédié** ;
- même architecture en développement et en production ;
- **fermeture par défaut** : panne de l'antivirus, délai dépassé ou signatures trop anciennes
  laissent le fichier `pending` puis `scan_failed`, **jamais servi**.

**États de `scan_status`** :
- `pending` : en attente d'analyse ;
- `clean` : analysé sain ;
- `infected` : menace détectée ;
- `scan_failed` : analyse impossible ;
- `rescan_pending` : nouvelle analyse demandée.

Seul `clean` est diffusable.

**Nouvelle analyse périodique** (hebdomadaire, avec les signatures à jour) de tous les fichiers
`clean`. Une détection a posteriori fait passer le fichier à `infected`, **révoque immédiatement**
tous les jetons et retire le fichier de la diffusion. La leçon reste publiée et affiche « média
indisponible ».

### 3.5 Quotas de stockage

- Table `media_usage` : octets et nombre de fichiers par établissement, avec une ligne « plateforme »
  pour le contenu public.
- Contrôle **sous verrou de ligne** (`FOR UPDATE`) au téléversement : les fichiers `pending`, `clean`
  et `scan_failed` comptent ; les fichiers supprimés libèrent l'espace à la purge.
- Valeurs confirmées (D17) :
  - 5 Go et 2 000 fichiers par établissement ;
  - 20 Go pour la plateforme ;
  - 1 Go par utilisateur.

  Elles sont surchargeables par l'administrateur de la plateforme seulement.
- Un dépassement répond **`413 quota_exceeded`**, sans écriture dans S3. L'espace est **réservé** avant l'écriture S3 (S10).

### 3.6 Permissions d'accès

| Action | Exigence |
|---|---|
| Téléverser | Session sensible + `content:create` dans la portée (établissement, ou plateforme pour le public) |
| Consulter les métadonnées et l'état | `content:create` / `review` / `publish` dans la portée, sinon 404 |
| Rattacher à une version de leçon (`lesson_version_media`) | Brouillon de la portée ; média de la même portée ou public ; vérification par déclencheur (0007) |
| Soumettre, approuver, publier une version ou une édition | Chaque média rattaché est `clean` (API + déclencheur) ; sinon `422 media_not_clean` |
| Aperçu éditorial | Jeton pour un média `clean` de la portée (aucun aperçu d'un fichier non analysé, même pour l'auteur) |
| Diffusion étudiant | Session authentifiée (D18) + média `clean` + cité par une version de leçon d'une édition **exposée par le catalogue** (règle 2.2.2-c) ou, à partir de 2.2.4, de l'édition de l'inscription |
| Supprimer | Auteur ou éditeur de la portée, seulement si le média n'est rattaché à aucune version soumise, approuvée, publiée ou archivée |
| Révoquer (bloquer) | `content:review` plateforme ou `admin` ; effet immédiat |
| Modifier quotas | `admin` uniquement |

**Isolation.** Un média d'établissement n'est rattachable qu'à un contenu du même établissement. Un
média public n'est rattachable qu'à un contenu public ou d'établissement. La règle est vérifiée par
la base et par l'API ; hors portée, la réponse est 404.

### 3.7 Distribution sécurisée aux étudiants

- **Jeton opaque** (32 octets aléatoires), stocké dans Redis sous la forme
  `media:tok:<sha256(jeton)>` → (média, utilisateur, époque, expiration) :
  - durée de vie **5 minutes** ;
  - lié à l'utilisateur, qui doit présenter sa session ;
  - jamais journalisé ;
  - réutilisable pendant sa durée de vie pour les requêtes `Range`.
- **Diffusion par l'API** (`GET /api/v1/media/t/{jeton}`), en flux depuis `nl-media-clean`, avec
  prise en charge de `Range` pour la vidéo. **Pas d'URL présignée S3 exposée** (D24), ce qui rend la
  révocation immédiate.
- **En-têtes** :
  - `Content-Type` déterminé à l'analyse ;
  - `X-Content-Type-Options: nosniff` ;
  - `Content-Disposition` : `inline` pour image, vidéo et sous-titres, `attachment` pour PDF par
    défaut ;
  - `Content-Security-Policy: sandbox; default-src 'none'` ;
  - `Cross-Origin-Resource-Policy: same-site` ;
  - `Cache-Control: private, no-store`.
- **Revérification à chaque requête** : état `clean`, époque inchangée, accès toujours autorisé.

### 3.8 Liens temporaires et révocables

| Mécanisme | Effet |
|---|---|
| Expiration | Clé Redis à durée de vie de 5 min |
| Révocation d'un média | `media_assets.access_epoch` + 1 → tous les jetons existants deviennent invalides à la requête suivante |
| Révocation d'un utilisateur | Fin de session ou désactivation du compte → jetons refusés (session revérifiée) |
| Perte de Redis | Tous les jetons sont perdus ; il suffit d'en redemander (fermeture par défaut) |

### 3.9 Conservation et suppression

| Cas | Politique (D21, sous réserve des obligations légales de conservation) |
|---|---|
| Média rattaché à une version soumise, approuvée, publiée ou archivée | **Conservé, immuable** (historique des éditions) ; jamais supprimé par l'API ; seule une procédure de conservation administrée pourra l'effacer |
| Brouillon jamais rattaché ou rattaché seulement à des brouillons | Suppression logique (`deleted`), objet purgé après 30 jours de grâce ; audité |
| `infected` | Conservé en quarantaine 30 jours (analyse), puis objet purgé ; métadonnées conservées |
| `pending` / `scan_failed` orphelins | Purge de l'objet après 7 jours |
| Révoqué par un relecteur | Objet conservé, jamais diffusé ; décision de purge manuelle et auditée |

Les purges sont faites par une **tâche planifiée** du worker, journalisée.

Aucune purge ne touche :
- un média cité par une version non brouillon ;
- un média visé par un **signalement de contenu** ouvert ou conservé (`content_issue_reports`) ;
- un média sous **obligation de conservation** (colonne `legal_hold`, posée par un administrateur ; enquête, litige, exigence réglementaire de la juridiction) ;
- une décision éditoriale ;
- un historique d'apprentissage.

Les durées ci-dessus sont des **minimums techniques** : elles s'effacent devant toute obligation
légale de conservation plus longue, à confirmer par juridiction avec la politique de confidentialité.
La purge vérifie ces conditions **sous verrou**, dans la même transaction, et s'abstient en cas de
doute.

### 3.10 Audit

Actions, journalisées sans nom de fichier, jeton, URL ni contenu :
- `media.uploaded` : type, taille, SHA-256, portée ;
- `media.rejected` : code de refus ;
- `media.scan_clean`, `media.scan_infected` : nom de signature ;
- `media.scan_failed` : cause technique ;
- `media.attached`, `media.detached` ;
- `media.access_granted` : média, utilisateur ; **sans le jeton** ; éventuellement agrégé par minute
  si le volume l'exige ;
- `media.revoked`, `media.deleted`, `media.purged` ;
- `media.quota_exceeded`.

Le worker agit sous une **identité de service** dédiée, un compte système non connectable ; aucune
action anonyme. Les événements d'analyse sont aussi tracés dans `media_scan_events`, en ajout seul :
version des signatures, durée, résultat.

### 3.11 Sauvegardes

| Donnée | Politique (D22) |
|---|---|
| PostgreSQL | Inchangé (architecture) : quotidienne + WAL, restauration testée mensuellement |
| `nl-media-clean` | Copie quotidienne **chiffrée** (`rclone crypt`, clé hors dépôt) vers un stockage de sauvegarde distinct, rétention 30 jours ; versionnage RustFS si disponible |
| `nl-media-quarantine` | **Exclu** des sauvegardes (fichiers non sûrs) |
| Cohérence | Tâche de rapprochement hebdomadaire : chaque `media_assets.clean` a son objet et son SHA-256 ; chaque objet a sa ligne. Les écarts sont signalés, jamais supprimés automatiquement |
| Cohérence base ↔ objets | La sauvegarde des objets suit le **point de sauvegarde PostgreSQL** : la liste des objets à copier est extraite de `media_assets` (état `clean`, clé, SHA-256) au moment de la sauvegarde de la base, et chaque copie est vérifiée par SHA-256. Tout objet référencé et absent est signalé |
| Restauration | Base d'abord, puis objets ; tout média dont l'objet manque ou dont le SHA-256 diffère passe en `scan_failed` (non servi) |
| Test de restauration | **Mensuel**, et à chaque évolution du schéma média : restauration dans un environnement jetable, rapprochement complet, échantillon de diffusions ; résultat consigné |

### 3.12 Tests de sécurité

- **Formats** :
  - type hors liste ;
  - extension trompeuse ;
  - MIME annoncé faux ;
  - SVG ou HTML déguisé ;
  - polyglotte JPEG+ZIP (neutralisé par ré-encodage) ;
  - bombe de décompression d'image ;
  - PDF chiffré, avec JavaScript ou avec fichier joint ;
  - VTT avec HTML ou script ;
  - fichier vide ;
  - taille limite et taille plus un octet.
- **Antivirus** :
  - fichier de test **EICAR** (chaîne de test standard, inoffensive) → `infected`, jamais servi ;
  - `clamd` arrêté → `pending` puis `scan_failed`, jamais servi ;
  - délai dépassé ;
  - signatures trop anciennes → refus de marquer `clean` ;
  - nouvelle analyse détectant une menace → révocation immédiate.
- **Accès** :
  - jeton expiré, d'un autre utilisateur ou révoqué par l'époque ;
  - média `pending` demandé par l'auteur → refus ;
  - média d'un autre établissement → 404 ;
  - média non cité par une édition exposée → 404 ;
  - session expirée.
- **Quotas** : dépassement ; deux téléversements concurrents à la limite (verrou).
- **Éditorial** :
  - soumission ou publication avec média `pending`, `infected` ou révoqué → 422 ;
  - rattachement inter-établissements → refus de la base.
- **Robustesse** : perte de Redis (file reconstruite depuis la base), reprise du worker, idempotence
  d'une analyse rejouée.
- **Audit** : aucune présence de jeton, de nom de fichier ou de contenu.
- **Non-régression complète** : 2.2.2-a/b/c et antérieures.

## 4. Modèle de données (migration 0007, additive et réversible)

Aucune modification de 0001 à 0006, et `learning_resources` reste intacte.

| Table | Rôle |
|---|---|
| `media_assets` | Identifiant ; `organization_id` (nul = public) ; `legal_hold` ; `uploaded_by` ; `kind` ; `mime_type` ; `byte_size` ; `sha256` ; clés de stockage (quarantaine, sains) ; `scan_status` ; `access_epoch` ; `status` (`active` / `revoked` / `deleted`) ; `display_name` assaini ; `license` ; `attribution` ; `alt_text_i18n` ; `captions_for` (sous-titres → vidéo) ; horodatages |
| `media_scan_events` | Ajout seul : média, résultat, signature détectée, version des signatures, durée, tentative |
| `lesson_version_media` | (version de leçon, média) : rattachement vérifié par déclencheur (portée, état du brouillon, immuabilité hors brouillon) |
| `media_usage` | Compteurs par établissement ou plateforme, quotas surchargeables |

**Contraintes de la base** :
- `clean` impose une clé « sains » et un SHA-256 ;
- une vidéo rattachée doit avoir des sous-titres `clean` (WCAG 1.2.2) ;
- une image exige un texte alternatif FR (WCAG 1.1.1).

**Règles de publication** : la validation des versions et éditions de 0006 est complétée par une
fonction de contrôle des médias, appelée par l'API et par un déclencheur de 0007. La fonction de
0006 n'est jamais réécrite.

## 5. Points d'accès prévus

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/api/v1/editorial/media` | Téléverser (202, `pending`) |
| `GET` | `/api/v1/editorial/media`, `/api/v1/editorial/media/{id}` | Liste et état dans la portée |
| `PUT` | `/api/v1/editorial/lesson-versions/{id}/media` | Rattachements d'un brouillon (`If-Match`) |
| `DELETE` | `/api/v1/editorial/media/{id}` | Suppression logique d'un média libre |
| `POST` | `/api/v1/editorial/media/{id}/revoke` | Blocage immédiat (relecteur plateforme ou admin) |
| `POST` | `/api/v1/media/{id}/access` | Jeton de 5 min (étudiant ou aperçu éditorial) |
| `GET` | `/api/v1/media/t/{jeton}` | Diffusion en flux (`Range`) |
| `GET` | `/api/v1/admin/media/usage` | Quotas et consommation (admin) |

## 6. Renforcement de sécurité (exigences contraignantes)

| # | Exigence | Mise en œuvre prévue |
|---|---|---|
| S1 | **Révocation immédiate par contrôle serveur** | Aucun lien autonome : chaque requête de diffusion relit en base l'état du média (`scan_status`, `status`, `access_epoch`) et en Redis le jeton ; une révocation (époque + 1, `revoked`, `infected`) prend effet à la requête suivante, sans attendre l'expiration |
| S2 | **Autorisation revérifiée pour chaque requête HTTP `Range`** | Chaque requête partielle est traitée comme une requête complète : session, jeton, utilisateur, état `clean`, époque, rattachement à une édition accessible ; aucune décision mise en cache entre deux plages |
| S3 | **Refus de diffusion si le média n'est pas `clean`** | Garde unique côté API, plus vue SQL de diffusion ne renvoyant que des médias `clean`, `active`, à époque courante ; test pour chaque état |
| S4 | **Analyse structurelle des PDF et MP4** | PDF : analyse de l'arbre d'objets (refus de JavaScript, `OpenAction`/`Launch`, `EmbeddedFile`, XFA, chiffrement, flux corrompus) ; MP4 : parcours des boîtes ISO BMFF (`ftyp` autorisé, tailles cohérentes, profondeur et nombre de boîtes bornés, aucune donnée hors boîtes) |
| S5 | **Traitements isolés et bornés** | Analyse et normalisation dans le **worker** (conteneur dédié, sans privilège, système de fichiers en lecture seule hors répertoire temporaire, réseau limité à RustFS, Redis, PostgreSQL et `clamd`) ; limites Compose `cpus` / `mem_limit` ; limites par tâche (`RLIMIT_AS`, `RLIMIT_CPU`, délai d'expiration) ; `clamd` avec `StreamMaxLength`, `MaxScanTime`, `MaxFileSize` |
| S6 | **PostgreSQL comme source de vérité** | L'état d'un média n'existe qu'en base ; Redis ne porte que des messages et des jetons reconstructibles ; aucune décision d'accès n'est prise sur la seule foi de Redis |
| S7 | **Tâches Redis idempotentes** | Message = (média, tentative, empreinte attendue) ; transition conditionnelle en base (`UPDATE … WHERE scan_status = 'pending' AND sha256 = :attendu`) ; copie S3 vers une clé déterministe ; un message rejoué ne produit ni doublon ni changement d'état |
| S8 | **Reprise après panne du worker** | Groupe de consommateurs Redis Streams : messages non acquittés réclamés (`XAUTOCLAIM`) après délai ; réconciliateur périodique qui réenfile tout média `pending`/`rescan_pending` plus ancien qu'un seuil ; nombre de tentatives borné, puis `scan_failed` |
| S9 | **Refus par défaut en cas de panne de ClamAV** | `clamd` injoignable, délai dépassé, réponse inattendue ou signatures de plus de 48 h ⇒ aucun passage à `clean` ; le média reste non diffusable ; santé `media_scanning: degraded` |
| S10 | **Quotas protégés contre les téléversements simultanés** | **Réservation** de l'espace sous verrou de ligne (`media_usage … FOR UPDATE`) **avant** l'écriture S3, libérée si le téléversement échoue ; contrainte `CHECK (used_bytes <= quota_bytes)` en base comme dernier rempart |
| S11 | **Sauvegardes cohérentes avec PostgreSQL** | Voir § 3.11 : liste des objets dérivée de la base au point de sauvegarde, vérification SHA-256, rapprochement |
| S12 | **Tests de restauration** | Mensuels et à chaque évolution du schéma média ; restauration jetable, rapprochement, diffusion échantillonnée (§ 3.11) |
| S13 | **Purge interdite en présence de signalements ou d'obligations de conservation** | Voir § 3.9 : `content_issue_reports` et `legal_hold` bloquent toute purge, vérifiés sous verrou ; la clé étrangère historique en CASCADE de `content_issue_reports` n'est jamais exercée par une purge de média |
| S14 | **Aucun contournement manuel du statut `clean`** | `clean` ne peut être posé que par l'identité de service du worker avec un événement `media_scan_events` « OK » de la même tentative ; déclencheur 0007 refusant tout autre passage ; aucune route, variable d'environnement ni rôle (admin compris) pour forcer l'état |

## 7. Décisions confirmées (2026-11-21)

Les identifiants D1 à D14 existent déjà, D14 désignant la séparation des rôles PostgreSQL (lot 2.5).
Les décisions médias utilisent donc **D7** (déjà réservée aux médias par le plan 2.2) et **D15 à
D24**, tous libres avant ce plan.

| # | Décision | Contenu confirmé |
|---|---|---|
| D7 | Antivirus | ClamAV `clamd` en Docker + analyse **asynchrone** par un **worker** alimenté par une file **Redis** ; refus par défaut ([ADR-0011](adr/0011-medias-antivirus.md)) |
| D15 | Tailles maximales | Image 5 Mo ; PDF 20 Mo ; VTT 1 Mo ; MP4 200 Mo en développement (valeur de production à fixer avant mise en ligne) |
| D16 | Débit de téléversement | 30 par heure et par utilisateur ; 300 par heure et par établissement |
| D17 | Quotas | 1 Go par utilisateur ; 5 Go par établissement ; 20 Go pour la plateforme |
| D18 | Consultation des médias | **Authentification obligatoire**, y compris pour les programmes publics gratuits |
| D19 | Stockage | Compartiments **privés**, **privilèges minimaux** (une clé par rôle : dépôt, analyse, diffusion, si RustFS le permet ; sinon cloisonnement applicatif documenté), chiffrement au repos en production |
| D20 | Bibliothèques | Uniquement des bibliothèques **maintenues**, à licence compatible, **auditées** (`pip-audit` en CI) ; choix précis arrêté et justifié en 2.2.3-b (Pillow ; pypdf ou pikepdf ; client S3 ; client `clamd` interne) |
| D21 | Conservation | Durées techniques du § 3.9, **sous réserve des obligations légales** de conservation par juridiction |
| D22 | Sauvegardes | **Chiffrées**, cohérentes avec les références PostgreSQL, **restauration testée** (§ 3.11) |
| D23 | Antivirus en CI | **ClamAV réel** dans un job d'intégration avec le fichier de test **EICAR** ; analyseur simulé pour les tests unitaires |
| D24 | Diffusion | Par l'**API**, avec **contrôle d'autorisation à chaque requête** (y compris `Range`) et jetons révocables de 5 min ; aucune URL présignée S3 exposée |

## 8. Découpage du développement (branche `phase-2.2.3-secure-media`)

Chaque sous-étape se termine par : Ruff, mypy, tests de base et d'API sur PostgreSQL et Redis réels,
non-régression complète, aucun push sans autorisation.

### 2.2.3-a — Migration 0007, modèles, invariants et quotas

- **Contenu** : tables `media_assets`, `media_scan_events` (ajout seul), `lesson_version_media`,
  `media_usage` ; colonnes `legal_hold`, `access_epoch` ; déclencheurs : portée et isolation des
  rattachements, immuabilité hors brouillon, passage à `clean` réservé au worker (S14), quotas
  (S10) ; extension des contrôles de soumission et de publication via une fonction de 0007 (aucune
  réécriture de 0001 à 0006).
- **Critères d'acceptation** : montée, descente et remontée à l'identique ; aucune migration
  historique modifiée ; tous les invariants refusés par la base même sans l'API.
- **Tests obligatoires** :
  - aller-retour de 0007 avec signature de schéma identique ;
  - rattachement inter-établissements refusé ;
  - version soumise, approuvée ou publiée avec média non `clean` refusée ;
  - passage à `clean` sans événement « OK » ou hors identité du worker refusé ;
  - quota dépassé refusé par la contrainte ;
  - `legal_hold` et signalement bloquant la suppression ;
  - non-régression de toutes les suites de base.

### 2.2.3-b — Téléversement sécurisé et stockage privé

- **Contenu** : service de stockage RustFS (deux compartiments privés, clés aléatoires, privilèges
  minimaux D19) ; `POST /api/v1/editorial/media` en flux ; liste blanche et signature réelle ;
  ré-encodage des images ; analyse structurelle PDF et MP4 (S4) ; limites D15 ; débit D16 ;
  réservation de quota D17 (S10) ; audit.
- **Critères d'acceptation** : aucun fichier hors liste ou incohérent n'atteint la quarantaine ;
  aucun fichier n'est jamais lisible depuis l'API à ce stade ; quotas exacts sous concurrence.
- **Tests obligatoires** :
  - type hors liste, extension ou MIME trompeur, SVG ou HTML déguisé, polyglotte, bombe d'image ;
  - PDF actif, chiffré ou avec fichier joint ; MP4 malformé ou à boîtes incohérentes ;
  - VTT avec HTML ;
  - taille limite et taille plus un octet ;
  - débit dépassé ;
  - quotas sous téléversements simultanés ;
  - isolation 404 ;
  - session non sensible refusée ;
  - audit sans nom de fichier ni contenu.

### 2.2.3-c — ClamAV, worker, quarantaine et reprise après panne

- **Contenu** : service `clamav` (profil Compose `media`, volume de signatures persistant, limites
  CPU et mémoire) ; worker `media-scan` (Redis Streams, groupe de consommateurs, idempotence S7,
  reprise S8, isolation S5) ; refus par défaut S9 ; nouvelle analyse hebdomadaire ; réconciliateur ;
  état de santé.
- **Critères d'acceptation** : seul un verdict « OK » avec signatures fraîches produit `clean` ;
  aucune panne ne rend un fichier diffusable ; aucun message rejoué ne change un état déjà établi.
- **Tests obligatoires** :
  - EICAR → `infected` (ClamAV réel, D23) ;
  - `clamd` arrêté → `pending` puis `scan_failed` ;
  - délai dépassé ;
  - signatures périmées ;
  - message rejoué ;
  - worker tué en cours d'analyse puis repris ;
  - perte de Redis puis réconciliation ;
  - nouvelle analyse détectant une menace → révocation ;
  - limites de ressources respectées ;
  - job CI d'intégration ClamAV.

### 2.2.3-d — Diffusion autorisée, révocation et conservation

- **Contenu** : rattachements `PUT /lesson-versions/{id}/media` (`If-Match`) ; contrôles de
  soumission, approbation et publication ; `POST /api/v1/media/{id}/access` et
  `GET /api/v1/media/t/{jeton}` en flux avec `Range` (D24, S1 à S3) ; authentification obligatoire
  (D18) ; révocation ; suppression logique ; purge planifiée (§ 3.9, S13) ; `legal_hold`.
- **Critères d'acceptation** : aucun octet d'un média non `clean`, révoqué ou supprimé ne sort de
  l'API ; chaque requête, partielle ou complète, est autorisée à neuf ; aucune purge ne viole
  S13.
- **Tests obligatoires** :
  - diffusion refusée pour chaque état non `clean`, et pour l'auteur ;
  - jeton expiré, d'un autre utilisateur, révoqué ;
  - révocation entre deux requêtes `Range` ;
  - accès anonyme refusé ;
  - média non cité par une édition exposée → 404 ;
  - hors établissement → 404 ;
  - en-têtes de sécurité ;
  - publication bloquée par un média non `clean` ;
  - purge bloquée par signalement et par `legal_hold` ;
  - audit sans jeton.

### 2.2.3-e — Tests d'intégration, sécurité, restauration et validation Windows

- **Contenu** : scénario de bout en bout (dépôt → analyse → rattachement → relecture → publication
  non médicale → diffusion → révocation) ; campagne de tests de sécurité ; sauvegarde chiffrée et
  restauration (D22, S11, S12) ; documentation `VALIDATION-PHASE2.2.3.md` ; script
  `validate-phase2.2.3.ps1` (enchaîne 2.2.2-c et antérieures).
- **Critères d'acceptation** : les 10 critères du § 10 ; restauration réussie et rapprochée ;
  CI 7/7 (plus le job ClamAV) ; validation Windows sans échec ; aucune publication médicale.
- **Tests obligatoires** :
  - bout en bout sur Docker Compose réel ;
  - restauration dans un environnement jetable avec rapprochement et diffusion échantillonnée ;
  - non-régression complète (pytest, Vitest, Playwright + axe) ;
  - contrôles Windows en lecture seule (migration en tête `0007`, médias non `clean` jamais servis,
    pilote PAB en brouillon).

Estimation globale : 6,5 à 7 jours.

## 9. Risques

| Risque | Niveau | Mesure |
|---|---|---|
| Fichier malveillant atteignant un étudiant | Élevé | Liste blanche, signature réelle, ré-encodage, ClamAV, fermeture par défaut, nouvelle analyse, révocation immédiate |
| Mémoire de `clamd` (~1,5 à 2 Go avec signatures) sous Docker Desktop Windows | Moyen | Service activé avec un profil Compose `media` ; documentation des ressources ; attente de santé |
| Téléchargement et limitation des mises à jour de signatures (`freshclam`) | Moyen | Cadence par défaut, volume de signatures persistant ; miroir privé (`cvdupdate`) en production |
| Faux positifs ClamAV | Faible | `infected` réexaminable par un relecteur plateforme (nouvelle analyse) ; jamais de contournement manuel vers `clean` |
| Analyse incomplète des PDF par ClamAV | Moyen | Analyse de structure en plus (JavaScript, fichiers joints, chiffrement refusés) |
| Performance de la diffusion par l'API (vidéo) | Moyen | Flux et `Range` ; mesure ; toute redirection présignée exigerait une nouvelle décision, car elle affaiblirait la révocation immédiate (D24) |
| Maturité de RustFS (versionnage, politiques par clé) | Moyen | Vérification en début de 2.2.3-b ; repli sur les contrôles applicatifs |
| Données personnelles dans les médias (visages, patients) | Élevé | EXIF supprimé ; **aucune donnée ni photo de patient réel** (règle du projet) ; contrôle en relecture médicale |
| Croissance du stockage | Moyen | Quotas, purge des orphelins, rapprochement |
| Redis perdu (file, jetons) | Faible | La base reste la source de vérité ; un réconciliateur relance les `pending` ; les jetons se redemandent |

## 10. Critères d'acceptation

1. Aucun octet d'un média `pending`, `infected`, `scan_failed`, révoqué ou supprimé ne sort de l'API,
   pour aucun rôle. Un test couvre chaque état.
2. Avec `clamd` arrêté, un téléversement aboutit à `pending` puis `scan_failed`, jamais à `clean`.
3. Le fichier EICAR est `infected` et n'est jamais servi.
4. Une version de leçon ou une édition citant un média non `clean` ne peut être ni soumise, ni
   approuvée, ni publiée.
5. Un jeton expiré, d'un autre utilisateur ou révoqué est refusé. La révocation agit à la requête
   suivante.
6. Isolation : 404 hors établissement ; aucun rattachement inter-établissements possible.
7. Les quotas sont respectés sous concurrence.
8. L'audit ne contient ni jeton, ni nom de fichier, ni contenu.
9. Migration 0007 : montée, descente et remontée à l'identique ; aucune migration historique modifiée.
10. Non-régression complète (CI 7/7, validation Windows) ; aucune publication médicale.
