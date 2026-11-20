import { Notice } from "@/components/Notice";
import { getDictionary } from "@/lib/i18n";
import { localeFrom } from "@/lib/route";

// Texte versionné (TERMS_VERSION / PRIVACY_POLICY_VERSION côté API) : version de travail.
const VERSION = "2026-11-19";

export default async function TermsPage({ params }: { params: Promise<{ locale: string }> }) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale).auth.legal;
  return (
    <article className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-3xl font-bold">{t.termsTitle}</h1>
      <Notice>{t.draft}</Notice>
      <p className="text-sm text-fg-muted">
        {t.version} {VERSION}
      </p>
      <ul className="list-disc space-y-3 pl-6">
        {t.terms.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </article>
  );
}
