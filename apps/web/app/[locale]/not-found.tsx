import Link from "next/link";
import { headers } from "next/headers";

import { getDictionary, isLocale, defaultLocale } from "@/lib/i18n";

export default async function NotFound() {
  // not-found ne reçoit pas les paramètres : la langue est déduite de l'en-tête posé par le middleware.
  const h = await headers();
  const fromHeader = h.get("x-nl-locale") ?? "";
  const locale = isLocale(fromHeader) ? fromHeader : defaultLocale;
  const t = getDictionary(locale);
  return (
    <div className="space-y-4" data-testid="not-found">
      <h1 className="text-3xl font-bold">{t.states.notFoundTitle}</h1>
      <p className="text-fg-muted">{t.states.notFoundText}</p>
      <Link
        href={`/${locale}`}
        className="inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary hover:bg-primary-hover"
      >
        {t.states.backHome}
      </Link>
    </div>
  );
}
