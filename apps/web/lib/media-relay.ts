// Relais Web → media-delivery / API pour les médias protégés (2.2.3-e). Module réservé au serveur.
//
// Garanties :
//  - destination FIXE (variables d'environnement du serveur) ; le client ne fournit qu'un identifiant
//    validé (UUID) : aucune adresse, aucun hôte, aucun chemin libre → pas de SSRF ;
//  - la session vient du cookie HttpOnly et part en en-tête Authorization vers le service interne ;
//    jamais dans une URL, jamais dans le JavaScript du navigateur, aucun jeton autonome ni stockage ;
//  - chaque requête (y compris chaque requête Range d'une vidéo) est revérifiée par media-delivery ;
//  - en-têtes transmis en liste blanche, dans les deux sens ; aucune mise en cache publique ;
//  - flux continu (aucune mise en mémoire du fichier) ; annulation propagée au service interne ;
//  - aucune journalisation des requêtes, des identifiants de session ni des contenus.

import { isSameOrigin } from "./csrf";

export const DELIVERY_URL = (process.env.MEDIA_DELIVERY_INTERNAL_URL ?? "http://localhost:8001").replace(/\/$/, "");
export const API_URL = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/$/, "");

const SECURE = (process.env.SESSION_COOKIE_SECURE ?? "true") !== "false";
export const SESSION_COOKIE = SECURE ? "__Host-nl_session" : "nl_session";

const SESSION_PATTERN = /^[A-Za-z0-9_-]{20,128}$/;
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** En-têtes du navigateur relayés vers media-delivery (lecture) : Range et validation seulement. */
const FORWARD_READ = ["range", "if-range"];
/** En-têtes de media-delivery renvoyés au navigateur (aucun Set-Cookie, aucun en-tête interne). */
const RETURN_READ = [
  "content-type",
  "content-length",
  "content-range",
  "accept-ranges",
  "content-disposition",
  "etag",
  "x-content-type-options",
  "content-security-policy",
  "cross-origin-resource-policy",
  "referrer-policy",
  "retry-after",
];
const RANGE_PATTERN = /^bytes=\d{0,15}-\d{0,15}$/;
const IF_RANGE_PATTERN = /^"[A-Za-z0-9-]{1,80}"$/;

export function sessionFrom(cookieValue: string | undefined | null): string | null {
  return cookieValue && SESSION_PATTERN.test(cookieValue) ? cookieValue : null;
}

/**
 * Lecture de média (balises img, video, track, téléchargement) : refus des requêtes intersites.
 * `same-origin` (sous-ressource de nos pages) et `none` (ouverture directe, téléchargement) sont admis ;
 * `same-site` et `cross-site` sont refusés, de même que tout navigateur annonçant une autre origine.
 */
export function readAllowed(headers: Headers): boolean {
  const site = headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return false;
  const origin = headers.get("origin");
  if (origin && origin !== "null") {
    const host = headers.get("x-forwarded-host") ?? headers.get("host");
    try {
      if (!host || new URL(origin).host !== host) return false;
    } catch {
      return false;
    }
  }
  return true;
}

/** Mutation (réservation, téléversement, abandon) : même origine exigée (protection CSRF). */
export function mutationAllowed(headers: Headers): boolean {
  return isSameOrigin(
    headers.get("origin"),
    headers.get("x-forwarded-host") ?? headers.get("host"),
    headers.get("sec-fetch-site"),
  );
}

/** En-têtes vers media-delivery : session, Range/If-Range valides seulement (une seule plage). */
export function upstreamReadHeaders(incoming: Headers, token: string, visitor?: string): Headers {
  const out = new Headers({ Authorization: `Bearer ${token}`, Accept: "*/*" });
  for (const name of FORWARD_READ) {
    const value = incoming.get(name);
    if (!value) continue;
    if (name === "range" && !RANGE_PATTERN.test(value.trim())) {
      // Plage mal formée ou multiple : transmise telle quelle pour que media-delivery réponde 416.
      out.set("Range", value.slice(0, 100));
      continue;
    }
    if (name === "if-range" && !IF_RANGE_PATTERN.test(value.trim())) continue;
    out.set(name === "range" ? "Range" : "If-Range", value.trim());
  }
  if (visitor) out.set("X-Forwarded-For", visitor);
  return out;
}

/** En-têtes renvoyés au navigateur : liste blanche + interdiction de toute mise en cache partagée. */
export function downstreamReadHeaders(upstream: Headers): Headers {
  const out = new Headers();
  for (const name of RETURN_READ) {
    const value = upstream.get(name);
    if (value) out.set(name, value);
  }
  out.set("Cache-Control", "private, no-store");
  out.set("Pragma", "no-cache");
  out.set("X-Content-Type-Options", "nosniff");
  out.set("Referrer-Policy", "no-referrer");
  out.set("Vary", "Cookie, Range");
  if (!out.has("content-security-policy")) out.set("Content-Security-Policy", "default-src 'none'; sandbox");
  return out;
}

/** Statuts relayés pour la lecture ; tout autre statut amont devient 502 (aucun détail interne). */
export function relayedStatus(status: number): number {
  return [200, 206, 304, 401, 403, 404, 409, 416, 422, 429, 503].includes(status) ? status : 502;
}

/** Corps d'erreur minimal (code fermé, sans détail interne ni identifiant). */
export function errorBody(status: number, code?: string): string {
  const safe = code && /^[a-z_]{2,40}$/.test(code) ? code : undefined;
  return JSON.stringify({ status, ...(safe ? { code: safe } : {}) });
}

export function jsonResponse(status: number, body: string): Response {
  return new Response(body, {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

/** Code d'erreur métier extrait d'un document problème amont (`code`, sinon fin du `type`). */
export function problemCode(data: unknown): string | undefined {
  if (!data || typeof data !== "object") return undefined;
  const d = data as { code?: unknown; type?: unknown };
  if (typeof d.code === "string") return d.code;
  if (typeof d.type === "string") return d.type.split("/").pop()?.replace(/-/g, "_");
  return undefined;
}

/** Adresse du visiteur transmise aux services (limitation de débit), comme lib/api.ts. */
export function visitorFrom(headers: Headers): string | undefined {
  const candidate = headers.get("x-forwarded-for")?.split(",").at(-1)?.trim() || headers.get("x-real-ip")?.trim();
  return candidate && /^[0-9A-Fa-f:.]{2,45}$/.test(candidate) ? candidate : undefined;
}
