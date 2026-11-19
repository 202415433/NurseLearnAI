// Middleware : préfixe de langue (/ → /fr ou /en selon Accept-Language) et politique CSP par requête.
import { NextResponse, type NextRequest } from "next/server";

import { defaultLocale, isLocale, locales } from "./lib/i18n";
import { buildCsp, createNonce } from "./lib/security";

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

export function middleware(request: NextRequest) {
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
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
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
