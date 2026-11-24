"""Rapport de conservation des médias (2.2.3-d, plan § 3.9, S13) — LECTURE SEULE.

    docker compose exec -T api python -m app.ops.media_retention

Classe les médias selon la politique de conservation documentée et indique, pour chaque catégorie,
combien d'objets SERAIENT éligibles à une purge et combien en sont protégés (conservation légale,
signalement de contenu, rattachement à une version figée). AUCUNE suppression, AUCUNE purge, aucune
écriture : la purge automatique reste désactivée tant qu'aucune politique n'est explicitement
approuvée (durées légales par juridiction à confirmer). Les objets orphelins du stockage sont
signalés par la réconciliation du worker (2.2.3-c), jamais supprimés.

Sortie : une ligne JSON (nombres seulement : aucun identifiant, nom de fichier ni clé de stockage).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import psycopg

from app.core.config import get_settings

PROTECTED = """(m.legal_hold OR nl_media_is_reported(m.id) OR nl_media_is_attached(m.id))"""

# (catégorie, condition SQL statique) — durées techniques minimales du plan § 3.9.
CATEGORIES: list[tuple[str, str]] = [
    ("supprime_logiquement_plus_de_30_jours", "m.deleted_at IS NOT NULL AND m.deleted_at < now() - interval '30 days'"),
    ("infecte_plus_de_30_jours", "m.state = 'infected' AND m.updated_at < now() - interval '30 days'"),
    (
        "en_attente_ou_echec_orphelin_plus_de_7_jours",
        "m.state IN ('pending_upload', 'scan_failed') AND m.created_at < now() - interval '7 days'",
    ),
    ("revoque_decision_manuelle", "m.state = 'revoked'"),
]


def report(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"purge_automatique": "desactivee (aucune politique approuvee)", "categories": {}}
    with conn.cursor() as cur:
        for name, cond in CATEGORIES:
            cur.execute(
                "SELECT count(*) FILTER (WHERE NOT " + PROTECTED + "), "  # noqa: S608 - fragments statiques
                "count(*) FILTER (WHERE m.legal_hold), count(*) FILTER (WHERE nl_media_is_reported(m.id)), "
                "count(*) FILTER (WHERE nl_media_is_attached(m.id)) "
                "FROM media_assets m WHERE m.purged_at IS NULL AND " + cond
            )
            eligible, hold, reported, attached = cur.fetchone() or (0, 0, 0, 0)
            out["categories"][name] = {
                "eligibles_si_politique_approuvee": eligible,
                "proteges_conservation_legale": hold,
                "proteges_signalement": reported,
                "proteges_rattachement": attached,
            }
        cur.execute(
            "SELECT count(*) FILTER (WHERE legal_hold), count(*) FILTER (WHERE nl_media_is_protected(id)), "
            "count(*) FILTER (WHERE purged_at IS NOT NULL) FROM media_assets"
        )
        hold, frozen, purged = cur.fetchone() or (0, 0, 0)
        out["conservation_legale"] = hold
        out["rattaches_a_une_version_figee"] = frozen
        out["deja_purges"] = purged
    return out


def main() -> int:
    url = get_settings().sync_database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(url) as conn:
        conn.read_only = True
        print(json.dumps(report(conn), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
