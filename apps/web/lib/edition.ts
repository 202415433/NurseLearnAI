// Accès serveur à l'API d'édition pour les écrans médias (2.2.3-e). Réservé au serveur : la session
// (cookie HttpOnly) part en Authorization vers l'API interne ; rien n'est exposé au navigateur.
import { BASE_URL, visitorAddress } from "./api";
import { sessionToken, type Me } from "./auth";
import type { Locale } from "./i18n";
import type { LessonMediaList, MediaItem, MediaKind, MediaState, Quotas } from "./media";

export type EditorialModule = {
  id: string;
  code: string;
  organization_id: string | null;
  title: Record<string, string>;
  status: string;
};
export type EditorialLesson = { id: string; module_id: string; code: string; position: number; kind: string };
export type LessonVersionSummary = { id: string; locale: string; version_no: number; status: string };
export type ModuleDetail = EditorialModule & { lessons: EditorialLesson[] };
export type LessonDetail = EditorialLesson & { versions: LessonVersionSummary[] };
export type LessonVersion = {
  id: string;
  lesson_id: string;
  module_id: string;
  organization_id: string | null;
  locale: string;
  version_no: number;
  status: string;
  title: string;
};
export type HoldEvent = {
  id: string;
  action: "set" | "released";
  reason: string;
  actor_name: string | null;
  created_at: string;
};

export type Fetched<T> = { ok: true; data: T; total?: number } | { ok: false; status: number };

const EDITORIAL_ROLES = ["admin", "org_admin", "instructor", "medical_reviewer"];

/** Accès à l'espace d'édition (affichage seulement ; l'API revérifie chaque droit dans sa portée). */
export function canEdit(me: Me | null): boolean {
  if (!me) return false;
  return (
    me.permissions.some((p) => p === "content:create" || p === "content:review" || p === "content:publish") ||
    me.roles.some((r) => EDITORIAL_ROLES.includes(r.role))
  );
}
/** Révocation : relecteurs plateforme et administration (règle de l'API, permissions globales). */
export function canRevoke(me: Me | null): boolean {
  return Boolean(me && (me.permissions.includes("content:review") || me.permissions.includes("user:manage")));
}
/** Conservation légale : permission dédiée media:legal_hold ET administration de la plateforme (0010). */
export function canHold(me: Me | null): boolean {
  return Boolean(me && me.permissions.includes("media:legal_hold") && me.permissions.includes("user:manage"));
}

async function get<T>(path: string, locale: Locale): Promise<Fetched<T>> {
  const token = await sessionToken();
  if (!token) return { ok: false, status: 401 };
  const visitor = await visitorAddress();
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/json",
        "Accept-Language": locale,
        ...(visitor ? { "X-Forwarded-For": visitor } : {}),
      },
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    return { ok: false, status: 503 };
  }
  if (!response.ok) return { ok: false, status: response.status };
  const total = response.headers.get("x-total-count");
  return { ok: true, data: (await response.json()) as T, total: total ? Number(total) : undefined };
}

export type LibraryQuery = {
  q?: string;
  kind?: MediaKind;
  state?: MediaState;
  includeDeleted?: boolean;
  limit: number;
  offset: number;
};

export function libraryPath(query: LibraryQuery): string {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.kind) params.set("kind", query.kind);
  if (query.state) params.set("state", query.state);
  params.set("include_deleted", query.includeDeleted ? "true" : "false");
  params.set("limit", String(query.limit));
  params.set("offset", String(query.offset));
  return `/api/v1/editorial/media?${params.toString()}`;
}

const enc = encodeURIComponent;

export const edition = {
  library: (q: LibraryQuery, l: Locale) => get<MediaItem[]>(libraryPath(q), l),
  media: (id: string, l: Locale) => get<MediaItem>(`/api/v1/editorial/media/${enc(id)}`, l),
  holdHistory: (id: string, l: Locale) => get<HoldEvent[]>(`/api/v1/editorial/media/${enc(id)}/legal-hold`, l),
  quotas: (l: Locale) => get<Quotas>("/api/v1/editorial/quotas/media", l),
  modules: (l: Locale) => get<EditorialModule[]>("/api/v1/editorial/modules", l),
  module: (id: string, l: Locale) => get<ModuleDetail>(`/api/v1/editorial/modules/${enc(id)}`, l),
  lesson: (id: string, l: Locale) => get<LessonDetail>(`/api/v1/editorial/lessons/${enc(id)}`, l),
  version: (id: string, l: Locale) => get<LessonVersion>(`/api/v1/editorial/lesson-versions/${enc(id)}`, l),
  versionMedia: (id: string, l: Locale) =>
    get<LessonMediaList>(`/api/v1/editorial/lesson-versions/${enc(id)}/media`, l),
};

/** Libellé d'un texte multilingue de l'API (langue demandée, sinon français, sinon première valeur). */
export function pick(text: Record<string, string> | null | undefined, locale: Locale): string {
  if (!text) return "";
  return text[locale] ?? text.fr ?? Object.values(text)[0] ?? "";
}
