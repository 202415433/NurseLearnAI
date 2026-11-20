// Session côté serveur Web (BFF, ADR-0007) : le navigateur ne détient qu'un cookie HttpOnly ;
// le serveur Web relaie l'identifiant à l'API dans l'en-tête Authorization. Aucun jeton n'est
// accessible au JavaScript du navigateur. Module réservé au serveur (next/headers).

import { cookies, headers } from "next/headers";
import { cache } from "react";

import { BASE_URL, visitorAddress } from "./api";
import { isSameOrigin } from "./csrf";
import type { Locale } from "./i18n";

export const SESSION_COOKIE_SECURE = (process.env.SESSION_COOKIE_SECURE ?? "true") !== "false";
// Préfixe __Host- (impose Secure, Path=/ et l'absence de Domain) dès que le cookie est sécurisé.
export const SESSION_COOKIE = SESSION_COOKIE_SECURE ? "__Host-nl_session" : "nl_session";

export type Role = { role: string; organization_id: string | null; organization_name: string | null };
export type Me = {
  id: string;
  email: string;
  display_name: string;
  locale: string;
  status: string;
  roles: Role[];
  permissions: string[];
  sensitive_fresh: boolean;
  session_expires_at: string;
};
export type ApiResult<T> = { ok: boolean; status: number; data: T & { type?: string; detail?: string } };

export async function callApi<T = Record<string, unknown>>(
  path: string,
  options: { method?: string; body?: unknown; token?: string | null; locale?: Locale } = {},
): Promise<ApiResult<T>> {
  const visitor = await visitorAddress();
  const response = await fetch(`${BASE_URL}${path}`, {
    method: options.method ?? "POST",
    headers: {
      Accept: "application/json",
      ...(options.body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
      ...(options.locale ? { "Accept-Language": options.locale } : {}),
      ...(visitor ? { "X-Forwarded-For": visitor } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
  const text = await response.text();
  let data = {} as T & { type?: string; detail?: string };
  try {
    data = text ? JSON.parse(text) : data;
  } catch {
    // corps non JSON : ignoré
  }
  return { ok: response.ok, status: response.status, data };
}

export async function sessionToken(): Promise<string | null> {
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export async function setSessionCookie(token: string, expiresAt: string): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: SESSION_COOKIE_SECURE,
    sameSite: "lax",
    path: "/",
    expires: new Date(expiresAt),
  });
}

export async function clearSessionCookie(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}

// Compte courant (une seule requête à l'API par rendu de page).
export const currentUser = cache(async (): Promise<Me | null> => {
  const token = await sessionToken();
  if (!token) return null;
  try {
    const result = await callApi<Me>("/api/v1/me", { method: "GET", token });
    return result.ok ? result.data : null;
  } catch {
    return null;
  }
});

// Contrôle d'origine de chaque action serveur (défense en profondeur contre le CSRF).
export async function assertSameOrigin(): Promise<void> {
  const h = await headers();
  if (!isSameOrigin(h.get("origin"), h.get("x-forwarded-host") ?? h.get("host"), h.get("sec-fetch-site"))) {
    throw new Error("Origine refusée");
  }
}

export function hasRole(me: Me | null, role: string): boolean {
  return Boolean(me?.roles.some((r) => r.role === role));
}

export function canInvite(me: Me | null): boolean {
  return Boolean(me && (me.permissions.includes("user:manage") || hasRole(me, "org_admin")));
}
