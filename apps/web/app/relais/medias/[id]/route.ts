// Métadonnées d'un média (suivi de l'analyse antivirus) et abandon d'un téléversement.
import type { NextRequest } from "next/server";

import { relayApi } from "@/lib/media-relay-handlers";
import { UUID, errorBody, jsonResponse } from "@/lib/media-relay";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const id = (await params).id;
  if (!UUID.test(id)) return jsonResponse(404, errorBody(404, "not_found"));
  return relayApi(request, { method: "GET", path: `/api/v1/editorial/media/${id}` }, false);
}

export async function DELETE(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const id = (await params).id;
  if (!UUID.test(id)) return jsonResponse(404, errorBody(404, "not_found"));
  return relayApi(request, { method: "DELETE", path: `/api/v1/editorial/media/${id}` }, true);
}
