// Redirige les chemins sans préfixe de langue (/ → /fr ou /en selon Accept-Language).
import { NextResponse, type NextRequest } from "next/server";

import { defaultLocale, isLocale, locales } from "./lib/i18n";

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
  if (isLocale(first)) {
    // Transmet la langue aux pages qui ne reçoivent pas les paramètres de route (not-found).
    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("x-nl-locale", first);
    return NextResponse.next({ request: { headers: requestHeaders } });
  }
  const url = request.nextUrl.clone();
  url.pathname = `/${preferredLocale(request)}${pathname === "/" ? "" : pathname}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next|api|favicon.svg|.*\\..*).*)"],
};
