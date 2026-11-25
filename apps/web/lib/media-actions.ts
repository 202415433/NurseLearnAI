"use server";

// Actions serveur des médias (2.2.3-e) : formulaires sans JavaScript client. Chaque action contrôle
// l'origine (CSRF), valide les identifiants, appelle l'API avec la session (cookie HttpOnly) et
// redirige avec un code pris dans une liste fermée (aucun texte de l'API réfléchi dans l'adresse).
import { redirect } from "next/navigation";

import { assertSameOrigin, callApi, sessionToken, setSessionCookie } from "./auth";
import { isLocale, type Locale } from "./i18n";
import { isUuid } from "./media";

const KNOWN = new Set([
  "reauth",
  "forbidden",
  "not_found",
  "not_draft",
  "media_scope",
  "media_not_clean",
  "reason_required",
  "no_change",
  "already_revoked",
  "transition_forbidden",
  "already_deleted",
  "too_many",
]);

function field(form: FormData, name: string, max = 1000): string {
  const value = form.get(name);
  return typeof value === "string" ? value.slice(0, max) : "";
}

function localeOf(form: FormData): Locale {
  const value = field(form, "locale", 5);
  return isLocale(value) ? value : "fr";
}

function codeOf(status: number, data: { code?: string; type?: string }): string {
  if (status === 429) return "too_many";
  const type = data.type ?? "";
  if (type.endsWith("reauthentication-required")) return "reauth";
  const code = data.code ?? type.split("/").pop()?.replace(/-/g, "_") ?? "";
  if (KNOWN.has(code)) return code;
  if (status === 404) return "not_found";
  if (status === 403) return "forbidden";
  return "generic";
}

/** Retour vers une page de l'espace d'édition seulement (aucune redirection ouverte). */
function back(locale: Locale, path: string, param: "etat" | "erreur", code: string): never {
  const safe =
    path.startsWith(`/${locale}/edition/`) && !path.includes("//") && !path.includes("\\")
      ? path
      : `/${locale}/edition/medias`;
  const [base] = safe.split("?");
  redirect(`${base}?${param}=${encodeURIComponent(code)}`);
}

async function prepare(form: FormData): Promise<{ locale: Locale; token: string | null; returnTo: string }> {
  await assertSameOrigin();
  const locale = localeOf(form);
  return { locale, token: await sessionToken(), returnTo: field(form, "retour", 300) };
}

export async function attachMediaAction(form: FormData): Promise<void> {
  const { locale, token, returnTo } = await prepare(form);
  const version = field(form, "version", 36);
  const media = field(form, "media", 36);
  if (!isUuid(version) || !isUuid(media)) back(locale, returnTo, "erreur", "not_found");
  const r = await callApi(`/api/v1/editorial/lesson-versions/${version}/media/${media}`, {
    method: "PUT",
    token,
    locale,
  });
  if (r.ok) back(locale, returnTo, "etat", "attached");
  back(locale, returnTo, "erreur", codeOf(r.status, r.data));
}

export async function detachMediaAction(form: FormData): Promise<void> {
  const { locale, token, returnTo } = await prepare(form);
  const version = field(form, "version", 36);
  const media = field(form, "media", 36);
  if (!isUuid(version) || !isUuid(media)) back(locale, returnTo, "erreur", "not_found");
  const r = await callApi(`/api/v1/editorial/lesson-versions/${version}/media/${media}`, {
    method: "DELETE",
    token,
    locale,
  });
  if (r.ok) back(locale, returnTo, "etat", "detached");
  back(locale, returnTo, "erreur", codeOf(r.status, r.data));
}

export async function revokeMediaAction(form: FormData): Promise<void> {
  const { locale, token, returnTo } = await prepare(form);
  const media = field(form, "media", 36);
  const reason = field(form, "reason").trim();
  if (!isUuid(media)) back(locale, returnTo, "erreur", "not_found");
  if (reason.length < 5) back(locale, returnTo, "erreur", "reason_required");
  const r = await callApi(`/api/v1/editorial/media/${media}/revoke`, { token, locale, body: { reason } });
  if (r.ok) back(locale, returnTo, "etat", "revoked");
  back(locale, returnTo, "erreur", codeOf(r.status, r.data));
}

export async function legalHoldAction(form: FormData): Promise<void> {
  const { locale, token, returnTo } = await prepare(form);
  const media = field(form, "media", 36);
  const reason = field(form, "reason").trim();
  const hold = field(form, "hold", 5) === "true";
  if (!isUuid(media)) back(locale, returnTo, "erreur", "not_found");
  if (reason.length < 5) back(locale, returnTo, "erreur", "reason_required");
  const r = await callApi(`/api/v1/editorial/media/${media}/legal-hold`, { token, locale, body: { hold, reason } });
  if (r.ok) back(locale, returnTo, "etat", hold ? "holdSet" : "holdReleased");
  back(locale, returnTo, "erreur", codeOf(r.status, r.data));
}

export async function deleteMediaAction(form: FormData): Promise<void> {
  const { locale, token, returnTo } = await prepare(form);
  const media = field(form, "media", 36);
  if (!isUuid(media)) back(locale, returnTo, "erreur", "not_found");
  const r = await callApi(`/api/v1/editorial/media/${media}`, { method: "DELETE", token, locale });
  if (r.ok) redirect(`/${locale}/edition/medias?etat=deleted`);
  back(locale, returnTo, "erreur", codeOf(r.status, r.data));
}

/** Confirmation du mot de passe (session sensible) puis retour à la page d'édition d'origine. */
export async function confirmPasswordAction(form: FormData): Promise<void> {
  const { locale, token, returnTo } = await prepare(form);
  const r = await callApi<{ session_token: string; expires_at: string }>("/api/v1/auth/reauthenticate", {
    token,
    body: { password: field(form, "password", 1024) },
  });
  if (r.ok) {
    await setSessionCookie(r.data.session_token, r.data.expires_at);
    back(locale, returnTo, "etat", "confirmed");
  }
  back(locale, returnTo, "erreur", r.status === 429 ? "too_many" : "reauth");
}
