# Validation — Étape 2.2.3-a : migration 0007, modèle des médias, invariants et quotas

**Branche** : `phase-2.2.3-secure-media`, à partir de `main` = `a9c0a04`.
**Références** : [plan 2.2.3](PHASE2.2.3-PLAN.md) · [ADR-0011](adr/0011-medias-antivirus.md) · décisions D7, D15–D24 · exigences S1–S14.

**Périmètre.** Schéma et invariants **uniquement**. Restent hors de cette étape :
- point d'accès de téléversement ;
- worker et ClamAV ;
- diffusion.

**Ce qui n'est pas touché.**
- Aucune migration de 0001 à 0006 n'est modifiée.
- `learning_resources` est conservée intacte, et aucune donnée n'est supprimée.
- Aucune publication médicale n'a lieu.

## 1. Analyse préalable

| Élément étudié | Constat utilisé par 0007 |
|---|---|
| Migrations 0001 à 0006 | SQL versionné dans `database/schema/`, exécuté par Alembic via `nl_sql.run_sql`. Retour arrière explicite qui **refuse** de détruire des données. Erreurs avec `HINT 'nl:<code>'` (convention 0006) |
| Modèles pédagogiques | Leçons et versions de leçon. Portée d'une leçon = établissement de son module (`nl_lesson_org`) |
| Éditions figées (ADR-0009, ADR-0010) | Une version de leçon ne quitte le brouillon que par le workflow. 0007 ajoute une **garde distincte** (`trg_lesson_versions_30_media`) sans réécrire de fonction existante |
| Établissements et permissions | `nl_user_has_permission(user, perm, org)` ; acteur déclaré par `nl.actor_id` (`nl_actor()`) |
| Signalements (`content_issue_reports`) | Liés aux versions de leçon. Leur clé étrangère historique est en `CASCADE` : 0007 empêche toute suppression ou purge d'un média lié à une version signalée |
| Journal d'audit | `audit_logs` en ajout seul et chaîné par hachage ; les déclencheurs de 0007 y écrivent (quotas, conservation légale) |
| `learning_resources` (0001) | Vide, non utilisée, **conservée telle quelle** |
| Conventions | Déclencheur `updated_at` sur toute table qui a la colonne ; chaque clé étrangère a une stratégie explicite (`verify_head.sql`) ; identifiant de révision ≤ 32 caractères |
| Données de référence | `0001_reference_data.sql` donnait **toutes** les permissions à `admin` : modifié pour exclure `media:scan` (voir § 4) |

## 2. Schéma final (migration `0007_secure_media_foundation`, additive et réversible)

| Élément | Nombre |
|---|---|
| Nouvelles tables | **4** : `media_assets`, `media_scan_events`, `lesson_version_media`, `media_usage`. Total : **75** tables |
| Clés étrangères ajoutées | **10**, toutes avec une stratégie explicite. Une seule `CASCADE` : rattachements → version de leçon, et seulement pour un brouillon. Les autres sont `RESTRICT` |
| Contraintes `CHECK` ajoutées | **40** |
| Index ajoutés | **14**, dont 3 uniques partiels (une ligne de quota par portée) |
| Déclencheurs ajoutés | **11** : 9 sur les tables médias, 1 sur `lesson_versions`, 1 sur `role_permissions`. Total à la tête : 79. Aucun n'utilise `pg_trigger_depth()` |
| Fonctions ajoutées | **15** |
| Vue ajoutée | **1** : `media_usage_totals` (quota, occupé, réservé, disponible) |
| Permission ajoutée | `media:scan`, attribuée à **aucun** rôle |

Totaux à la tête, mesurés par `verify_head.sql` : 75 tables, 186 clés étrangères, 188 index, 79 déclencheurs.

### Tables

- **`media_assets`**
  - identité, portée (`organization_id` NULL pour le public) et déposant ;
  - `kind`, `mime_type` (liste blanche D15), `declared_bytes` (≤ maximum D15 du type), `byte_size`
    (≤ déclarée), `sha256` ;
  - `state` (modèle du § 3), `scan_attempt` ;
  - `quarantine_key` et `clean_key` aléatoires (format UUID/UUID) ;
  - `clean_at`, `access_epoch`, `revoked_reason` ;
  - `legal_hold` et son motif ;
  - `display_name` assaini (sans `/`, `\` ni caractère de contrôle), `license`, `attribution` ;
  - `alt_text_i18n`, obligatoire en FR pour une image ;
  - `captions_for` (sous-titres → vidéo) ;
  - `reservation_expires_at`, suppression logique (`deleted_at`, `deleted_reason`), `purged_at`.
- **`media_scan_events`** : journal des analyses en **ajout seul**. Il porte :
  - le moteur (`clamav`), sa version et celle des signatures ;
  - la date des signatures, qui doivent avoir **moins de 48 h** pour un verdict « sain » ;
  - la menace, l'empreinte analysée et la durée ;
  - l'auteur du verdict, imposé par la base.
- **`lesson_version_media`** : rattachement d'un média à une version de leçon. Possible en brouillon
  seulement, figé ensuite ; l'auteur du rattachement est imposé par la base.
- **`media_usage`** : **quota seulement**, par portée (utilisateur, établissement, plateforme).
  L'occupation et les réservations ne sont **pas stockées** : elles sont **dérivées** des médias
  (`nl_media_scope_usage`, vue `media_usage_totals`). Il n'existe donc aucun compteur à falsifier,
  et aucun compteur négatif possible (somme de tailles positives).

## 3. Modèle des médias et transitions

```
pending_upload ──réception (déposant)──► quarantined ──► scanning ──► clean ──► infected
      │                                       ▲            │  │  │        (détection a posteriori)
      │ expiration / abandon                  └────────────┘  │  └──► infected ──► scanning (nouvelle analyse)
      ▼ (suppression logique, réservation libérée)            └─────► scan_failed ──► scanning
                         toute étape après réception ──► revoked (définitif)
```

| Transition | Qui | Conditions vérifiées par la base |
|---|---|---|
| (création) → `pending_upload` | Déposant = acteur de la session, `content:create` dans la portée | Naît obligatoirement `pending_upload` ; échéance de réservation future, 24 h au plus ; **réservation du quota** |
| `pending_upload` → `quarantined` | Le déposant uniquement | Réservation non expirée ; taille, SHA-256 et clé de quarantaine posées ; réservation convertie en occupation |
| `quarantined` / `scan_failed` / `infected` → `scanning` | `media:scan` | Tentative suivante obligatoire (`scan_attempt + 1`) |
| `scanning` → `clean` | `media:scan` | Dernier verdict = `clean`, de **la même tentative**, sur la **même empreinte**, signatures de moins de 48 h ; clé saine posée à ce moment seulement |
| `scanning` → `infected` / `scan_failed` | `media:scan` | Dernier verdict `infected`, ou `error` / `timeout` |
| `clean` → `infected` | `media:scan` | Verdict `infected` d'une nouvelle analyse ; `access_epoch + 1` |
| tout état reçu → `revoked` | `content:review` plateforme ou `user:manage` | Motif obligatoire ; `access_epoch + 1` ; état définitif |
| toute autre transition | — | **Refusée** (`nl:transition_forbidden`). Un média `clean` ne quitte cet état que par détection ou révocation |

**Aucun acteur applicatif ne peut attribuer `clean`.**
- `media:scan` n'est attribuable qu'au rôle de service `media_scanner`, qui n'existe pas encore : un
  déclencheur sur `role_permissions` l'impose. Le seed ne la donne plus à `admin`.
- Résultat en 2.2.3-a : **aucun média ne peut devenir diffusable**, ni par l'administrateur, ni par
  un relecteur, ni par l'auteur, ni sans acteur.
- Ni la clé saine, ni la date de validation, ni le journal ne sont modifiables directement.
- Le passage à `clean` est démontré **seulement** dans des bases de test jetables, où un rôle
  `media_scanner` de test est créé. Il préfigure l'identité de service de 2.2.3-c.

**Immuables.**
- Identité, portée, déposant, type, MIME, taille déclarée, nom affiché, licence, texte alternatif et
  cible des sous-titres.
- Après réception : taille, SHA-256 et clé de quarantaine.
- `access_epoch` ne décroît jamais. La suppression logique est irréversible. Un média purgé ne change
  plus.

## 4. Quotas (D17) et invariants

**Unités binaires.** Elles sont confirmées et identiques dans les fonctions, les messages d'erreur
(en octets), les tests (`test_binary_quota_units`) et la documentation :

| Portée | Quota | Octets |
|---|---|---|
| Utilisateur | 1 Gio | 1 073 741 824 |
| Établissement | 5 Gio | 5 368 709 120 |
| Plateforme | 20 Gio | 21 474 836 480 |

Tailles maximales par type (D15) : image 5 Mio (5 242 880 octets), PDF 20 Mio, VTT 1 Mio, MP4 200 Mio.

| Exigence | Mise en œuvre |
|---|---|
| Portées | Un média compte dans **deux** portées : utilisateur, et établissement ou plateforme |
| Calcul | **Occupé** = tailles reçues des médias non purgés. **Réservé** = tailles déclarées des réservations non supprimées et non échues. Les deux sont calculés à la demande à partir de `media_assets` |
| Réservation atomique | Dans le déclencheur de création du média, même transaction : verrou de ligne `FOR UPDATE` sur le quota de la portée, **puis** calcul de l'occupation. Le nouvel instantané, pris après le verrou en READ COMMITTED, voit les réservations validées entre-temps |
| Concurrence | Les demandes d'une même portée sont sérialisées par le verrou. Ordre constant (utilisateur, puis établissement ou plateforme) : pas d'interblocage |
| Aucun dépassement | `nl:quota_exceeded`, avec un message chiffré en octets |
| Aucun négatif | Par construction : aucune colonne de compteur |
| Expiration explicite | `reservation_expires_at`, 24 h au plus. Une réservation échue cesse de compter. `nl_media_expire_reservations()` la marque explicitement (`reservation_expired`) pour la traçabilité. Impossible d'expirer une réservation non échue ou de recevoir un fichier après expiration |
| Libération contrôlée | Uniquement par des transitions contrôlées : réception (déclaré → reçu, ≤ déclaré), abandon (`upload_aborted`), expiration, purge. Il n'existe aucune autre écriture possible |
| Lignes de quota non falsifiables | Création au quota par défaut **seulement** ; portée immuable ; suppression et `TRUNCATE` refusés ; modification du quota réservée à `user:manage` plateforme, jamais sous l'occupation (`nl:quota_below_usage`), **auditée** (`media.quota_changed` : acteur, ancien et nouveau quota) |
| Conservation légale | Pose et retrait réservés à `user:manage`, audités (`media.legal_hold_set` / `released`) |

### 4.1 Audit `pg_trigger_depth()` et correction

**Constat sur la première version (`f26360f`).** Elle stockait `used_bytes` et `reserved_bytes` et
n'autorisait leur modification que si `pg_trigger_depth() >= 2`, c'est-à-dire depuis un déclencheur.
Ce n'est **pas** une preuve d'autorisation. Le contournement a été **reproduit** dans une transaction
annulée, avec les seuls droits d'un rôle applicatif ordinaire :
- le privilège `TEMP`, accordé à `PUBLIC` par défaut ;
- `UPDATE` sur la table.

Le scénario :
1. Créer une table temporaire et une fonction `pg_temp`.
2. Poser un déclencheur `AFTER INSERT` qui exécute `UPDATE media_usage SET reserved_bytes = 0`.
3. Insérer une ligne : le déclencheur s'exécute à la profondeur 2, la garde l'accepte, et la
   réservation est remise à zéro (quota contournable).

**Correction (`0007` révisée avant tout push).**
- **Aucun compteur stocké :** l'occupation est dérivée des médias. Il n'y a plus rien à modifier
  dans `media_usage`, hormis le quota, protégé par l'acteur et la permission.
- **Plus aucune autorisation fondée sur `pg_trigger_depth()`** ni sur un drapeau de session.
- **Pas de `SECURITY DEFINER`** : aucune élévation de privilège.
- **Chaque écriture qui influence l'occupation est contrôlée** par la garde de `media_assets`, avec
  l'acteur déclaré, les permissions et les transitions : création, réception, suppression, purge.

**Test adversarial reproductible** (`test_adversarial_nested_triggers_cannot_bypass_quota_or_clean`).
Il rejoue le même schéma (table temporaire, fonction `pg_temp`, déclencheur imbriqué,
`ROLLBACK`). Sont refusés :
- l'augmentation du quota ;
- la création d'une ligne de quota à 100 Gio ;
- la suppression d'une ligne de quota ;
- l'entrée en analyse ;
- la purge ;
- la modification de la taille reçue ;
- l'attribution de `media:scan` à `instructor`.

L'appel direct de `nl_media_reserve` avec une taille négative n'écrit rien et ne libère rien.
L'occupation, l'état du média et le nombre de lignes de quota sont **identiques** avant et après.

**Concurrence et libération** (`test_concurrent_reservations_never_exceed_the_quota_then_release`).
- 8 réservations parallèles d'1 Mio face à un quota d'établissement de 3 Mio + 1 octet : **exactement
  3** acceptées.
- Après un abandon, 4 nouvelles réservations parallèles : **exactement 1** acceptée.
- L'occupation ne dépasse jamais le quota.

**Limites qui restent dépendantes de D14** (rôles PostgreSQL séparés, lot 2.5). Le rôle applicatif
actuel est aussi **propriétaire** du schéma : il pourrait désactiver un déclencheur
(`ALTER TABLE … DISABLE TRIGGER`), modifier une fonction ou insérer `media:scan` après avoir retiré
la garde. Aucune protection au niveau des lignes ne résiste au propriétaire : seule la séparation
« propriétaire de migration » / « rôle d'exécution sans DDL » le règle. Elle est requise avant la
production. Dans l'intervalle, aucune route applicative n'exécute de DDL.

## 5. Intégrité et conservation

| Garantie | Mise en œuvre |
|---|---|
| Un média référencé par une version protégée n'est jamais supprimé | Suppression logique refusée tant qu'un rattachement existe (`nl:in_use`) ; les rattachements d'une version soumise, approuvée, publiée ou archivée sont immuables (`nl:not_draft`) |
| `legal_hold` interdit suppression et purge | `nl:legal_hold` ; pose et retrait réservés à `user:manage`, audités |
| Un média lié à un signalement n'est ni supprimé ni purgé | `nl:reported` : signalement sur une version qui le cite |
| Références cohérentes | Clés étrangères ; sous-titres → vidéo du même établissement, non supprimée ; rattachement d'un média reçu, non infecté, non révoqué, non supprimé |
| Soumission, approbation et publication | Refusées si un média rattaché n'est pas `clean` (`nl:media_not_clean`) ou si une vidéo n'a pas de sous-titres sains (`nl:missing_captions`, WCAG 1.2.2). Codes ajoutés à l'API éditoriale |
| Historique des analyses conservé | `media_scan_events` en ajout seul (UPDATE, DELETE et TRUNCATE refusés), avec `RESTRICT` vers le média |
| Éditions publiées jamais modifiées implicitement | Aucune fonction de 0005 ou 0006 réécrite ; rattachements figés hors brouillon |
| Isolation | Média d'un établissement rattachable seulement à son établissement ; média public partout ; création dans un établissement réservée à ses membres `content:create` (`nl:organization_scope`, `nl:forbidden`) |
| Aucune cascade destructrice | Suppression physique d'un média refusée (`nl:history`) et `TRUNCATE` refusé. La seule `CASCADE` retire les rattachements d'un **brouillon** supprimé ; le média et ses analyses restent |

**Installations existantes.** La migration retire toute attribution de `media:scan` à un autre rôle
que `media_scanner`, **avant** de poser la garde : c'est le cas d'une permission créée hors migration.
Le test `test_existing_installation_loses_unauthorized_media_scan_grants` part d'une base 0006 où
`admin` et `medical_reviewer` la détiennent. Après 0007 et deux applications des données de
référence, plus personne ne la détient, et toute nouvelle attribution est refusée.

`0001_reference_data.sql` est un fichier de **données de référence** (`database/seeds/`, appliqué
par `python -m app.seed`, idempotent), et **non une migration** (`database/schema/`, Alembic). La
seule modification qu'il reçoit est l'exclusion de `media:scan` pour `admin`. Les migrations 0001 à
0006 sont inchangées (`git diff origin/main -- database/schema/000[1-6]*` vide).

**Retour arrière.** `0007_secure_media_foundation_down.sql` refuse (`nl:downgrade_unsafe`) dès qu'il
existe un média, un verdict, un rattachement, une ligne de quota, ou un rôle détenant `media:scan`.
Sinon, il restaure exactement le schéma 0006.

## 6. Tests

Environnement local réel : PostgreSQL 16.15 + pgvector 0.8.0, Redis 7.0.15.

| Suite | Résultat |
|---|---|
| `database/tests/test_media_foundation.py` (nouveau) | **23 réussis** |
| `database/tests` (toutes migrations 0001 → 0007) | **145 réussis** |
| `services/api/tests` (API, unitaires, chaîne Alembic 0001 → 0007 sur PostgreSQL) | **130 réussis, 1 ignoré** (Mailpit hors Docker) |
| Rejeu local du job CI Alembic | `upgrade head` → `verify_head` (75 tables) → seeds ×2 (aucun rôle `media:scan`) → `downgrade base` → `verify_base` → `upgrade head` → `verify_head` : OK |
| Ruff (dépôt) et mypy (45 fichiers) | OK |

Couverture des 15 points demandés :

| # | Point | Tests |
|---|---|---|
| 1–2 | Tables, clés étrangères | `test_tables_foreign_keys_and_no_scanner_role`, `test_constraints_on_formats_sizes_and_accessibility` |
| 3 | Contraintes | `test_constraints_on_formats_sizes_and_accessibility` : tailles D15 à l'octet près, MIME, texte alternatif, sous-titres, nom de fichier |
| 4 | Déclencheurs | `test_creation_requires_session_actor_and_permission`, garde `role_permissions` |
| 5 | Transitions | `test_transitions_allowed_and_forbidden` |
| 6 | Forçage de `clean` | `test_clean_cannot_be_forced_in_the_shipped_schema`, `test_clean_only_from_a_traceable_fresh_verdict` |
| 7 | Réservations concurrentes | `test_concurrent_reservations_never_exceed_the_quota_then_release` : 8 en parallèle, quota de 3 → exactement 3 ; après libération, exactement 1 ; `test_adversarial_nested_triggers_cannot_bypass_quota_or_clean` |
| 8 | Dépassements | `test_quota_exceeded_without_overshoot` (quota utilisateur réel de 1 Gio) |
| 9 | Expiration et libération | `test_reservation_expiry_abort_and_controlled_release`, `test_reservation_and_reception_accounting`, `test_quota_rows_cannot_be_forged_and_quota_changes_are_audited`, `test_binary_quota_units` |
| 10 | Références protégées | `test_attachments_drafts_only_and_submission_requires_clean_media`, `test_protected_references_survive` |
| 11 | `legal_hold` | `test_legal_hold_blocks_deletion_and_purge` |
| 12 | Signalements | `test_reported_media_cannot_be_deleted_or_purged` |
| 13 | Isolation | `test_establishment_isolation` |
| 14 | Historique | `test_history_is_append_only_and_kept` |
| 15 | Compatibilité historique | `test_upgrade_from_0006_with_historical_data_then_safe_downgrade`, `test_empty_database_up_down_up_is_exact`, `test_downgrade_refused_when_a_role_holds_media_scan`, `test_existing_installation_loses_unauthorized_media_scan_grants` |

**Ajustements de tests existants**, justifiés par les nouvelles règles et sans en masquer aucun :
- `test_role_permission_matrix` : l'administrateur a toutes les permissions **sauf** `media:scan`.
- `test_revision_chain_is_linear` : la tête est désormais `0007_secure_media_foundation`.
- Chaînes Alembic : 0007 est ajoutée aux chaînes testées.

**Scripts de validation.**
- Ceux de 2.2.2-a, b et c acceptent désormais une migration ≥ 0006 et au moins 71 tables. Leur
  vérification de branche accepte aussi `phase-2.2.3-*`.
- `db-apply.ps1` applique aussi 0007.

**Validation Windows.** `scripts\validate-phase2.2.3a.ps1` vérifie :
- l'état Git ;
- que les migrations historiques sont inchangées ;
- la non-régression complète, en enchaînant 2.2.2-c ;
- pytest ;
- la migration en tête (0007) et les 75 tables ;
- le schéma de tête (`verify_head.sql`) ;
- qu'aucun rôle ne détient `media:scan` et qu'aucun média n'est `clean` ;
- des sondes d'invariants **annulées**, sans écriture ;
- les quotas 1, 5 et 20 Gio ;
- qu'aucun programme médical n'est publié et que le pilote PAB est en brouillon ;
- que les services Docker sont sains.

Résultat : à exécuter sous Windows sur le commit corrigé (voir § 9).

## 7. Limites

- Aucun point d'accès : l'API ne crée ni ne lit encore de média (2.2.3-b et d).
- `media:scan` n'est attribuable qu'à un rôle `media_scanner` qui n'existe pas encore (2.2.3-c).
  Aucun média ne peut donc être `clean` : c'est voulu.
- Les durées de conservation (D21) et les délais de purge sont appliqués par la tâche planifiée de
  2.2.3-d, pas par la base.
- Les garde-fous reposent sur des déclencheurs et sur l'acteur déclaré (`nl.actor_id`), comme pour
  0005 et 0006. Le cloisonnement par rôles PostgreSQL séparés (D14) reste prévu au lot 2.5.
- L'occupation est recalculée à chaque réservation (somme sur `media_assets`, indexée par déposant
  et par établissement). Le coût est à mesurer en 2.2.3-b ; une matérialisation sous verrou reste
  possible plus tard, sans revenir à des compteurs modifiables.
- Unités : les quotas et tailles sont en **Mio et Gio** (binaire), soit 5 Mio = 5 242 880 octets.

## 8. Risques

| Risque | Niveau | Traitement |
|---|---|---|
| Seed modifié (`admin` sans `media:scan`) appliqué à une base existante | Faible | La garde de 0007 refuse toute attribution à `admin` ; le seed est idempotent et testé deux fois (CI) |
| Contournement par le rôle propriétaire du schéma (DDL, désactivation des déclencheurs) | Moyen avant production | D14 (rôles séparés, lot 2.5) ; aucune route applicative n'exécute de DDL ; les contournements par déclencheur imbriqué ou table temporaire sont fermés (§ 4.1) |
| Charge de verrouillage sur la ligne « plateforme » et coût du calcul dérivé | Faible | Une ligne par portée ; transactions courtes ; à mesurer en 2.2.3-b |
| Évolution du modèle d'états en 2.2.3-c (worker réel) | Faible | Toute évolution passe par une migration additive (0008) |

## 9. Validation Windows et CI

À compléter après exécution de `scripts\validate-phase2.2.3a.ps1` sur le commit corrigé, puis
après la CI de la PR.
