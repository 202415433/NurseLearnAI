import Link from "next/link";
import { notFound } from "next/navigation";

import { Notice } from "@/components/Notice";
import { api, ApiError, type Jurisdiction, type Profession } from "@/lib/api";
import { getDictionary, isLocale } from "@/lib/i18n";

type Props = { params: Promise<{ locale: string }>; searchParams: Promise<{ j?: string }> };

export default async function ProfessionsPage({ params, searchParams }: Props) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const { j } = await searchParams;
  const t = getDictionary(locale);

  if (!j) {
    return (
      <div className="space-y-4">
        <h1 className="text-3xl font-bold">{t.professions.title}</h1>
        <Notice tone="info">{t.professions.missing}</Notice>
        <Link href={`/${locale}/pays`} className="text-primary underline">
          {t.professions.change}
        </Link>
      </div>
    );
  }

  let professions: Profession[] | null = null;
  let jurisdiction: Jurisdiction | undefined;
  let missing = false;
  try {
    const [list, all] = await Promise.all([api.professions(j, locale), api.jurisdictions(locale)]);
    professions = list;
    jurisdiction = all.find((x) => x.code === j.toUpperCase());
  } catch (e) {
    missing = e instanceof ApiError && (e.status === 404 || e.status === 400);
  }

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">{t.professions.title}</h1>
      {jurisdiction ? (
        <p className="text-fg-muted">
          {t.professions.in} : <strong className="text-fg">{jurisdiction.name}</strong> ·{" "}
          <Link href={`/${locale}/pays`} className="text-primary underline">
            {t.professions.change}
          </Link>
        </p>
      ) : null}
      <Notice tone="info">{t.professions.lead}</Notice>

      {professions === null ? (
        <Notice tone="danger">{missing ? t.errors.notFound : t.errors.unavailable}</Notice>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {professions.map((p) => (
            <li key={p.code} className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-5" data-testid={`profession-${p.code}`}>
              <div>
                <p className="text-sm font-medium text-accent">{t.professions.categories[p.category] ?? p.category}</p>
                <h2 className="text-xl font-semibold">{p.title}</h2>
              </div>
              <p className="text-sm text-fg-muted">
                {p.is_regulated ? t.professions.regulated : t.professions.notRegulated}
                {p.regulator_name ? (
                  <>
                    {" "}— {t.professions.regulator} : {p.regulator_name}
                  </>
                ) : null}
              </p>
              {p.validation_status !== "validated" ? (
                <p className="w-fit rounded-sm bg-warning-soft px-2 py-1 text-sm text-fg" data-testid="pending-badge">
                  {t.professions.pending}
                </p>
              ) : null}
              <Link
                href={`/${locale}/catalogue?j=${encodeURIComponent(p.jurisdiction_code)}&p=${encodeURIComponent(p.code)}`}
                className="mt-auto inline-flex min-h-11 w-fit items-center rounded-md bg-primary px-4 font-semibold text-on-primary hover:bg-primary-hover"
              >
                {t.professions.see}
                <span className="sr-only"> — {p.title}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
