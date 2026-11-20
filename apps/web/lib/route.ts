import { notFound } from "next/navigation";

import { ApiError, api, isValid, type Jurisdiction, type Profession } from "./api";
import { isLocale, type Locale } from "./i18n";

export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export async function localeFrom(params: Promise<{ locale: string }>): Promise<Locale> {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  return locale;
}

// Un paramètre de requête n'est accepté que sous forme de chaîne unique.
export function single(value: string | string[] | undefined): string | undefined {
  return typeof value === "string" ? value : undefined;
}

// 404 et 422 de l'API → page introuvable ; toute autre erreur remonte vers error.tsx.
export async function orNotFound<T>(promise: Promise<T>): Promise<T> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) notFound();
    throw error;
  }
}

// Résout la juridiction (et le métier) demandés ; toute combinaison inconnue donne une 404.
// Un métier n'est cherché que dans sa propre juridiction : aucune équivalence n'est déduite.
export async function resolvePath(
  locale: Locale,
  j: string | undefined,
  p?: string | undefined,
): Promise<{ jurisdiction: Jurisdiction; profession?: Profession; professions: Profession[] }> {
  if (!isValid("jurisdiction", j)) notFound();
  const [all, professions] = await Promise.all([
    orNotFound(api.jurisdictions({}, locale)),
    orNotFound(api.professions(j, locale)),
  ]);
  const jurisdiction = all.items.find((x) => x.code === j);
  if (!jurisdiction) notFound();
  if (p === undefined) return { jurisdiction, professions: professions.items };
  if (!isValid("code", p)) notFound();
  const profession = professions.items.find((x) => x.code === p);
  if (!profession) notFound();
  return { jurisdiction, profession, professions: professions.items };
}

// Codes d'état et jeton lus dans l'adresse (validés : aucune valeur arbitraire n'est réutilisée).
export function statusParams(query: Record<string, string | string[] | undefined>) {
  const code = (v: string | undefined) => (v && /^[a-zA-Z_]{1,40}$/.test(v) ? v : undefined);
  const token = single(query.jeton);
  return {
    status: code(single(query.etat)),
    error: code(single(query.erreur)),
    token: token && /^[A-Za-z0-9_-]{20,128}$/.test(token) ? token : undefined,
  };
}
