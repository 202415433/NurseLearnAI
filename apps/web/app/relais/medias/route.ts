// Réservation d'un téléversement (quota réservé atomiquement par l'API). Corps JSON limité et revalidé.
import type { NextRequest } from "next/server";

import { relayApi } from "@/lib/media-relay-handlers";
import { errorBody, jsonResponse } from "@/lib/media-relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED = [
  "kind",
  "mime_type",
  "size_bytes",
  "display_name",
  "license",
  "attribution",
  "alt_text",
  "captions_for",
  "organization_id",
];

export async function POST(request: NextRequest) {
  const length = Number(request.headers.get("content-length") ?? "0");
  if (!Number.isFinite(length) || length > 16384) return jsonResponse(413, errorBody(413, "too_large"));
  let body: unknown;
  try {
    const text = await request.text();
    if (text.length > 16384) return jsonResponse(413, errorBody(413, "too_large"));
    body = JSON.parse(text);
  } catch {
    return jsonResponse(422, errorBody(422, "invalid_input"));
  }
  if (!body || typeof body !== "object" || Array.isArray(body))
    return jsonResponse(422, errorBody(422, "invalid_input"));
  // Seuls les champs du contrat sont transmis (l'identité et la portée viennent de la session).
  const filtered = Object.fromEntries(Object.entries(body).filter(([k]) => ALLOWED.includes(k)));
  return relayApi(
    request,
    { method: "POST", path: "/api/v1/editorial/media", body: JSON.stringify(filtered), json: true },
    true,
  );
}
