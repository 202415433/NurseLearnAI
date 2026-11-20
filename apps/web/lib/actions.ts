"use server";

// Actions serveur de l'identité (formulaires sans JavaScript côté client).
// Chaque action : contrôle d'origine (CSRF), validation minimale, appel de l'API, redirection
// avec un code d'état ou d'erreur pris dans une liste fermée (aucun texte réfléchi).
import { redirect } from "next/navigation";

import { assertSameOrigin, callApi, clearSessionCookie, sessionToken, setSessionCookie } from "./auth";
import { safeNext } from "./csrf";
import { isLocale, type Locale } from "./i18n";

const PASSWORD_CODES = ["password_too_short", "password_too_long", "password_too_common", "password_contains_email"];

function field(form: FormData, name: string, max = 1024): string {
  const value = form.get(name);
  return typeof value === "string" ? value.slice(0, max) : "";
}

function localeOf(form: FormData): Locale {
  const value = field(form, "locale", 5);
  return isLocale(value) ? value : "fr";
}

function errorCode(status: number, data: { type?: string; password_errors?: string[] }): string {
  const type = data.type ?? "";
  if (status === 429) return "too_many";
  if (type.endsWith("reauthentication-required")) return "reauth";
  if (type.endsWith("email-not-verified")) return "not_verified";
  if (type.endsWith("invalid-credentials")) return "credentials";
  if (type.endsWith("invalid-token")) return "invalid_link";
  if (type.endsWith("consent-required")) return "consent";
  if (type.endsWith("weak-password")) {
    const first = (data.password_errors ?? []).find((c) => PASSWORD_CODES.includes(c));
    return first ?? "invalid_input";
  }
  if (status === 409) return "conflict";
  if (status === 403 || status === 404) return "forbidden";
  if (status === 422 || status === 400) return "invalid_input";
  return "generic";
}

async function prepare(form: FormData): Promise<Locale> {
  await assertSameOrigin();
  return localeOf(form);
}

// ------------------------------------------------------------------------------------------ inscription
export async function registerAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const result = await callApi<{ password_errors?: string[] }>("/api/v1/auth/register", {
    locale,
    body: {
      email: field(form, "email", 254),
      password: field(form, "password"),
      display_name: field(form, "display_name", 100),
      locale,
      adult_attestation: form.get("adult_attestation") === "on",
      accept_terms: form.get("accept_terms") === "on",
      accept_privacy: form.get("accept_privacy") === "on",
      website: field(form, "website", 200),
    },
  });
  if (result.status === 202) redirect(`/${locale}/verification?etat=registered`);
  redirect(`/${locale}/inscription?erreur=${errorCode(result.status, result.data)}`);
}

export async function verifyEmailAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const result = await callApi("/api/v1/auth/verify-email", { body: { token: field(form, "token", 128) } });
  if (result.ok) redirect(`/${locale}/connexion?etat=verified`);
  redirect(`/${locale}/verification?erreur=invalid_link`);
}

export async function resendVerificationAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const result = await callApi("/api/v1/auth/resend-verification", { body: { email: field(form, "email", 254) } });
  if (result.status === 429) redirect(`/${locale}/verification?erreur=too_many`);
  redirect(`/${locale}/verification?etat=resent`);
}

// ------------------------------------------------------------------------------------------ connexion
export async function loginAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const result = await callApi<{ session_token: string; expires_at: string }>("/api/v1/auth/login", {
    body: { email: field(form, "email", 254), password: field(form, "password") },
  });
  if (result.ok) {
    await setSessionCookie(result.data.session_token, result.data.expires_at);
    redirect(safeNext(field(form, "next", 300), locale));
  }
  redirect(`/${locale}/connexion?erreur=${errorCode(result.status, result.data)}`);
}

export async function logoutAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const token = await sessionToken();
  if (token) await callApi("/api/v1/auth/logout", { token }).catch(() => undefined);
  await clearSessionCookie();
  redirect(`/${locale}/connexion?etat=loggedOut`);
}

export async function logoutAllAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const result = await callApi("/api/v1/auth/logout-all", { token: await sessionToken() });
  if (result.ok) {
    await clearSessionCookie();
    redirect(`/${locale}/connexion?etat=loggedOutAll`);
  }
  redirect(`/${locale}/compte?erreur=${errorCode(result.status, result.data)}`);
}

export async function reauthenticateAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const result = await callApi<{ session_token: string; expires_at: string }>("/api/v1/auth/reauthenticate", {
    token: await sessionToken(),
    body: { password: field(form, "password") },
  });
  if (result.ok) {
    await setSessionCookie(result.data.session_token, result.data.expires_at);
    redirect(safeNext(field(form, "next", 300), locale) + "?etat=confirmed");
  }
  redirect(`/${locale}/compte?erreur=${errorCode(result.status, result.data)}`);
}

// ------------------------------------------------------------------------------------------ mot de passe
export async function forgotPasswordAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const result = await callApi("/api/v1/auth/password/forgot", { body: { email: field(form, "email", 254) } });
  if (result.status === 429) redirect(`/${locale}/mot-de-passe-oublie?erreur=too_many`);
  redirect(`/${locale}/mot-de-passe-oublie?etat=resetSent`);
}

export async function resetPasswordAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const token = field(form, "token", 128);
  const result = await callApi<{ password_errors?: string[] }>("/api/v1/auth/password/reset", {
    body: { token, new_password: field(form, "new_password") },
  });
  if (result.ok) {
    await clearSessionCookie();
    redirect(`/${locale}/connexion?etat=resetDone`);
  }
  const code = errorCode(result.status, result.data);
  if (code === "invalid_link") redirect(`/${locale}/reinitialisation?erreur=invalid_link`);
  redirect(`/${locale}/reinitialisation?jeton=${encodeURIComponent(token)}&erreur=${code}`);
}

export async function changePasswordAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const result = await callApi<{ session_token: string; expires_at: string; password_errors?: string[] }>(
    "/api/v1/auth/password/change",
    {
      token: await sessionToken(),
      body: { current_password: field(form, "current_password"), new_password: field(form, "new_password") },
    },
  );
  if (result.ok) {
    await setSessionCookie(result.data.session_token, result.data.expires_at);
    redirect(`/${locale}/compte?etat=passwordChanged`);
  }
  redirect(`/${locale}/compte?erreur=${errorCode(result.status, result.data)}`);
}

// ------------------------------------------------------------------------------------------ compte
export async function updateProfileAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const preferred = field(form, "preferred_locale", 5);
  const result = await callApi("/api/v1/me/profile", {
    method: "PATCH",
    token: await sessionToken(),
    body: {
      display_name: field(form, "display_name", 100),
      ...(isLocale(preferred) ? { locale: preferred } : {}),
    },
  });
  redirect(`/${locale}/compte?${result.ok ? "etat=saved" : `erreur=${errorCode(result.status, result.data)}`}`);
}

export async function revokeSessionAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const id = field(form, "session_id", 36);
  const result = await callApi(`/api/v1/me/sessions/${encodeURIComponent(id)}`, {
    method: "DELETE",
    token: await sessionToken(),
  });
  redirect(`/${locale}/compte?${result.ok ? "etat=sessionClosed" : "erreur=generic"}`);
}

// ------------------------------------------------------------------------------------------ invitations
export async function createInvitationAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const organization = field(form, "organization_id", 36);
  const result = await callApi("/api/v1/invitations", {
    token: await sessionToken(),
    body: {
      email: field(form, "email", 254),
      role: field(form, "role", 30),
      organization_id: organization || null,
      locale: isLocale(field(form, "invite_locale", 5)) ? field(form, "invite_locale", 5) : locale,
    },
  });
  const base = `/${locale}/compte/invitations`;
  redirect(`${base}?${result.ok ? "etat=invitationSent" : `erreur=${errorCode(result.status, result.data)}`}`);
}

export async function revokeInvitationAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const id = field(form, "invitation_id", 36);
  const result = await callApi(`/api/v1/invitations/${encodeURIComponent(id)}`, {
    method: "DELETE",
    token: await sessionToken(),
  });
  redirect(`/${locale}/compte/invitations?${result.ok ? "etat=invitationRevoked" : "erreur=forbidden"}`);
}

export async function acceptInvitationAction(form: FormData): Promise<void> {
  const locale = await prepare(form);
  const token = field(form, "token", 128);
  const result = await callApi<{ session_token: string; expires_at: string; detail?: string }>(
    "/api/v1/invitations/accept",
    { token: await sessionToken(), body: { token } },
  );
  if (result.ok) {
    await setSessionCookie(result.data.session_token, result.data.expires_at);
    redirect(`/${locale}/compte?etat=invitationAccepted`);
  }
  if (result.status === 401) redirect(`/${locale}/connexion?etat=expired`);
  const code = result.status === 403 ? "invitation_email" : errorCode(result.status, result.data);
  redirect(`/${locale}/invitation?erreur=${code}`);
}
