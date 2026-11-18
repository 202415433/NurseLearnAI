# Référence de l'API publique — Phase 1.2

> Branche `phase-1.2` · documentation interactive : <http://localhost:28000/docs> (OpenAPI : `/openapi.json`)

L'API de la Phase 1.2 est **en lecture seule** et **publique** : aucune authentification (elle arrivera en Phase 2), aucune écriture. Toute méthode autre que `GET` renvoie `405`.

## Règles communes

| Sujet | Règle |
|---|---|
| Préfixe | `/api/v1` pour les données ; `/health` et `/ready` à la racine |
| Langue | `?lang=fr|en` ou en-tête `Accept-Language` (défaut `fr`, `q=0` ignoré). Réponse : en-tête `Content-Language` |
| Pagination | `limit` (1–100, défaut 50) et `offset` (0–10 000). Réponse : `{ "items": [...], "total": n, "limit": l, "offset": o }` |
| Validation | Paramètres stricts (expressions régulières, listes fermées, UUID). Paramètre invalide → `422` |
| Erreurs | Format RFC 9457 `application/problem+json` : `type`, `title`, `status`, `detail`, `instance`, `request_id` ; jamais de trace technique. `500` générique |
| Visibilité | Seuls les programmes et modules `status = 'published'` **et** sans `organization_id` sont visibles ; une leçon n'apparaît qu'avec une version publiée. Les brouillons, contenus en relecture et contenus d'établissement ne sont **jamais** exposés |
| Sécurité | CORS limité aux origines configurées et à `GET` ; `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` ; limite de débit Redis par client (`RATE_LIMIT_PER_MINUTE`, réponse `429` + `Retry-After`) ; `X-Request-ID` propagé |
| Journaux | JSON, une ligne par requête : méthode, chemin, statut, durée, identifiant de requête. Ni adresse IP en clair, ni données personnelles, ni secret |

## Points d'accès

### Santé

| Méthode | Chemin | Réponse |
|---|---|---|
| GET | `/health` | `200 {"status":"ok","version":"…"}` — le processus répond |
| GET | `/ready` | `200 {"status":"ready","checks":{"database":"ok","redis":"ok"}}` ; `503` (problem+json) si une dépendance est indisponible |
| GET | `/api/v1/about` | Nom, version et **mention obligatoire** : la formation n'est ni un diplôme, ni une licence, ni une autorisation d'exercer |

### Référentiels

| Chemin | Filtres | Élément renvoyé |
|---|---|---|
| `GET /api/v1/countries` | — | `{code, name, jurisdiction_count}` (pays ayant au moins une juridiction active) |
| `GET /api/v1/jurisdictions` | `country=CA` (2 lettres), `scope=all|framework` | `{code, name, kind, country_code, parent_code, default_locale}` ; tri pays → provinces → référentiels transnationaux |
| `GET /api/v1/professions` | `jurisdiction=CA-QC`, `category=care_assistant|practical_nurse|registered_nurse|other` | `{code, title, category, is_regulated, regulator_name, regulator_url, official_path, validation_status, jurisdiction_code, availability:{published, in_preparation}}` |
| `GET /api/v1/specialties` | `group=a…h`, `jurisdiction`, `profession` | `{code, group, name, published_programs}` ; les spécialités ayant des programmes publiés d'abord |

Un métier appartient à **une seule** juridiction : l'API ne déduit jamais d'équivalence entre métiers de juridictions différentes. `availability` ne donne que des **compteurs** : `in_preparation` signale des programmes non publiés sans rien en exposer.

### Catalogue

| Chemin | Filtres | Réponse |
|---|---|---|
| `GET /api/v1/programs` | `jurisdiction`, `profession`, `specialty`, `level=beginner|fundamental|intermediate|advanced|professional_prep` | Page de `{id, code, title, summary, level, access_tier, profession_code, profession_title, jurisdiction_code, specialty_code, specialty_name, module_count}` |
| `GET /api/v1/programs/{id}` | `id` UUID | Programme + `modules[]` publiés (`code, title, summary, level, estimated_minutes, position, is_required, lessons[]`) ; chaque leçon : `code, position, kind, estimated_minutes, title, locale, jurisdiction_specific, next_review_due`. `404` pour un programme inconnu, en brouillon ou privé |

Pour chaque leçon, la version publiée retenue est, dans l'ordre : langue demandée + juridiction du programme, langue demandée commune, langue de repli + juridiction, langue de repli commune.

## Exemples

```powershell
curl.exe -s "http://localhost:28000/api/v1/countries"
curl.exe -s "http://localhost:28000/api/v1/jurisdictions?country=CA"
curl.exe -s -H "Accept-Language: en" "http://localhost:28000/api/v1/professions?jurisdiction=CA-QC"
curl.exe -s "http://localhost:28000/api/v1/programs?jurisdiction=CA-QC&profession=pab"   # total 0 : pilote en brouillon
curl.exe -s "http://localhost:28000/api/v1/professions?jurisdiction=%3Cx%3E"           # 422 problem+json
```

## Évolution

Toute modification incompatible passera par `/api/v1` → `/api/v2`. Les points d'accès authentifiés (profil, progression, administration, relecture) sont prévus en Phase 2 (`docs/07-plan-phase-2.md`).
