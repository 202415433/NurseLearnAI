import Link from "next/link";
import { notFound } from "next/navigation";

import { Notice } from "@/components/Notice";
import { api, type Profession, type ProgramSummary } from "@/lib/api";
import { getDictionary, isLocale } from "@/lib/i18n";

type Props = { params: Promise<{ locale: string }>; searchParams: Promise<{ j?: string; p?: string }> };

export default async function CatalogPage({ params, searchParams }: Props) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const { j, p } = await searchParams;
  const t = getDictionary(locale);
  if (!j || !p) notFound();

  let programs: ProgramSummary[] | null = null;
  let profession: Profession | undefined;
  try {
    const [list, professions] = await Promise.all([
      api.programs({ jurisdiction: j, profession: p }, locale),
      api.professions(j, locale),
    ]);
    programs = list;
    profession = professions.find((x) => x.code === p);
  } catch {
    programs = null;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">{t.catalog.title}</h1>
      {profession ? (
        <p className="text-fg-muted">
          {t.catalog.for} : <strong className="text-fg">{profession.title}</strong> ·{" "}
          <Link href={`/${locale}/metier?j=${encodeURIComponent(j)}`} className="text-primary underline">
            {t.catalog.back}
          </Link>
        </p>
      ) : null}

      {programs === null ? (
        <Notice tone="danger">{t.errors.unavailable}</Notice>
      ) : programs.length === 0 ? (
        <section data-testid="catalog-empty" className="rounded-lg border border-border bg-surface p-6">
          <h2 className="text-xl font-semibold">{t.catalog.emptyTitle}</h2>
          <p className="mt-2 max-w-prose text-fg-muted">{t.catalog.emptyText}</p>
        </section>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {programs.map((prog) => (
            <li key={prog.code} className="rounded-lg border border-border bg-surface p-5" data-testid={`program-${prog.code}`}>
              <h2 className="text-xl font-semibold">{prog.title}</h2>
              {prog.summary ? <p className="mt-1 text-fg-muted">{prog.summary}</p> : null}
              <p className="mt-3 text-sm">
                <span className="rounded-sm bg-accent-soft px-2 py-1 text-fg">{t.catalog.levels[prog.level] ?? prog.level}</span>{" "}
                <span className="rounded-sm bg-primary-soft px-2 py-1 text-fg">{t.catalog.tiers[prog.access_tier] ?? prog.access_tier}</span>
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
