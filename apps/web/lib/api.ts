// Client de l'API NurseLearn (appelé côté serveur uniquement : aucune clé, aucun cookie exposé).
import type { Locale } from "./i18n";

const BASE_URL = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export type Jurisdiction = {
  code: string;
  name: string;
  kind: string;
  country_code: string | null;
  parent_code: string | null;
  default_locale: string;
};

export type Profession = {
  code: string;
  title: string;
  category: string;
  is_regulated: boolean;
  regulator_name: string | null;
  regulator_url: string | null;
  official_path: string | null;
  validation_status: string;
  jurisdiction_code: string;
};

export type ProgramSummary = {
  code: string;
  title: string;
  summary: string | null;
  level: string;
  access_tier: string;
  profession_code: string;
  jurisdiction_code: string;
  specialty_code: string | null;
};

async function get<T>(path: string, locale: Locale): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { "Accept-Language": locale, Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    throw new ApiError(503, "API injoignable");
  }
  if (!response.ok) {
    throw new ApiError(response.status, `API ${response.status} sur ${path}`);
  }
  return (await response.json()) as T;
}

const codePattern = /^[A-Za-z0-9_-]{1,80}$/;
function safe(code: string): string {
  if (!codePattern.test(code)) throw new ApiError(400, "Code invalide");
  return encodeURIComponent(code);
}

export const api = {
  jurisdictions: (locale: Locale) => get<Jurisdiction[]>("/api/v1/jurisdictions", locale),
  professions: (jurisdiction: string, locale: Locale) =>
    get<Profession[]>(`/api/v1/jurisdictions/${safe(jurisdiction)}/professions`, locale),
  programs: (filters: { jurisdiction: string; profession: string }, locale: Locale) =>
    get<ProgramSummary[]>(
      `/api/v1/programs?jurisdiction=${safe(filters.jurisdiction)}&profession=${safe(filters.profession)}`,
      locale,
    ),
};
