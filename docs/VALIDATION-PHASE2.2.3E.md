# Validation — Étape 2.2.3-e : interfaces des médias protégés et validation de bout en bout

Branche : `phase-2.2.3e-media-management-ui` (depuis `main` @ `e3a3ec3`, 2.2.3-d fusionnée).
Trois sous-lots successifs, un commit chacun : **E1** correctifs préalables · **E2** interfaces · **E3** validation de bout en bout.
Aucun développement 2.2.4 ni tuteur IA ; aucune URL S3 ; aucun jeton autonome ; aucune publication médicale.

> **État : E1 validé (32/32), E2 validé (47/47), E3 validé (83/83) sous Windows — étape 2.2.3-e validée localement ; non poussée.**

## 1. Audit préalable

| Point | Constat (main @ `e3a3ec3`, CI 7/7) | Conséquence |
|---|---|---|
| Points d'accès | Édition : `/api/v1/editorial/*` (dont médias : réservation, téléversement, liste, détail, suppression, révocation) ; diffusion : service séparé `media-delivery` (`/content`, `/preview`) | Rattachement et conservation légale ajoutés à l'API d'édition ; la diffusion est inchangée |
| Permissions PostgreSQL | `nl_api` : aucune écriture sur `lesson_version_media`, aucune sur `legal_hold` (0009) ; fonctions SECURITY DEFINER inventoriées et verrouillées (test D14) | D3-3 par **fonctions contrôlées** (0010), sans droit d'écriture général |
| `lesson_version_media` | Déclencheur 0007 : brouillon seulement, `content:create` dans la portée, même établissement, média non supprimé/infecté/révoqué ; soumission → médias `clean` et sous-titres sains | Réutilisé tel quel (aucune fonction historique réécrite) ; contrôles d'état plus stricts ajoutés en amont |
| Conservation légale | Colonne `legal_hold` (0007), réservée à `user:manage` plateforme, audit sans motif ; levée sans justification possible ; commande d'administration (2.2.3-d) | Permission dédiée `media:legal_hold`, justification obligatoire pour poser ET lever, journal en ajout seul |
| Frontend | Next.js 15 / React 19 / Tailwind 4 ; pages publiques, identité, compte ; **aucune interface éditoriale** (ni éditeur de leçon, ni bibliothèque) | E2 crée les écrans éditoriaux nécessaires (bibliothèque, téléversement, médias d'une leçon) |
| Design system | `app/tokens.css` (jetons), composants `Form`, `Notice`, `EmptyState`, `Pagination`… ; contrastes vérifiés en CI | Réutilisé ; aucun nouveau framework |
| Sessions Next.js | BFF (ADR-0007) : cookie HttpOnly `__Host-nl_session`, jeton relayé en `Authorization` par le serveur ; contrôle d'origine des actions ; CSP à nonce | Le relais média (E2) suit le même modèle |
| FreshClam / clamd | L'image officielle place le socket de clamd dans `/tmp/clamd.sock` (propre à chaque conteneur) ; `NotifyClamd` de freshclam lit ce chemin → « Clamd was NOT notified » ; clamd reste sur sa base embarquée (constaté en CI le 2026-11-24 : 57 h) | Correctif E1 (§ 4) |
| Sauvegarde / restauration | Sauvegarde ponctuelle de la base avant D14 (script) ; aucune procédure complète base + objets | E3 |
| Écarts D7, D15–D24 | D18 (jetons signés) remplacé en 2.2.3-d par la vérification de session à chaque requête (restriction « aucun jeton autonome ») ; D22 (sauvegarde chiffrée, restauration testée) non encore livré | Documentés ; D22 traité en E3 |

## 2. E1 — Migration `0010_media_editorial_permissions` (additive, réversible)

| Élément | Rôle |
|---|---|
| Permission `media:legal_hold` | Attribuée au seul rôle `admin` ; une garde refuse toute attribution à un autre rôle |
| Table `media_legal_hold_events` | Journal en **ajout seul** (modification, suppression et vidage refusés même à l'administration) : média, action `set`/`released`, **justification**, acteur, établissement, horodatage. Alimentée uniquement par la fonction contrôlée |
| `nl_media_attach(version, média)` | SECURITY DEFINER, chemin de recherche fixé ; acteur obligatoire ; média reçu, en analyse ou sain ; passe par le déclencheur 0007 (brouillon, droits, établissement) ; idempotente |
| `nl_media_detach(version, média)` | Idem ; refusée pour toute version non brouillon (déclencheur 0007) |
| `nl_media_set_legal_hold(média, pose, motif)` | Justification 5–1000 caractères ; `media:legal_hold` dans la portée du média ET `user:manage` plateforme (0007) ; refus si aucun changement ou média purgé ; journal + audit |
| Privilèges | `nl_api` : EXECUTE sur les trois fonctions, SELECT sur le journal ; **aucun** INSERT/DELETE sur `lesson_version_media`, aucun UPDATE sur `legal_hold`, aucun INSERT dans le journal ; PUBLIC : rien |
| Retour arrière | Supprime fonctions, journal et permission ; **refusé** si le journal contient des événements (historique légal jamais effacé) |

## 3. E1 — Correction D3-3 et conservation légale par l'API

| Route | Contrôles |
|---|---|
| `GET /api/v1/editorial/lesson-versions/{id}/media` | Version visible dans la portée ; indique si elle est modifiable |
| `PUT /api/v1/editorial/lesson-versions/{id}/media/{media}` | Session sensible ; version visible (404 sinon) ; `content:create` (403) ; brouillon (409 `not_draft`) ; média visible dans la portée (404 : rien n'est révélé d'un autre établissement) ; même établissement que la leçon ou public (422 `media_scope`) ; média reçu, en analyse ou sain — jamais en attente, infecté, en échec, révoqué, supprimé (422 `media_not_clean`) ; puis fonction contrôlée |
| `DELETE /api/v1/editorial/lesson-versions/{id}/media/{media}` | Mêmes contrôles ; 404 si non rattaché |
| `POST /api/v1/editorial/media/{id}/legal-hold` (`{"hold", "reason"}`) | Session sensible ; `media:legal_hold` + `user:manage` (403) ; justification (422) ; fonction contrôlée ; audit `media.legal_hold_requested` (sans motif) en plus de l'audit en base |
| `GET /api/v1/editorial/media/{id}/legal-hold` | Historique (acteur, date, motif) réservé à l'administration |

Garanties : une version soumise, approuvée ou publiée — donc toute édition figée — n'est jamais modifiée par un rattachement ou un détachement ; un média non sain n'est jamais accessible aux apprenants (soumission refusée, diffusion refusée) ; la conservation légale n'ouvre aucun accès (un média infecté ou révoqué sous conservation reste non diffusable) ; aucune suppression automatique. La commande d'administration `media-legal-hold` passe désormais par la même fonction (justification obligatoire aussi pour la levée).

## 4. E1 — Correction FreshClam → clamd

**Cause** (sources de l'image `clamav/clamav:1.5.5` et de ClamAV 1.5.5) : le `clamd.conf` de l'image fixe `LocalSocket /tmp/clamd.sock` ; `freshclam.conf` contient `NotifyClamd /etc/clamav/clamd.conf` et notifie par ce socket local. Dans deux conteneurs distincts, `/tmp` n'est pas partagé : la notification échoue et clamd conserve sa base embarquée.

**Correctif** :
- `infra/clamav/clamd.conf` (lecture seule, monté dans `clamd` ET `freshclam`) : configuration identique à celle de l'image, sauf `LocalSocket /run/clamav/clamd.sock`, `LocalSocketMode 660`, `LocalSocketGroup clamav` ;
- `/run/clamav` est le volume nommé `clamav-sock`, **dédié au socket** (aucun `/tmp` partagé, aucun port publié) ;
- clamd abandonne ses droits avant de créer son socket (source `clamd.c`) : le répertoire du volume lui est attribué au démarrage (`chown clamav:clamav`, `0750`), puis l'entrée standard de l'image prend le relais, inchangée ;
- freshclam n'est **pas** sur le réseau `media-scan` : la notification passe exclusivement par le socket.

Inchangé : contrôle de fraîcheur (≤ 48 h, plafond non relevable), fermé par défaut (signatures absentes, illisibles, périmées ou futures → jamais `clean`). En CI, le conteneur de service ClamAV du job API ne peut pas recevoir ce fichier de configuration : une étape y envoie `RELOAD` à clamd jusqu'à des signatures de moins de 47 h (même effet que la notification) ; le job Docker Compose vérifie le correctif réel.

| # | Test | Où |
|---|---|---|
| 1 | Mise à jour des signatures | freshclam au démarrage (journal) ; version sur disque lue par `sigtool` |
| 2 | Notification réussie | `clamdscan --ping` depuis freshclam, en utilisateur `clamav`, sans accès TCP à clamd |
| 3 | Rechargement effectif | `RELOAD` par le socket de `NotifyClamd`, « Database correctly reloaded », version chargée = version sur disque |
| 4 | Redémarrages | `restart clamd` puis `restart freshclam` : socket recréé, notification possible |
| 5 | FreshClam indisponible | `stop freshclam` : clamd sain, worker conforme, fraîcheur toujours exigée |
| 6 | Signatures absentes | `test_clamav_freshness.py` (réponses VERSION absentes ou illisibles) |
| 7 | Signatures périmées | `test_clamav_freshness.py`, `test_stale_signatures_never_produce_clean` |
| 8 | EICAR | `test_eicar_is_detected_and_never_copied` (ClamAV réel) et tests de diffusion |

## 5. Point de contrôle E1

Local (conteneur de développement : PostgreSQL réel, Redis, S3 de mise au point, imitation de clamd) :
- tests SQL adversariaux 0010 : **11/11** ; suite base de données : **182/182** (dont D14) ;
- suite API : **234 réussis**, 11 ignorés (RustFS/ClamAV réels) — dont rattachement et conservation **7/7** ;
- tests de fraîcheur des signatures : **9/9** ; migrations up/down/up : OK ; Ruff, mypy : OK.

Windows (pile réelle, ClamAV réel) :

```powershell
cd C:\NurseLearnAI-git
git switch phase-2.2.3e-media-management-ui
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3e.ps1 -Stage e1
```

**Premier passage Windows (2026-11-24, `3d9a9ef`) : 21/32, 11 échecs — bloquant, E2 non commencé.**

| Contrôles | Résultat | Cause |
|---|---|---|
| 0010, privilèges, permission admin seulement | PASS | — |
| FreshClam → clamd (socket, notification, rechargement 28193 = disque, redémarrages, FreshClam arrêté) | PASS (6/6) | Premier essai réel du correctif : concluant |
| Intégrité (volumes, données, objets S3, pilote en brouillon, aucune publication) | PASS | — |
| Non-régression 1.2 → 2.2.2-c (`migrations_a_jour`, `migration_0004…0006`) | FAIL | Scripts antérieurs n'acceptant que `000x` comme révision de tête ; 0010 refusée. Défaut du script, pas du produit. |
| D14 `security_definer_verrouillees` | FAIL | Inventaire exact non mis à jour pour les trois fonctions de 0010. Valeurs observées toutes `false/true` (PUBLIC sans EXECUTE, `search_path` fixé) : les fonctions sont bien verrouillées. |
| 10 contrôles pytest | FAIL (0 test) | Les suites `database/tests` et `services/api/tests` importent chacune leur `conftest` ; collectées dans un seul processus, pytest s'interrompt (code 2). |

Correctif (commit séparé, périmètre E1, aucune exigence affaiblie) :
- révisions de tête acceptées `000x` **ou** `00[1-9][0-9]` dans les six scripts antérieurs ;
- inventaire D14 exact : celui de 0009 seul, ou celui de 0010 (8 fonctions, toutes `false/true`) lorsque la tête est 0010 ou ultérieure ;
- pytest en deux processus (base depuis la racine, API depuis `services/api`), codes de sortie combinés, et 30 dernières lignes journalisées (secrets masqués) si aucun test n'est collecté.

Vérification locale du correctif : base 60/60, API 91 réussis + 1 ignoré (ClamAV réel absent du conteneur).

**Second passage Windows (2026-11-24, `38618c3`) : 31/32.** Non-régression complète verte (2.2.3-d 30/30,
R3 32/32, D14 et antérieures), pytest 150 tests, codes 0/0 (base / API), aucun échec ni saut. Seul échec :
`tests_signatures_absentes_perimees` = 7 au lieu de 9 — défaut du filtre du script (`::test_\S+`), qui
ignorait les deux identifiants paramétrés contenant une espace (`[ClamAV 1.5.5]`, `[ClamAV 1.5.5/]`) ; ces
tests ont bien été exécutés et réussis (code pytest 0). Filtre corrigé (`^\S+::test_.+? (PASSED|…)`), seuil
inchangé ; vérifié en local : 9 lignes reconnues sur 9.

**Troisième passage Windows (2026-11-24, `08e9345`) : 32/32, 0 échec — point de contrôle E1 VERT.**
Non-régression 2.2.3-d 30/30, R3 32/32 (et D14, 2.2.3-c … 1.2) ; 0010 appliquée (76 tables) ; FreshClam →
clamd : 6/6 (rechargements 2 → 3, chargée = disque = 28193) ; pytest 152 tests, codes 0/0, aucun échec ni
saut (SQL 0010 : 11, D14 : 15, rattachement : 5, conservation : 2, éditions figées : 4, ClamAV réel : 19
dont EICAR, signatures : 9, diffusion : 27, éditorial : 30) ; intégrité intacte. Journal
`phase2.2.3e-20261124-215507.log`.

## 6. E2 — Interfaces des médias et relais Web

### 6.1 Ajouts à l'API (lecture seule, aucune migration)

| Ajout | Détail |
|---|---|
| `GET /api/v1/editorial/media` (paramètres facultatifs) | `q` (motif littéral, `%` et `_` échappés), `kind`, `state` (7 états), `include_deleted`, `limit` 1–500, `offset` ; total filtré dans `X-Total-Count` ; `attachment_count` par média. Sans paramètre : comportement 2.2.3-b inchangé. Portée toujours calculée par le serveur. |
| `GET /api/v1/editorial/quotas/media` | Occupation dérivée de `media_assets` (même calcul que `nl_media_scope_usage`) et quota (`media_usage` ou défaut 1/5/20 Gio) ; portées : l'utilisateur, ses établissements où il peut créer, la plateforme s'il peut y créer. Aucun chiffre d'un autre établissement. Aucune écriture. |

Inventaires mis à jour : opérations éditoriales 40 → 41 (`test_editorial.py`, `validate-phase2.2.2b.ps1`) ;
inventaire `/api/v1/editorial/media` inchangé (8 opérations, aucune diffusion).

### 6.2 Relais Web → media-delivery (`apps/web/app/relais/medias/…`)

| Route (même origine) | Destination fixe (serveur) | Contrôles |
|---|---|---|
| `GET /relais/medias/{id}/apercu` | `media-delivery /api/v1/media/{id}/preview` | lecture : `Sec-Fetch-Site` ∈ {same-origin, none}, `Origin` = hôte ; UUID ; session |
| `GET /relais/medias/{id}/diffusion?release=&lesson_version=` | `media-delivery /api/v1/media/{id}/content` | idem + contexte pédagogique (UUID) obligatoire |
| `GET /relais/medias/{id}` / `DELETE` | API `GET` / `DELETE /editorial/media/{id}` | suivi d'analyse ; abandon (mutation : même origine) |
| `POST /relais/medias` | API `POST /editorial/media` | même origine ; JSON ≤ 16 Kio ; champs du contrat seulement |
| `PUT /relais/medias/{id}/contenu` | API `PUT /editorial/media/{id}/content` | même origine ; `Content-Length` obligatoire, ≤ 200 Mio ; flux (`duplex: half`) |

- **Session** : cookie HttpOnly `__Host-nl_session` (ou `nl_session` en HTTP local) lu par le serveur Web et
  transmis en `Authorization: Bearer` au service interne, à CHAQUE requête (y compris chaque plage vidéo).
  Jamais dans une URL, jamais dans le JavaScript client, aucun jeton autonome, aucun stockage persistant.
- **Destination** : `MEDIA_DELIVERY_INTERNAL_URL` / `API_INTERNAL_URL` (environnement du serveur) ; le client
  ne fournit qu'un UUID validé → aucune adresse libre, pas de SSRF ; redirections non suivies.
- **En-têtes** : vers l'amont, `Range` et `If-Range` (validés) seulement ; vers le navigateur, liste blanche
  (`Content-Type`, `Content-Range`, `Accept-Ranges`, `Content-Disposition`, `ETag`, CSP `sandbox`, …),
  `Cache-Control: private, no-store`, aucun `Set-Cookie`. Statuts 200/206/304/401/403/404/416/429/503
  relayés ; tout autre → 502. Corps d'erreur réduit à `{status, code}` (aucun détail interne).
- **Flux** : corps amont relayé en flux (aucune mise en mémoire) ; l'abandon du navigateur coupe l'amont
  (`signal`). Next.js retire `Content-Length` (transfert par morceaux) ; `Content-Range` et `Accept-Ranges`
  sont conservés : la recherche dans la vidéo fonctionne par requêtes `Range`.
- **Middleware** : `/relais/` exclu (aucune redirection de langue, aucun rafraîchissement implicite).
- **Aucune journalisation** des requêtes, des sessions ni des contenus par le relais.

### 6.3 Écrans (FR/EN, sans nouveau cadriciel)

| Écran | Contenu |
|---|---|
| `/{fr,en}/edition` | Accueil de l'espace d'édition ; modules de la portée |
| `/{fr,en}/edition/medias` | Médiathèque : recherche, filtres format/état, médias supprimés, pagination (20), taille, date, état antivirus (7 états, texte explicite), conservation légale, rattachements ; quotas (`<meter>`) et limites par fichier |
| `/{fr,en}/edition/medias/televerser` | Téléversement : sélection ou glisser-déposer, validation indicative (format, 5/20/200/1 Mio), progression réelle (XHR `upload.onprogress`, `<progress>` + zone `aria-live`), annulation (abandon de la réservation), suivi de l'analyse (toutes les 2 s, 5 min) ; confirmation du mot de passe si la session sensible a expiré |
| `/{fr,en}/edition/medias/{id}` | Métadonnées, aperçu autorisé (seulement `clean`), actualisation contrôlée pendant l'analyse, révocation (relecteurs, administration), conservation légale + historique (administration), suppression logique |
| `/{fr,en}/edition/modules/{id}` | Leçons et versions → sélecteur |
| `/{fr,en}/edition/versions/{id}/medias` | Sélecteur de l'éditeur pédagogique : médias rattachés (aperçu, retrait), recherche et rattachement (même portée que la leçon) ; version non brouillon = lecture seule (« figée ») |

Lecteurs réutilisables (`components/media/MediaPlayer.tsx`, modes aperçu et apprenant) : image (texte
alternatif, responsive), PDF (téléchargement contrôlé, `Content-Disposition` fixé par media-delivery), MP4
(`<video controls>`, Range) avec pistes VTT (`<track kind="captions">`) ; erreur de lecture (révocation,
session expirée, limite) annoncée (`role="alert"`) avec reprise. Toutes les adresses sont `/relais/medias/…`.

Le mode apprenant est prêt (relais `diffusion` + lecteur) ; son intégration dans une page de leçon
apprenant (catalogue des médias d'une leçon publiée) relève de 2.2.4 et n'est pas développée ici.

### 6.4 Résultats locaux E2 (conteneur de développement)

| Vérification | Résultat |
|---|---|
| pytest API (rattachement + bibliothèque/quotas, édition, téléversement, analyse, diffusion) | 128 réussis, 1 ignoré (ClamAV réel absent) ; 3 nouveaux tests bibliothèque/quotas |
| Suites complètes | API 237 réussis, 11 ignorés (RustFS/ClamAV réels absents) ; base de données 182 réussis |
| ruff, mypy | OK |
| Web : typecheck, ESLint, Prettier | OK |
| Vitest | 64 réussis (dont 30 médias : relais, validation, i18n, composants) |
| `next build` | OK (5 routes de relais, 6 écrans) |
| JavaScript client | aucune occurrence de `Bearer`, `media-delivery`, `nl_session`, `API_INTERNAL`, `nl-media-`, `X-Amz`, `presign` |
| Sonde du relais (Next.js + media-delivery réels) | 401 sans session ou session invalide ; 404 identifiant/contexte invalides ; 403 intersite, origine étrangère, CSRF réservation et téléversement ; 405 méthode ; `private, no-store`, aucun cookie, aucune fuite |
| Parcours réel local à travers le relais (API, media-delivery, Next.js, worker ; ClamAV imité) | 18/19 puis 19/19 : téléversement → quarantaine (jamais servie, 404) → analyse → sain ; aperçu image ; PDF en pièce jointe ; vidéo 206 `Content-Range` ; 416 hors fichier et plages multiples ; VTT ; pages Web sans fuite ; révocation → requête suivante 404 ; session révoquée → 401 |
| Captures (Chrome) et axe WCAG 2.0–2.2 AA sur 8 écrans (FR/EN, bureau et mobile 390 px) | 0 violation, 0 erreur console (après correction du débordement de l'en-tête mobile) |
| Révocation pendant une lecture réelle (vidéo 40 Mo, lecteur lent) | flux coupé (`IncompleteRead`) après ≈ 13 Mo ; voir risque E2-1 |

### 6.5 Risques résiduels propres à E2

| Id | Risque | Traitement |
|---|---|---|
| E2-1 | Après une révocation, les octets déjà en tampon entre media-delivery, le relais et le navigateur (≈ 10 Mo mesurés en local, selon les tampons TCP) sont encore livrés avant la coupure (revérification toutes les 10 s, D3). | Accepté et documenté ; les octets transmis ne peuvent pas être repris (déjà vrai en 2.2.3-d). |
| E2-2 | Réponse 200 sans `Content-Length` (transfert par morceaux) via le relais. | Lecture et recherche par `Range` (206) vérifiées ; contrôle navigateur réel en E3 (Playwright). |
| E2-3 | L'intégration apprenant (page de leçon) n'est pas développée (2.2.4). | Relais et lecteur prêts ; hors périmètre. |
| E2-4 | Le relais applique `Sec-Fetch-Site`/`Origin` ; un navigateur très ancien sans ces en-têtes est admis en lecture (cookie `SameSite=Lax` non envoyé en sous-ressource intersite). | Mutations : même origine exigée sans exception. |

### 6.6 Validation Windows E2

```powershell
cd C:\NurseLearnAI-git
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3e.ps1 -Stage e2
```

**Premier passage Windows E2 (2026-11-25, `9fa3b97`) : 45/47.** Tout E1 vert (non-régression complète,
0010, FreshClam 6/6, pytest 155 tests codes 0/0), bibliothèque et quotas 3/3, relais 8/8 (401, 404, 403
intersite et CSRF, 405, sans cache ni cookie ni fuite, destination fixe joignable), typecheck/lint/format,
build, JavaScript client propre. Deux échecs, tous deux dans le script (aucun dans le produit) :
- `fichiers_e2_presents` : `Test-Path` traite les crochets du dossier Next.js `[id]` comme des jokers →
  `Test-Path -LiteralPath` ;
- `web_vitest` : Vitest a réussi (code 0) mais le motif de lecture contenait « ✓ », altéré entre
  PowerShell 5.1 et Docker → motifs ASCII seulement, `NO_COLOR=1`, total et sous-ensemble médias lus
  séparément (seuils inchangés : ≥ 64 et ≥ 30). Vérifié en local : 64 et 30.

**Second passage Windows E2 (2026-11-25, `db84cd7`) : 47/47, 0 échec — validation E2 VERTE.** Vitest 64
(dont 30 médias), relais 8/8, pytest 155 (codes 0/0), non-régression complète verte. Journal
`phase2.2.3e-20261125-005936.log`.

## 7. E3 — Validation de bout en bout

### 7.1 Parcours Playwright (`apps/web/e2e/medias.spec.ts`, projet `chrome-media`)

Pile réelle (Next.js, API, media-delivery, PostgreSQL, Redis, RustFS, **ClamAV réel**, media-worker),
Google Chrome (décodage H.264), exécution en série, axe WCAG 2.0–2.2 AA sur chaque écran visité.
Données **fictives** préparées par [`scripts/e2e_media_setup.py`](../scripts/e2e_media_setup.py) :
établissements « Établissement fictif E2E A/B », sept comptes `e2e-medias-*@example.test` **stables**
(inscrits une seule fois ; mot de passe aléatoire renouvelé à chaque passage, jamais affiché, transmis par
un fichier temporaire supprimé en fin de validation), leçons en brouillon et soumise, média de
l'établissement B, fichier de test EICAR construit **en mémoire** et déposé en quarantaine (cas adverse :
aucun format ne l'accepterait), vidéo MP4 H.264 réelle de 40 s (20 Mio) générée par ffmpeg.

| # | Parcours | Vérifications principales |
|---|---|---|
| 01 | Connexion auteur | formulaire réel, lien « Édition », axe |
| 02 | Médiathèque | quotas, limites, filtres format/état, axe |
| 03 | Téléversement image | réservation + envoi par le relais, progression, aucune requête hors origine |
| 04 | Quarantaine | état reçu = attente ; jamais « disponible » ; relais 404 tant que non sain |
| 05 | ClamAV réel | verdict `clean` par media-worker |
| 06 | Média sain | aperçu, texte alternatif, image chargée, axe |
| 07 | Rattachement | brouillon, message, élément rattaché, axe |
| 08 | Aperçu éditorial | image du sélecteur servie par le relais |
| 09 | MP4 Range | 206 `Content-Range`, 416, lecture réelle, recherche à 30 s |
| 10 | PDF | téléchargement (`%PDF`), `Content-Disposition: attachment` |
| 11 | VTT | piste `captions`, `text/vtt`, `textTracks` = 1, axe |
| 12 | Infecté | EICAR → `infected` (ClamAV réel), bloqué, relais 404, non rattachable |
| 13 | Non analysé | réservation sans fichier : relais 404, aperçu indisponible, non proposé, abandon 204 |
| 14 | Inter-établissements | 404 page, relais et sélecteur ; aucune mention de l'autre établissement |
| 15 | Édition figée | version soumise : lecture seule, aucun bouton, axe |
| 16 | Révocation pendant la lecture | relecteur révoque pendant la lecture → requête suivante 404 → alerte, page relue « Révoqué — bloqué » |
| 17 | Session révoquée | cookie HttpOnly ; après déconnexion, l'ancien identifiant rejoué → 401 |
| 18 | Limites de débit | 429 avec `Retry-After`, aucun autre statut |
| 19 | Quota dépassé | 413 `quota_exceeded`, message explicite, réservations libérées |
| 20 | Conservation légale | motif obligatoire, pose/levée, historique (2 événements), média révoqué toujours 404, invisible pour un auteur |
| 21 | Clavier | lien d'évitement, filtre, ouverture d'un média, champ fichier et bouton atteignables |
| 22 | FR/EN | libellés, états, `lang` |
| 23 | Mobile 390 px | aucun débordement horizontal (4 écrans), axe |
| 24 | Aucune fuite | aucun jeton, `Bearer`, compartiment, `X-Amz`, adresse interne dans les URL, en-têtes, HTML, JS, JSON ; `document.cookie` sans session ; stockage local vide de jeton |

Mise au point locale (pile locale, ClamAV imité, Chrome) : **24/24**. Deux corrections issues de ces
parcours : (1) sur `<source>`, une erreur de chargement n'atteint pas `<video>` → gestionnaire ajouté ;
(2) après une interruption, le lecteur relit la page serveur pour que l'état affiché suive (« Révoqué »).

### 7.2 Correctif : compteurs de téléversement par pile

Les compteurs horaires de téléversement (`nl:media:rate:*`) étaient partagés entre toutes les piles
utilisant le même Redis : les tests pytest (≈ 110 téléversements par fichier de tests) consommaient le
quota horaire « plateforme » (300) de la pile réelle, provoquant des 429 étrangers au produit. Ils sont
désormais préfixés par l'espace de noms de la pile (`RATE_LIMIT_SALT`, sinon dérivé de la base), comme la
diffusion depuis 2.2.3-d. Limite inchangée dans chaque pile ; test
`test_upload_rate_counters_are_namespaced_per_stack`.

### 7.3 Sauvegarde chiffrée et restauration isolée (D22)

Voir [GUIDE-SAUVEGARDE-RESTAURATION-MEDIAS.md](GUIDE-SAUVEGARDE-RESTAURATION-MEDIAS.md). Mise au point
locale : archive 128 Mio (173 objets), déchiffrement authentifié ; altération et mauvaise clé **refusées** ;
restauration isolée : tables, révision, états antivirus, verdicts, rattachements, permissions D14 et
`verify_head` conformes. Défaut trouvé et corrigé : sans `--create`, les droits de la base (révocation de
`TEMPORARY`, `CONNECT` des rôles applicatifs) n'étaient pas sauvegardés et `verify_head` refusait la base
restaurée. La vérification des objets a aussi détecté, en local, les objets perdus par le stockage S3
imité redémarré (preuve que le rapprochement base ↔ objets fonctionne) ; sous Windows, RustFS est
persistant.

Constat annexe corrigé : `.dockerignore` n'excluait pas `backups/` — les sauvegardes locales (dont les
dumps D14 en clair) étaient envoyées au moteur Docker à chaque construction (jamais copiées dans une
image). Exclues désormais, avec les rapports Playwright.

### 7.4 Validation Windows E3

```powershell
cd C:\NurseLearnAI-git
powershell -ExecutionPolicy Bypass -File .\scripts\validate-phase2.2.3e.ps1 -Stage e3
```

**Premier passage Windows E3 (2026-11-25, `bf11cb9`) : 76/78.**

- **24/24 parcours Playwright** sur la pile réelle (ClamAV réel : EICAR → `infected`, vidéo H.264 lue,
  révocation pendant la lecture, quota, conservation légale, clavier, FR/EN, mobile, aucune fuite) ;
  14 captures ; préparation des données fictives (7 comptes, 6 fichiers) ; dossier temporaire retiré.
- E1 et E2 verts (0010, FreshClam 6/6 avec une **notification réelle observée** : 28193 → 28194, pytest
  155, relais 8/8, Vitest 64, build, JavaScript client propre) ; intégrité intacte.
- `non_regression_2_2_3d_et_anterieures` : **FAIL, cause environnementale** — à 08:44, Docker Desktop ne
  joignait plus Docker Hub (`lookup registry-1.docker.io: no such host`) : la reconstruction de la pile
  de la chaîne 1.2 a échoué, puis tous les contrôles dépendant de l'API et du site ont échoué en cascade.
  Le réseau est revenu ensuite (E1, E2 et E3 verts sur la pile réelle). S'y ajoute un échec du premier
  test de diffusion de la chaîne 2.2.3-d (`test_authorized_learner_gets_clean_image_with_safe_headers`),
  **sans cause journalisée** ; les mêmes 27 tests ont réussi dans le même passage (bloc E1) et lors de
  tous les passages précédents. Non masqué : le script 2.2.3-d journalise désormais le résumé pytest
  (`-rfE`) et les premières lignes d'assertion de tout échec.
- `sauvegarde_restauration_executee` : **FAIL, défaut du script** — `backup-restore-media.ps1` contenait
  `((commande); $LASTEXITCODE -eq 0)`, deux instructions dans des parenthèses simples : erreur de syntaxe
  PowerShell, le script n'a pas démarré. Corrigé ; attente de l'écoute TCP du PostgreSQL jetable
  (l'image démarre d'abord un serveur temporaire sur socket local).

**Second passage Windows E3 (2026-11-25, `deb0e5d`) : 81/83.** Réseau Docker rétabli (`docker pull`
préalable réussi).

- **24/24 parcours Playwright**, 14 captures ; E1 et E2 verts ; FreshClam : notification réelle observée.
- **Sauvegarde** : archive chiffrée de 76,6 Mio (12 objets de quarantaine, 10 sains, 26 médias,
  2 rattachements), hors Git, clé absente du manifeste ; intégrité vérifiée (25 membres) ; **altération
  et mauvaise clé refusées** ; aucun volume créé ni supprimé ; base de travail inchangée et injoignable
  depuis le réseau isolé.
- Chaîne 2.2.3-d : le test de diffusion du premier passage a réussi (27/27).
- `restauration_isolee_conforme` : **FAIL** — le RustFS jetable n'est jamais devenu joignable
  (`EndpointConnectionError`). Il était placé sur un tmpfs ; RustFS s'exécute en utilisateur 10001 et
  ses guides n'emploient que des disques réels. Corrigé : attente de `/health/ready`, journal RustFS
  conservé, et, s'il ne démarre pas en mémoire, relance sur la couche d'écriture du conteneur (toujours
  sans volume, détruite avec le conteneur) ; l'outil échoue désormais immédiatement avec un message clair
  si le stockage reste injoignable.
- `python_ruff_mypy` (chaîne 2.1) : **FAIL, défaut de mes scripts** — Ruff y vérifie tout le dépôt ;
  `scripts/media_backup.py` et `scripts/e2e_media_setup.py` présentaient 18 écarts (longueur de ligne,
  appels de processus et d'URL sans justification). Corrigés (justifications explicites :
  binaires fixes, adresse interne fixe) ; `ruff check .` et `ruff format --check .` verts sur le dépôt.

**Troisième passage Windows E3 (2026-11-25, `06f4c27`) : 83/83, 0 échec — VALIDATION 2.2.3-e VERTE.**
Journal `phase2.2.3e-20261125-110841.log`.

| Bloc | Résultat |
|---|---|
| Non-régression (2.2.3-d 30/30, R3 32/32, D14, 2.2.3-c → 1.2) | verte, Ruff et mypy compris |
| E1 : 0010, privilèges, FreshClam (notification réelle 28193 → 28194, rechargements 8 → 9) | 16/16 |
| E1 : pytest pile réelle | 155 tests, codes 0/0 (SQL 0010 : 11, D14 : 15, rattachement : 8, conservation : 2, éditions figées : 4, ClamAV réel : 19 dont EICAR, signatures : 9, diffusion : 27, éditorial : 30) + bibliothèque/quotas : 3 |
| E2 : relais (8 contrôles), Vitest 64 (dont 30 médias), typecheck, lint, format, build, JavaScript client propre | vert |
| E3 : 24 parcours Playwright + axe (Chrome, ClamAV réel) | **24/24**, 0 échec, 0 saut, 0 instable ; 14 captures |
| E3 : sauvegarde chiffrée | 114,6 Mio ; 18 objets de quarantaine, 15 sains ; 39 médias ; 3 rattachements ; intégrité vérifiée (36 membres) ; altération et mauvaise clé refusées |
| E3 : restauration isolée (tmpfs, réseau interne) | tables, rattachements, empreintes des octets, objets sains et de quarantaine, états antivirus, verdicts, permissions D14, `verify_head` : **tous conformes** ; aucun volume ; base de travail inchangée et injoignable |
| Intégrité | aucun volume, donnée ni objet perdu ; aucun programme médical publié ; pilote PAB en brouillon ; aucune URL publique ni présignée |

Le RustFS jetable a démarré en mémoire dès que l'attente de `/health/ready` a été ajoutée ; le repli sur la
couche du conteneur n'a pas été nécessaire.

## 8. Registre des risques résiduels (2.2.3-e)

| Id | Risque | État après 2.2.3-e |
|---|---|---|
| R1 | Migrations exécutées avec des privilèges élevés | **Conservé** — voir [D14-RESIDUAL-RISKS.md](D14-RESIDUAL-RISKS.md) |
| R2 | Lecture large des tables par l'API | **Conservé** — idem |
| R3-1 | L'API peut remplacer ou supprimer tout objet de quarantaine | **Conservé** (détecté par le worker) |
| R3-2 | Le worker peut modifier ou supprimer des objets sains | **Conservé** — WORM/versionnement avant production ; la restauration vérifiée (D22) limite l'impact |
| R3-3 | Clé racine RustFS dans `.env` local | **Conservé** — coffre de secrets en production |
| R3-4 | Compatibilité RustFS à revérifier à chaque version | **Conservé** (image épinglée) |
| D3-1 | Octets déjà transmis avant une révocation | **Conservé** ; mesuré via le relais (E2-1) |
| D3-2 | Diffusion en `nl_api` (lecture large) | **Conservé** |
| D3-3 | Rattachement et `legal_hold` sans route d'API | **Corrigé** (0010, fonctions contrôlées, routes, interfaces) |
| D3-4 | Pas de nouvelle analyse périodique des médias sains | **Conservé** |
| D3-5 | Limite par adresse partagée API/diffusion | **Conservé** |
| D3-6 | Apprenants d'établissement sans accès aux contenus privés | **Conservé** (2.2.4) |
| D3-7 | Intégrité par métadonnée seulement | **Réduit** : la restauration isolée recalcule le SHA-256 des octets ; pas encore de relecture périodique en production |
| E2-1 | Octets en tampon livrés après une révocation (mesures § 9.5 : 6,75 Mio en direct, 9,0 Mio par le relais avec un lecteur arrêté ; ≤ 13,7 Mio avec un lecteur lent) | Accepté, documenté ; aucune révocation rétroactive des octets déjà reçus |
| E2-2 | 200 sans `Content-Length` via le relais | Lecture et recherche vérifiées (E3 09, 16) |
| E2-3 | Intégration apprenant (page de leçon) non développée | 2.2.4 / 2.2.5 |
| E2-4 | Navigateur sans `Sec-Fetch-Site`/`Origin` admis en lecture | Mutations : même origine exigée |
| E3-1 | Sauvegarde lit la quarantaine avec `nl-media-scanner` (qui peut écrire dans le sain) | Identité de sauvegarde dédiée en lecture seule à créer |
| E3-2 | Clé de sauvegarde dans `.env` local ; aucune copie hors site | Coffre, rotation, copie chiffrée hors site |
| E3-3 | Sauvegarde manuelle, non atomique entre base et objets | Planification, fenêtre calme, rapprochement (déjà détecté) |
| E3-4 | Comptes de test `e2e-medias-*@example.test` conservés dans la base de développement | Fictifs, rôles de test ; jamais en production |
| E1-1 | Les fonctions `nl_media_attach`/`nl_media_detach` ne verrouillent pas elles-mêmes la version de leçon ; la sérialisation avec la soumission repose sur le `FOR UPDATE` posé par l'API (même transaction). Un client qui appellerait la fonction directement avec les identifiants `nl_api`, hors API, pourrait croiser une soumission (l'approbation et la publication revérifient toutefois `clean`). | Prochaine migration : `FOR SHARE` sur la version dans les fonctions |
| D9 | Authentification multifacteur (relecteurs, administrateurs) absente | **Conservé** — bloque toute publication réelle et la production |
| D10 | Validation médicale indépendante (relecteur qualifié) absente | **Conservé** — bloque toute publication réelle |
| AUD | Audit de sécurité externe non réalisé | **Conservé** — exigé avant la production |

**Le système n'est pas prêt pour la production.** Risques bloquants avant toute mise en production :
R3-2 et R3-3 (stockage et secrets), D3-2 et D3-4, E3-1 à E3-3 (sauvegardes de production : identité dédiée,
gestion de la clé, copie hors site, planification, procédure de bascule), D9 (MFA), D10 (validation médicale
indépendante) et audit de sécurité externe.

## 9. Audit avant push (2026-11-25)

### 9.1 Dépôt
- Branche `phase-2.2.3e-media-management-ui`, 11 commits sur `main` (`e3a3ec3`), copie propre.
- **Artefact retiré de l'historique** : deux instantanés Redis locaux (`dump.rdb`, `services/api/dump.rdb` :
  compteurs de limites et files de test de l'environnement de développement, aucun secret applicatif)
  avaient été commités par erreur (E1 et E2). La branche n'ayant jamais été poussée, ils ont été retirés
  de tout l'historique local (réécriture des 10 commits, contenu final identique), puis `dump.rdb` a été
  ajouté à `.gitignore`.
- Recherche de secrets : aucun dans le diff ni dans les messages de commit (seule occurrence : un jeton
  factice de test Vitest) ; sur le poste Windows, les 11 valeurs secrètes de `.env` et
  `BACKUP_ENCRYPTION_KEY` ont **0 occurrence** dans les fichiers versionnés, les journaux et les captures
  (seul `S3_ACCESS_KEY=nurselearn`, identifiant public par défaut, apparaît, comme dans `.env.example`).
- `.env`, `backups/`, journaux JSON, captures et rapports Playwright : hors Git (`git check-ignore`).
- Migrations 0001 à 0009 inchangées ; 0010 additive ; aucun programme médical publié ; pilote PAB en
  brouillon ; périmètre : `apps/web` (médias), `services/api` (médias), `database` (0010 et tests),
  `infra` (ClamAV, sauvegarde), `scripts`, `docs`, CI.

### 9.2 Migration 0010

| Exigence | Constat |
|---|---|
| Propriétaire | `nl_owner` (`nl_enforce_ownership()` en fin de migration ; vérifié par `test_postgres_roles`, inventaire SECURITY DEFINER) |
| Chemin de recherche | `SET search_path = public, pg_temp` sur les trois fonctions |
| EXECUTE | révoqué à PUBLIC ; accordé nominativement à `nl_api` (vérifié : `PUBLIC = false`) |
| Injection SQL | aucune requête dynamique (`EXECUTE`, `format`) : paramètres typés `uuid`, `boolean`, `text` |
| Acteur | `nl_actor()` lève une erreur sans `nl.actor_id` |
| Portée et permissions | `content:create` dans la portée de la leçon (fonction + déclencheur 0007) ; `media:legal_hold` dans la portée du média + `user:manage` (déclencheur 0007) ; permission réservée à `admin` par déclencheur |
| Éditions figées | déclencheur 0007 : insertion et suppression refusées hors brouillon ; la soumission, l'approbation et la publication exigent des médias sains |
| Concurrence | conservation : `SELECT … FOR UPDATE` du média ; rattachement et retrait : la version est verrouillée `FOR UPDATE` par l'API dans la même transaction que la soumission (sérialisation) ; clé primaire (version, média) ; risque résiduel E1-1 (appel direct hors API) |
| Journal | `media_legal_hold_events` : insertion réservée à la fonction, `UPDATE`/`DELETE`/`TRUNCATE` interdits |
| Écriture directe | `nl_api` : aucun INSERT/DELETE sur `lesson_version_media`, aucun UPDATE de `legal_hold`, aucun INSERT dans le journal (vérifié par `verify_head.sql` et le contrôle Windows) |
| Verdicts, quotas, audit | les fonctions ne modifient que `lesson_version_media`, `media_assets.legal_hold`/`legal_hold_reason` et le journal ; verdicts (`media_scan_events`, rôle `nl_media_scanner`), quotas (`media_usage`) et `audit_logs` (ajout seul) restent hors de portée de l'API |

### 9.3 Tests ignorés localement (11)

| Test | Motif | Dépendance | Exécuté par | Résultat pile réelle | Sécurité |
|---|---|---|---|---|---|
| `test_mailpit.py::test_verification_email_through_mailpit` | `MAILPIT_URL` absent | Mailpit | CI (job API), Windows (chaîne 1.2) | réussi | non critique (courriel) |
| `test_media_delivery.py::test_no_direct_or_anonymous_storage_access` | RustFS réel requis (IAM, accès anonyme) | RustFS | CI (job API, `NL_TEST_S3_KIND=rustfs`), Windows (2.2.3-d) | réussi (27/27 diffusion) | **critique** — couvert |
| `test_s3_identities.py` (9 tests : politiques propres, quarantaine seule pour l'API, analyse, lecture seule, anonyme refusé, élévation, multipart, objet sain inaltérable, chaîne complète) | RustFS réel requis | RustFS | CI (job API), Windows (R3, 2.2.3-d) | réussis (9/9 « adversariaux R3 RustFS réel ») | **critiques** — couverts |

Aucun test n'est ignoré dans tous les environnements ; aucun seuil n'a été réduit.

### 9.4 Relais Web

| Exigence | Constat |
|---|---|
| Cookie | HttpOnly ; `Secure` et préfixe `__Host-` par défaut (`SESSION_COOKIE_SECURE=true`), désactivé explicitement en HTTP local ; `SameSite=Lax` (non envoyé en sous-ressource intersite) |
| CSRF | mutations : même origine exigée (`Origin` / `Sec-Fetch-Site`) ; lectures : `cross-site`/`same-site` refusés (403, sonde Windows) |
| Destination / SSRF | `MEDIA_DELIVERY_INTERNAL_URL` et `API_INTERNAL_URL` du serveur ; seul un UUID validé vient du client ; redirections non suivies |
| Jeton | jamais dans une URL ni dans le JavaScript (parcours 24, build analysé) |
| Range | `Range`/`If-Range` validés et relayés ; 206 et 416 (+ `Content-Range: bytes */n`) relayés (Vitest, parcours 09) |
| Cache | `Cache-Control: private, no-store`, aucun `Set-Cookie` |
| Mémoire | flux sans mise en mémoire : RSS de Next.js **250,8 Mio au repos = 250,8 Mio** avec un flux vidéo de 40 Mo arrêté côté client |
| Déconnexion | client coupé brutalement : la connexion relais → diffusion n'a plus aucune donnée en file et se ferme en 3 à 6 s (signal d'annulation propagé) |
| Session révoquée | 401 immédiat (parcours 17) |
| Média révoqué | requête suivante 404, flux en cours coupé à la revérification (parcours 16, § 9.5) |

### 9.5 E2-1 — octets livrés après une révocation (mesure locale, vidéo de 40 Mo, 1 Mio reçu avant)

| Chemin | Lecteur | Reçus après la révocation | Issue |
|---|---|---|---|
| direct (media-delivery) | arrêté 15 s | 6,75 Mio | flux coupé ; requête suivante 404 |
| relais Web | arrêté 15 s | 9,0 Mio | flux coupé (`IncompleteRead`) ; 404 |
| direct | lent (≈ 640 Kio/s) | 11,25 Mio | coupé après 18 s ; 404 |
| relais Web | lent | 13,7 Mio | coupé après 22 s ; 404 |

Lecture : (1) **octets déjà reçus** avant la révocation : 1 Mio (hors de portée de toute révocation) ;
(2) **octets déjà en transit** : lecteur arrêté, media-delivery bloqué par la contre-pression ne lit plus
rien — 6,75 Mio en direct (tampons TCP de la diffusion et du client), **+ 2,25 Mio ajoutés par le relais**
(second saut TCP et tampons de flux Node/undici) ; (3) **nouveaux octets lus après la révocation** :
avec un lecteur qui consomme, la diffusion continue jusqu'à la revérification suivante (≤ 10 s) —
≈ 4,5 à 4,7 Mio supplémentaires à ce débit. Ces volumes dépendent des tampons TCP du système et du débit
du lecteur ; aucune révocation ne peut reprendre des octets déjà livrés (D3-1).

### 9.6 Sauvegarde et restauration

| Exigence | Constat |
|---|---|
| Chiffrement | AES-256-GCM (bibliothèque `cryptography`), blocs de 4 Mio, étiquette par bloc |
| Nonces | préfixe aléatoire de 64 bits par archive (`secrets`) + compteur de bloc de 32 bits : jamais réutilisés dans une archive ; collision entre archives d'une même clé ≈ 2⁻⁶⁴ par paire |
| Intégrité | ordre, fin et contenu authentifiés (données associées) ; SHA-256 de l'archive dans le manifeste |
| Altération / mauvaise clé | refusées (Windows) |
| Clair sur disque | aucun : dump et déchiffrement en tmpfs ; restauration RustFS en mémoire (repli éventuel sur la couche du conteneur, détruite avec lui) |
| Manifeste | sans clé (empreinte de clé seulement) — vérifié |
| Isolation | réseau Docker interne, conteneurs jetables, base de travail injoignable et inchangée |
| Permissions | droits de la base (`--create`), rôles sans mot de passe, `verify_head` et contrôles D14 conformes |
| Empreintes | SHA-256 recalculé sur les octets restaurés = `media_assets.sha256` |

Limites (E3-1 à E3-4) inchangées : ce n'est **pas** une sauvegarde de production opérationnelle.

### 9.7 Couverture CI des 24 parcours

Nouveau job `media-e2e` (« Parcours médias (pile réelle, ClamAV réel, Playwright + axe) ») : pile Docker
complète avec le profil `media`, secrets aléatoires par exécution, signatures ClamAV fraîches (freshclam +
contrôle fermé du worker, aucun simulacre), données synthétiques (`scripts/e2e_media_setup.py`), Google
Chrome, 24 parcours + axe, bilan exigé (24 réussis, 0 échec, 0 saut, 0 instable), artefacts assainis
(captures et rapport JSON ; traces désactivées ; recherche des mots de passe synthétiques et des secrets
de la pile avant publication). L'intégration dans le job Web existant n'est pas raisonnable : il n'a que
PostgreSQL et Redis en services, sans RustFS, ClamAV, worker, diffusion ni relais conteneurisé. La
sauvegarde/restauration reste validée sous Windows seulement.

### 9.8 Premier passage CI du job `media-e2e` (PR n° 21)

Échec sur le parcours 05 (`scan_failed`) : au démarrage à froid, freshclam a téléchargé `daily` 28194 avant
que clamd n'ouvre son socket (« Clamd was NOT notified »). Clamd est resté sur 28191 (83 h) et le worker
a **refusé d'analyser** (`signatures_stale`, comportement fermé attendu). L'étape CI de fraîcheur aurait dû
forcer un RELOAD, mais elle s'appuyait sur le code de sortie de `--self-check`, un diagnostic qui renvoie
toujours 0. Correction limitée à la CI : la fraîcheur est lue dans le JSON (`signatures_fresh: true`
exigé), sinon RELOAD et nouvel essai. Aucun simulacre, aucun contournement du contrôle du worker.
Remarque d'exploitation (rattachée à D3-5) : sur une pile neuve, la première notification de freshclam
peut être perdue ; le worker reste fermé jusqu'au prochain cycle de freshclam ou à un RELOAD manuel
(`clamdscan --reload`).
