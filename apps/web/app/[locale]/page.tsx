import Link from "next/link";
import { notFound } from "next/navigation";

import { Notice } from "@/components/Notice";
import { getDictionary, isLocale } from "@/lib/i18n";

export default async function HomePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = getDictionary(locale);

  return (
    <div className="space-y-10">
      <section className="space-y-4">
        <h1 className="text-3xl font-bold leading-tight sm:text-4xl">{t.home.title}</h1>
        <p className="max-w-prose text-lg text-fg-muted">{t.home.lead}</p>
        <Link
          href={`/${locale}/pays`}
          data-testid="cta-start"
          className="inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary hover:bg-primary-hover"
        >
          {t.home.cta}
        </Link>
      </section>

      <section aria-labelledby="etapes" className="space-y-4">
        <h2 id="etapes" className="text-2xl font-semibold">
          {t.home.stepsTitle}
        </h2>
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {t.home.steps.map((step, i) => (
            <li key={step.title} className="rounded-lg border border-border bg-surface p-5">
              <span aria-hidden="true" className="mb-2 inline-flex h-8 w-8 items-center justify-center rounded-full bg-accent-fill font-bold text-on-accent-fill">
                {i + 1}
              </span>
              <h3 className="font-semibold">{step.title}</h3>
              <p className="mt-1 text-fg-muted">{step.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <Notice tone="warning" title={t.home.noticeTitle}>
        {t.disclaimer}
      </Notice>
    </div>
  );
}
