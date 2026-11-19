"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { getDictionary, isLocale } from "@/lib/i18n";

// Erreur générique : aucun détail technique n'est affiché au visiteur.
export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const params = useParams<{ locale: string }>();
  const locale = params?.locale && isLocale(params.locale) ? params.locale : "fr";
  const t = getDictionary(locale);
  return (
    <div role="alert" className="space-y-4" data-testid="error-page">
      <h1 className="text-3xl font-bold">{t.states.errorTitle}</h1>
      <p className="text-fg-muted">{t.states.errorText}</p>
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={reset}
          className="inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary hover:bg-primary-hover"
        >
          {t.states.retry}
        </button>
        <Link
          href={`/${locale}`}
          className="inline-flex min-h-11 items-center rounded-md border border-border px-5 hover:border-primary"
        >
          {t.states.backHome}
        </Link>
      </div>
    </div>
  );
}
