// Client de l'API NurseLearn, appelé côté serveur uniquement (aucun secret, aucun cookie exposé).
// Toutes les données affichées viennent de l'API (donc de PostgreSQL) : rien n'est codé en dur ici.
import type { Locale } from "./i18n";

const BASE_URL = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

export type Country = { code: string; name: string; jurisdiction_count: number };

export type Jurisdiction = {
  code: string;
  name: string;
  kind: string;
  country_code: string | null;
  parent_code: string | null;
  default_locale: string;
};

export type Availability = { published: number; in_preparation: number };

export type Profession = {
  code: string;
  title: string;
  category: string;
  is_regulated: boolean;
  regulator_name: string | null;
  regulator_url: string | null;
  official_path: string | null;
  validation_status: "pending_expert_review" | "validated" | "needs_update";
  jurisdiction_code: string;
  availability: Availability;
};

export type Specialty = { code: string; group: string; name: string; published_programs: number };

export type ProgramSummary = {
  id: string;
  code: string;
  title: string;
  summary: string | null;
  level: string;
  access_tier: string;
  profession_code: string;
  profession_title: string;
  jurisdiction_code: string;
  specialty_code: string | null;
  specialty_name: string | null;
  module_count: number;
};

export type Lesson = {
  code: string;
  position: number;
  kind: string;
  estimated_minutes: number;
  title: string;
  locale: string;
  jurisdiction_specific: boolean;
  next_review_due: string | null;
};

export type ProgramModule = {
  code: string;
  title: string;
  summary: string | null;
  level: string;
  estimated_minutes: number;
  position: number;
  is_required: boolean;
  lessons: Lesson[];
};

export type ProgramDetail = ProgramSummary & { modules: ProgramModule[] };

// Validation stricte des paramètres provenant de l'URL avant tout appel.
export const patterns = {
  country: /^[A-Za-z]{2}$/,
  jurisdiction: /^[A-Za-z0-9-]{2,20}$/,
  code: /^[a-z0-9_]{1,60}$/,
  uuid: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
};

export function isValid(kind: keyof typeof patterns, value: string | undefined): value is string {
  return typeof value === "string" && patterns[kind].test(value);
}

// Adresse du visiteur transmise à l'API pour une limitation de débit par visiteur
// (l'API ne la lit que si ce serveur fait partie de ses mandataires approuvés : TRUSTED_PROXIES).
const IP_PATTERN = /^[0-9A-Fa-f:.]{2,45}$/;

async function visitorAddress(): Promise<string | undefined> {
  try {
    const { headers } = await import("next/headers");
    const h = await headers();
    const candidate = h.get("x-forwarded-for")?.split(",").at(-1)?.trim() || h.get("x-real-ip")?.trim();
    return candidate && IP_PATTERN.test(candidate) ? candidate : undefined;
  } catch {
    return undefined; // hors requête (tests, construction) : rien à transmettre
  }
}

async function get<T>(path: string, params: Record<string, string | number | undefined>, locale: Locale): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const url = `${BASE_URL}${path}${query.size ? `?${query.toString()}` : ""}`;
  let response: Response;
  try {
    const visitor = await visitorAddress();
    response = await fetch(url, {
      headers: {
        "Accept-Language": locale,
        Accept: "application/json",
        ...(visitor ? { "X-Forwarded-For": visitor } : {}),
      },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    throw new ApiError(503, "API injoignable");
  }
  if (!response.ok) throw new ApiError(response.status, `API ${response.status}`);
  return (await response.json()) as T;
}

export const api = {
  countries: (locale: Locale) => get<Page<Country>>("/api/v1/countries", { limit: 100 }, locale),
  jurisdictions: (filter: { country?: string; scope?: "framework" }, locale: Locale) =>
    get<Page<Jurisdiction>>("/api/v1/jurisdictions", { ...filter, limit: 100 }, locale),
  professions: (jurisdiction: string, locale: Locale) =>
    get<Page<Profession>>("/api/v1/professions", { jurisdiction, limit: 100 }, locale),
  specialties: (filter: { jurisdiction: string; profession: string }, locale: Locale) =>
    get<Page<Specialty>>("/api/v1/specialties", { ...filter, limit: 100 }, locale),
  programs: (
    filter: { jurisdiction: string; profession: string; specialty?: string; offset?: number; limit?: number },
    locale: Locale,
  ) => get<Page<ProgramSummary>>("/api/v1/programs", { limit: 20, ...filter }, locale),
  program: (id: string, locale: Locale) => get<ProgramDetail>(`/api/v1/programs/${encodeURIComponent(id)}`, {}, locale),
};
