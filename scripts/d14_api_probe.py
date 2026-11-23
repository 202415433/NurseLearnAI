"""Sonde D14 exécutée DANS le conteneur de l'API, avec SA connexion (DATABASE_URL = nl_api).

Usage : docker compose cp scripts/d14_api_probe.py api:/tmp/ && docker compose exec -T api python /tmp/d14_api_probe.py
Chaque opération privilégiée est tentée dans une transaction ANNULÉE ; toute acceptation est un échec.
Aucune donnée n'est modifiée. Sortie : une ligne JSON {"user": ..., "refused": n, "accepted": [...]}.
"""

import asyncio
import json
import os

import asyncpg

ATTEMPTS = {
    "desactiver_declencheur": "ALTER TABLE media_assets DISABLE TRIGGER USER",
    "replication_role": "SET LOCAL session_replication_role = replica",
    "remplacer_fonction": "CREATE OR REPLACE FUNCTION nl_actor() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid'",
    "modifier_verdict": "UPDATE media_scan_events SET result = 'clean'",
    "forcer_clean": "UPDATE media_assets SET clean_key = 'x/y', scan_attempt = scan_attempt",
    "modifier_quota": "UPDATE media_usage SET quota_bytes = quota_bytes",
    "effacer_audit": "DELETE FROM audit_logs WHERE false",
    "vider_audit": "TRUNCATE audit_logs",
    "attribuer_media_scan": "INSERT INTO role_permissions (role_id, permission_code) "
    "SELECT id, 'media:scan' FROM roles WHERE code = 'admin'",
    "contournement_role_grant": "SELECT set_config('nl.role_grant_bypass', 'on', true); "
    "INSERT INTO user_roles (user_id, role_id) SELECT gen_random_uuid(), id FROM roles WHERE code = 'admin'",
    "decision_editoriale_forgee": "SELECT set_config('nl.editorial_log', 'on', true); INSERT INTO editorial_decisions "
    "(entity_type, entity_id, from_status, to_status, decision, actor_id) VALUES ('lesson_version', gen_random_uuid(), "
    "'draft', 'medical_review', 'submitted', gen_random_uuid())",
    "devenir_proprietaire": "ALTER TABLE users OWNER TO nl_api",
    "set_role_proprietaire": "SET ROLE nl_owner",
    "changer_identite": "SET SESSION AUTHORIZATION nl_media_scanner",
    "creer_table": "CREATE TABLE intrus (a int)",
    "creer_table_temporaire": "CREATE TEMP TABLE intrus (a int)",
    "creer_declencheur": "CREATE TRIGGER intrus BEFORE INSERT ON audit_logs "
    "FOR EACH ROW EXECUTE FUNCTION nl_set_updated_at()",
    "fonction_proprietaire": "SELECT nl_enforce_ownership()",
    "fonction_de_service": "SELECT nl_media_scanner_identity()",
    "programme_externe": "COPY (SELECT 1) TO PROGRAM 'id'",
}


async def main() -> None:
    conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg", "postgresql"))
    user = await conn.fetchval("SELECT current_user")
    accepted: list[str] = []
    refused = 0
    for name, sql in ATTEMPTS.items():
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(sql)
            # Déclencheurs de contrainte différés éventuels : vérifiés avant l'annulation.
            await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
            accepted.append(name)
        except asyncpg.PostgresError:
            refused += 1
        finally:
            await tr.rollback()
    await conn.close()
    print(json.dumps({"user": user, "refused": refused, "total": len(ATTEMPTS), "accepted": accepted}))


asyncio.run(main())
