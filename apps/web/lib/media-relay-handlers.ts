// Gestionnaires du relais média (route handlers Next.js). Réservé au serveur ; voir media-relay.ts.
import {
  API_URL,
  DELIVERY_URL,
  SESSION_COOKIE,
  UUID,
  downstreamReadHeaders,
  errorBody,
  jsonResponse,
  mutationAllowed,
  problemCode,
  readAllowed,
  relayedStatus,
  sessionFrom,
  upstreamReadHeaders,
  visitorFrom,
} from "./media-relay";

type Req = Request & { cookies?: { get(name: string): { value: string } | undefined } };

function tokenOf(request: Req): string | null {
  const fromJar = request.cookies?.get(SESSION_COOKIE)?.value;
  if (fromJar !== undefined) return sessionFrom(fromJar);
  const raw = request.headers.get("cookie") ?? "";
  const part = raw
    .split(";")
    .map((p) => p.trim())
    .find((p) => p.startsWith(`${SESSION_COOKIE}=`));
  return sessionFrom(part ? decodeURIComponent(part.slice(SESSION_COOKIE.length + 1)) : null);
}

/** Lecture d'un média (aperçu éditorial ou diffusion apprenant) en flux, Range compris. */
export async function relayRead(
  request: Req,
  id: string,
  target: "preview" | "content",
  query?: { release: string | null; lessonVersion: string | null },
): Promise<Response> {
  if (!readAllowed(request.headers)) return jsonResponse(403, errorBody(403, "cross_site"));
  const token = tokenOf(request);
  if (!token) return jsonResponse(401, errorBody(401, "unauthenticated"));
  if (!UUID.test(id)) return jsonResponse(404, errorBody(404, "not_found"));
  let path = `/api/v1/media/${id}/preview`;
  if (target === "content") {
    const { release, lessonVersion } = query ?? { release: null, lessonVersion: null };
    if (!release || !lessonVersion || !UUID.test(release) || !UUID.test(lessonVersion)) {
      return jsonResponse(404, errorBody(404, "not_found"));
    }
    path = `/api/v1/media/${id}/content?release=${release}&lesson_version=${lessonVersion}`;
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${DELIVERY_URL}${path}`, {
      method: "GET",
      headers: upstreamReadHeaders(request.headers, token, visitorFrom(request.headers)),
      cache: "no-store",
      redirect: "manual", // aucune redirection suivie (media-delivery n'en émet jamais)
      signal: request.signal, // le navigateur abandonne → le flux amont est coupé
    });
  } catch {
    return jsonResponse(503, errorBody(503, "media_unavailable"));
  }
  const status = relayedStatus(upstream.status);
  if (status >= 400 || status === 304) {
    let code: string | undefined;
    if (status !== 304) {
      try {
        code = problemCode(await upstream.json());
      } catch {
        code = undefined;
      }
    } else {
      await upstream.body?.cancel();
    }
    const headers = new Headers({ "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" });
    const retry = upstream.headers.get("retry-after");
    if (retry && /^\d{1,5}$/.test(retry)) headers.set("Retry-After", retry);
    const range = upstream.headers.get("content-range");
    if (status === 416 && range && /^bytes \*\/\d+$/.test(range)) headers.set("Content-Range", range);
    if (status === 304) return new Response(null, { status, headers });
    headers.set("Content-Type", "application/json; charset=utf-8");
    return new Response(errorBody(status, code), { status, headers });
  }
  return new Response(upstream.body, { status, headers: downstreamReadHeaders(upstream.headers) });
}

type ApiCall = { method: "GET" | "POST" | "PUT" | "DELETE"; path: string; body?: BodyInit | null; json?: boolean };

/** Appel de l'API d'édition (métadonnées, réservation, téléversement, abandon) ; JSON relayé filtré. */
export async function relayApi(request: Req, call: ApiCall, mutation: boolean): Promise<Response> {
  if (mutation ? !mutationAllowed(request.headers) : !readAllowed(request.headers)) {
    return jsonResponse(403, errorBody(403, "cross_site"));
  }
  const token = tokenOf(request);
  if (!token) return jsonResponse(401, errorBody(401, "unauthenticated"));
  const headers = new Headers({ Authorization: `Bearer ${token}`, Accept: "application/json" });
  const visitor = visitorFrom(request.headers);
  if (visitor) headers.set("X-Forwarded-For", visitor);
  const lang = request.headers.get("accept-language");
  if (lang && /^[A-Za-z,;=.\- 0-9]{1,100}$/.test(lang)) headers.set("Accept-Language", lang);
  const init: RequestInit & { duplex?: "half" } = {
    method: call.method,
    headers,
    cache: "no-store",
    redirect: "manual",
    signal: request.signal,
  };
  if (call.body !== undefined && call.body !== null) {
    init.body = call.body;
    if (call.json) headers.set("Content-Type", "application/json");
    else {
      headers.set("Content-Type", "application/octet-stream");
      const length = request.headers.get("content-length");
      if (length && /^\d{1,12}$/.test(length)) headers.set("Content-Length", length);
      init.duplex = "half"; // corps transmis en flux, jamais mis en mémoire
    }
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}${call.path}`, init);
  } catch {
    return jsonResponse(503, errorBody(503, "media_unavailable"));
  }
  if (upstream.status === 204)
    return new Response(null, { status: 204, headers: { "Cache-Control": "private, no-store" } });
  let data: unknown = null;
  try {
    data = await upstream.json();
  } catch {
    data = null;
  }
  if (!upstream.ok) {
    const status = [400, 401, 403, 404, 409, 413, 422, 428, 429, 503].includes(upstream.status) ? upstream.status : 502;
    return jsonResponse(status, errorBody(status, problemCode(data)));
  }
  return jsonResponse(upstream.status, JSON.stringify(data));
}
