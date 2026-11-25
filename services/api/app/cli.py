"""Commandes d'exploitation locales (jamais exposées par HTTP).

    python -m app.cli grant-platform-admin <courriel>
    python -m app.cli create-organization <slug> "<nom>" [--kind school|hospital|care_home|employer|other]
    python -m app.cli media-legal-hold <média> --by <courriel admin> --reason "<motif>" [--release]

`media-legal-hold` (2.2.3-d, S13) pose ou lève une obligation de conservation légale sur un média.
L'administrateur désigné (permission user:manage) est l'acteur : la base vérifie son droit et
journalise `media.legal_hold_set` / `media.legal_hold_released`. Un média sous conservation ne peut
être ni supprimé ni purgé ; la conservation n'autorise AUCUNE diffusion supplémentaire.

La première commande est le seul moyen d'obtenir le rôle d'administrateur de la plateforme
(ADR-0007) : elle contourne explicitement la garde d'attribution des rôles, dans une transaction,
et journalise l'opération dans audit_logs.
"""

from __future__ import annotations

import argparse
import json
import sys

import psycopg

from app.core.config import get_settings


def _conn(url: str | None = None) -> psycopg.Connection:
    url = (url or get_settings().sync_database_url).replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(url)


def grant_platform_admin(email: str) -> int:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM users WHERE email = %s AND deleted_at IS NULL AND status = 'active'", (email.lower(),)
        )
        row = cur.fetchone()
        if row is None:
            print("Compte introuvable ou non vérifié : créez-le et vérifiez son courriel d'abord.", file=sys.stderr)
            return 1
        cur.execute("SELECT set_config('nl.role_grant_bypass', 'on', true)")
        cur.execute(
            "INSERT INTO user_roles (user_id, role_id) SELECT %s, id FROM roles WHERE code = 'admin' "
            "ON CONFLICT DO NOTHING RETURNING id",
            (row[0],),
        )
        created = cur.fetchone() is not None
        cur.execute(
            "INSERT INTO audit_logs (actor_user_id, action, entity_type, entity_id, metadata) "
            "VALUES (NULL, 'role.granted', 'user', %s, %s::jsonb)",
            (str(row[0]), json.dumps({"role": "admin", "via": "cli"})),
        )
        # Les sessions existantes sont fermées : la personne se reconnecte avec ses nouveaux droits.
        cur.execute(
            "UPDATE auth_sessions SET revoked_at = now(), revoked_reason = 'admin' "
            "WHERE user_id = %s AND revoked_at IS NULL",
            (row[0],),
        )
        conn.commit()
    print("Rôle administrateur attribué." if created else "Rôle administrateur déjà présent.")
    return 0


def create_organization(slug: str, name: str, kind: str) -> int:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (slug, name, kind) VALUES (%s, %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (slug, name, kind),
        )
        org_id = cur.fetchone()[0]  # type: ignore[index]
        cur.execute(
            "INSERT INTO audit_logs (action, entity_type, entity_id, metadata) "
            "VALUES ('organization.created', 'organization', %s, %s::jsonb)",
            (str(org_id), json.dumps({"slug": slug, "via": "cli"})),
        )
        conn.commit()
    print(org_id)
    return 0


def media_legal_hold(media_id: str, by: str, reason: str | None, release: bool, url: str | None = None) -> int:
    """Pose (défaut) ou lève (--release) une conservation légale ; justification obligatoire dans les
    deux cas. Passe par la fonction contrôlée nl_media_set_legal_hold (0010) : droit de l'acteur,
    journal en ajout seul (acteur, horodatage, motif) et audit, comme l'API d'administration."""
    if reason is None or not 5 <= len(reason.strip()) <= 1000:
        print('Justification obligatoire : --reason "<motif>" (5 à 1000 caractères).', file=sys.stderr)
        return 2
    with _conn(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s AND deleted_at IS NULL AND status = 'active'", (by.lower(),))
        actor = cur.fetchone()
        if actor is None:
            print("Administrateur introuvable ou inactif.", file=sys.stderr)
            return 1
        cur.execute("SELECT set_config('nl.actor_id', %s, true)", (str(actor[0]),))
        try:
            cur.execute("SELECT nl_media_set_legal_hold(%s, %s, %s)", (media_id, not release, reason))
        except psycopg.Error as exc:
            conn.rollback()
            print(f"Refusé par la base : {exc.diag.message_primary}", file=sys.stderr)
            return 1
        conn.commit()
    print("Conservation légale levée." if release else "Conservation légale posée.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("grant-platform-admin")
    admin.add_argument("email")
    org = sub.add_parser("create-organization")
    org.add_argument("slug")
    org.add_argument("name")
    org.add_argument("--kind", default="school", choices=["school", "hospital", "care_home", "employer", "other"])
    hold = sub.add_parser("media-legal-hold")
    hold.add_argument("media_id")
    hold.add_argument("--by", required=True, help="courriel de l'administrateur (user:manage)")
    hold.add_argument("--reason", help="motif (5 à 1000 caractères)")
    hold.add_argument("--release", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "grant-platform-admin":
        return grant_platform_admin(args.email)
    if args.command == "media-legal-hold":
        return media_legal_hold(args.media_id, args.by, args.reason, args.release)
    return create_organization(args.slug, args.name, args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
