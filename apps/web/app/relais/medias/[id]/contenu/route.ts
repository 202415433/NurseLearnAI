// Téléversement du contenu d'un média réservé : flux relayé vers l'API (quarantaine privée).
// Jamais d'envoi direct au stockage depuis le navigateur.
import type { NextRequest } from "next/server";

import { relayApi } from "@/lib/media-relay-handlers";
import { UUID, errorBody, jsonResponse } from "@/lib/media-relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX = 200 * 1048576; // plus grande limite par fichier (vidéo) ; l'API applique la limite réservée

export async function PUT(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const id = (await params).id;
  if (!UUID.test(id)) return jsonResponse(404, errorBody(404, "not_found"));
  const length = request.headers.get("content-length");
  if (!length || !/^\d{1,12}$/.test(length)) return jsonResponse(411, errorBody(411, "length_required"));
  if (Number(length) > MAX) return jsonResponse(413, errorBody(413, "media_too_large"));
  return relayApi(request, { method: "PUT", path: `/api/v1/editorial/media/${id}/content`, body: request.body }, true);
}
