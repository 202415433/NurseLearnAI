// Middleware : préfixe de langue (/ → /fr ou /en selon Accept-Language) et politique CSP par requête.
import { NextResponse, type NextRequest } from "next/server";

import { defaultLocale, isLocale, locales } from "./lib/i18n";
import { buildCsp, createNonce } from "./lib/security";
import {
  CHECK_COOKIE,
  CHECK_SECONDS,
  interpretRefresh,
  rewriteCookieHeader,
  type RefreshOutcome,
} from "./lib/session-refresh";

const SECURE = (process.env.SESSION_COOKIE_SECURE ?? "true") !== "false";
const SESSION_COOKIE = SECURE ? "__Host-nl_session" : "nl_session";
const API = (process.env.API_INTERNAL_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function refreshSession(token: string): Promise<RefreshOutcome> {
  try {
    const response = await fetch(`${API}/api/v1/auth/session/refresh`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    return interpretRefresh(response.status, response.status === 200 ? await response.json() : null);
  } catch {
    return { kind: "keep" }; // API indisponible : la page traitera la session normalement
  }
}

function preferredLocale(request: NextRequest): string {
  const header = request.headers.get("accept-language") ?? "";
  const ranked = header
    .split(",")
    .map((part) => {
      const [tag, q] = part.trim().split(";q=");
      return { lang: tag.slice(0, 2).toLowerCase(), q: q ? Number(q) : 1 };
    })
    .sort((a, b) => b.q - a.q);
  return ranked.find((r) => (locales as readonly string[]).includes(r.lang))?.lang ?? defaultLocale;
}

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const first = pathname.split("/")[1] ?? "";
  if (!isLocale(first)) {
    const url = request.nextUrl.clone();
    url.pathname = `/${preferredLocale(request)}${pathname === "/" ? "" : pathname}`;
    return NextResponse.redirect(url);
  }

  const nonce = createNonce();
  const csp = buildCsp(nonce, {
    dev: process.env.NODE_ENV === "development",
    https: request.nextUrl.protocol === "https:",
  });
  // Next.js lit le nonce dans l'en-tête CSP de la requête et l'applique à ses scripts.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", csp);
  // Langue transmise aux pages qui ne reçoivent pas les paramètres de route (not-found).
  requestHeaders.set("x-nl-locale", first);

  const token = request.cookies.get(SESSION_COOKIE)?.value;
  let outcome: RefreshOutcome = { kind: "keep" };
  if (token && !request.cookies.has(CHECK_COOKIE)) {
    outcome = await refreshSession(token);
    if (outcome.kind !== "keep") {
      requestHeaders.set(
        "cookie",
        rewriteCookieHeader(
          requestHeaders.get("cookie"),
          SESSION_COOKIE,
          outcome.kind === "rotate" ? outcome.token : null,
        ),
      );
    }
  }

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  if (token && !request.cookies.has(CHECK_COOKIE)) {
    const base = { httpOnly: true, secure: SECURE, sameSite: "lax" as const, path: "/" };
    if (outcome.kind === "rotate") {
      response.cookies.set(SESSION_COOKIE, outcome.token, { ...base, expires: new Date(outcome.expiresAt) });
    } else if (outcome.kind === "clear") {
      response.cookies.delete(SESSION_COOKIE);
    }
    if (outcome.kind !== "clear") response.cookies.set(CHECK_COOKIE, "1", { ...base, maxAge: CHECK_SECONDS });
  }
  return response;
}

export const config = {
  // Les ressources statiques et le préchargement de route n'ont pas besoin de nonce.
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.svg|.*\\..*).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
