// Médias protégés (2.2.3-e) : types, limites et états partagés par le serveur et le navigateur.
// Aucune adresse de stockage n'existe côté Web : les octets passent EXCLUSIVEMENT par le relais
// /relais/medias/… (serveur Web) vers le service media-delivery, session vérifiée à chaque requête.

export const MEDIA_STATES = [
  "pending_upload",
  "quarantined",
  "scanning",
  "clean",
  "infected",
  "scan_failed",
  "revoked",
] as const;
export type MediaState = (typeof MEDIA_STATES)[number];

export const MEDIA_KINDS = ["image", "video", "captions", "document"] as const;
export type MediaKind = (typeof MEDIA_KINDS)[number];

export type MediaItem = {
  id: string;
  organization_id: string | null;
  uploaded_by: string;
  kind: MediaKind;
  mime_type: string;
  state: MediaState;
  declared_bytes: number;
  byte_size: number | null;
  display_name: string;
  license: string;
  attribution: string | null;
  alt_text: Record<string, string> | null;
  captions_for: string | null;
  deleted_at: string | null;
  legal_hold: boolean;
  access_epoch: number;
  created_at: string;
  updated_at: string;
  attachment_count?: number;
};

export type QuotaScope = {
  scope: "user" | "organization" | "platform";
  organization_id: string | null;
  used_bytes: number;
  reserved_bytes: number;
  quota_bytes: number;
};
export type Quotas = { scopes: QuotaScope[]; file_limits: Record<MediaKind, number> };

export type LessonMedia = {
  media_id: string;
  kind: MediaKind;
  mime_type: string;
  state: MediaState;
  display_name: string;
  byte_size: number | null;
  organization_id: string | null;
  alt_text: Record<string, string> | null;
  captions_for: string | null;
  captions_id: string | null;
  legal_hold: boolean;
  attached_at: string;
};
export type LessonMediaList = { lesson_version_id: string; status: string; editable: boolean; items: LessonMedia[] };

export const MIB = 1048576;
export const GIB = 1073741824;

// D15 — limites par fichier (identiques à l'API et à la base ; rappel indicatif côté navigateur).
export const FILE_LIMITS: Record<MediaKind, number> = {
  image: 5 * MIB,
  document: 20 * MIB,
  video: 200 * MIB,
  captions: 1 * MIB,
};
export const QUOTA_DEFAULTS = { user: 1 * GIB, organization: 5 * GIB, platform: 20 * GIB } as const;

export const MIME_KIND: Record<string, MediaKind> = {
  "image/jpeg": "image",
  "image/png": "image",
  "image/webp": "image",
  "application/pdf": "document",
  "video/mp4": "video",
  "text/vtt": "captions",
};
const EXTENSION_MIME: Record<string, string> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  webp: "image/webp",
  pdf: "application/pdf",
  mp4: "video/mp4",
  vtt: "text/vtt",
};
export const ACCEPT =
  ".jpg,.jpeg,.png,.webp,.pdf,.mp4,.vtt,image/jpeg,image/png,image/webp,application/pdf,video/mp4,text/vtt";

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (v: unknown): v is string => typeof v === "string" && UUID.test(v);
export const isMediaState = (v: unknown): v is MediaState =>
  typeof v === "string" && (MEDIA_STATES as readonly string[]).includes(v);
export const isMediaKind = (v: unknown): v is MediaKind =>
  typeof v === "string" && (MEDIA_KINDS as readonly string[]).includes(v);

/** Seul un média sain, ni supprimé, est présenté comme disponible ; tout autre état est bloqué ou en attente. */
export function isAvailable(m: Pick<MediaItem, "state" | "deleted_at">): boolean {
  return m.state === "clean" && !m.deleted_at;
}

/** États terminaux bloquants (jamais diffusés, quelle que soit la conservation légale). */
export function isBlocked(state: MediaState): boolean {
  return state === "infected" || state === "scan_failed" || state === "revoked";
}

/** États où l'analyse antivirus n'est pas terminée (suivi périodique dans l'interface). */
export function isAwaitingScan(state: MediaState): boolean {
  return state === "quarantined" || state === "scanning";
}

export type StateTone = "success" | "warning" | "danger" | "neutral";
export function stateTone(state: MediaState): StateTone {
  if (state === "clean") return "success";
  if (isBlocked(state)) return "danger";
  if (isAwaitingScan(state)) return "warning";
  return "neutral";
}

export function formatBytes(bytes: number | null | undefined, locale: string): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "—";
  const fmt = (n: number, unit: string) =>
    `${new Intl.NumberFormat(locale === "fr" ? "fr-CA" : "en-CA", { maximumFractionDigits: n < 10 ? 1 : 0 }).format(n)} ${unit}`;
  const units = locale === "fr" ? ["o", "Kio", "Mio", "Gio"] : ["B", "KiB", "MiB", "GiB"];
  if (bytes < 1024) return fmt(bytes, units[0]);
  if (bytes < MIB) return fmt(bytes / 1024, units[1]);
  if (bytes < GIB) return fmt(bytes / MIB, units[2]);
  return fmt(bytes / GIB, units[3]);
}

export type FileCheck =
  | { ok: true; kind: MediaKind; mime: string }
  | { ok: false; code: "unsupported" | "too_large" | "empty"; kind?: MediaKind; limit?: number };

/**
 * Validation INDICATIVE avant envoi (aide à l'utilisateur) : type déduit du type MIME annoncé ou de
 * l'extension, taille comparée à la limite du type. L'API revérifie tout (signature réelle des octets,
 * normalisation, ClamAV) : ce contrôle ne remplace aucune vérification serveur.
 */
export function checkFile(file: { name: string; type: string; size: number }): FileCheck {
  const ext = file.name.toLowerCase().split(".").pop() ?? "";
  const mime = MIME_KIND[file.type] ? file.type : EXTENSION_MIME[ext];
  const kind = mime ? MIME_KIND[mime] : undefined;
  if (!mime || !kind) return { ok: false, code: "unsupported" };
  if (file.size <= 0) return { ok: false, code: "empty", kind };
  if (file.size > FILE_LIMITS[kind]) return { ok: false, code: "too_large", kind, limit: FILE_LIMITS[kind] };
  return { ok: true, kind, mime };
}

/** Nom affiché proposé : nom du fichier sans chemin ni caractère de contrôle (≤ 200 caractères). */
export function suggestedName(fileName: string): string {
  const base = fileName.split(/[\\/]/).pop() ?? "";
  const clean = base.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  return (clean || "media").slice(0, 200);
}

// Adresses du relais Web (même origine). Jamais d'adresse de stockage, jamais de jeton dans l'URL.
export const relay = {
  preview: (id: string) => `/relais/medias/${encodeURIComponent(id)}/apercu`,
  content: (id: string, release: string, lessonVersion: string) =>
    `/relais/medias/${encodeURIComponent(id)}/diffusion?release=${encodeURIComponent(release)}&lesson_version=${encodeURIComponent(lessonVersion)}`,
  metadata: (id: string) => `/relais/medias/${encodeURIComponent(id)}`,
  upload: (id: string) => `/relais/medias/${encodeURIComponent(id)}/contenu`,
  reserve: () => "/relais/medias",
};
