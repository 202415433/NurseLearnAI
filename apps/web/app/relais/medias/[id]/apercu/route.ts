// Aperçu éditorial d'un média sain, relayé vers media-delivery (session revérifiée à chaque requête).
import type { NextRequest } from "next/server";

import { relayRead } from "@/lib/media-relay-handlers";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  return relayRead(request, (await params).id, "preview");
}
