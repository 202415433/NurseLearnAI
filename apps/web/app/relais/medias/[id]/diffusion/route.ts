// Diffusion apprenant (édition publiée, version de leçon citée), relayée vers media-delivery.
import type { NextRequest } from "next/server";

import { relayRead } from "@/lib/media-relay-handlers";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const query = request.nextUrl.searchParams;
  return relayRead(request, (await params).id, "content", {
    release: query.get("release"),
    lessonVersion: query.get("lesson_version"),
  });
}
