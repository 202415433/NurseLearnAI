# Validation — Étape 2.2.3-e : interfaces des médias protégés et validation de bout en bout

Branche : `phase-2.2.3e-media-management-ui` (depuis `main` @ `e3a3ec3`, 2.2.3-d fusionnée).
Trois sous-lots successifs, un commit chacun : **E1** correctifs préalables · **E2** interfaces · **E3** validation de bout en bout.
Aucun développement 2.2.4 ni tuteur IA ; aucune URL S3 ; aucun jeton autonome ; aucune publication médicale.

> **État : E1 validé (32/32 Windows) ; E2 développé, validation Windows `-Stage e2` en attente.** E3 ne
> démarre qu'après une validation E2 verte.

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

*Résultat Windows E2 : à compléter.*
