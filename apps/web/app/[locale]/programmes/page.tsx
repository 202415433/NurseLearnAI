import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/Breadcrumbs";
import { ChoiceCard } from "@/components/ChoiceCard";
import { EmptyState } from "@/components/EmptyState";
import { Pagination } from "@/components/Pagination";
import { api, isValid } from "@/lib/api";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, orNotFound, resolvePath, single, type SearchParams } from "@/lib/route";

export const dynamic = "force-dynamic";
const LIMIT = 20;

export default async function ProgramsPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale);
  const query = await searchParams;
  const { jurisdiction, profession } = await resolvePath(locale, single(query.j), single(query.p) ?? "");
  const p = profession!;
  const specialty = single(query.s);
  if (specialty !== undefined && !isValid("code", specialty)) notFound();
  const rawOffset = single(query.offset) ?? "0";
  if (!/^\d{1,5}$/.test(rawOffset)) notFound();
  const offset = Number(rawOffset);

  const programs = await orNotFound(
    api.programs({ jurisdiction: jurisdiction.code, profession: p.code, specialty, offset, limit: LIMIT }, locale),
  );
  const base = `/${locale}/programmes?j=${encodeURIComponent(jurisdiction.code)}&p=${encodeURIComponent(p.code)}${
    specialty ? `&s=${encodeURIComponent(specialty)}` : ""
  }`;

  return (
    <div>
      <Breadcrumbs
        label={t.a11y.breadcrumb}
        items={[
          { label: t.crumbs.home, href: `/${locale}` },
          { label: t.crumbs.countries, href: `/${locale}/pays` },
          { label: jurisdiction.name, href: `/${locale}/metiers?j=${encodeURIComponent(jurisdiction.code)}` },
          {
            label: p.title,
            href: `/${locale}/specialites?j=${encodeURIComponent(jurisdiction.code)}&p=${encodeURIComponent(p.code)}`,
          },
          { label: t.crumbs.programs },
        ]}
      />
      <h1 className="text-3xl font-bold">{t.programs.title}</h1>
      <p className="mt-2 text-lg">
        {p.title} · {jurisdiction.name}
      </p>
      {programs.items.length === 0 ? (
        <div className="mt-6">
          <EmptyState title={t.programs.emptyTitle} testId="programs-empty">
            <p>{t.programs.emptyText}</p>
            {p.availability.in_preparation > 0 ? (
              <p className="mt-2 font-semibold text-fg" data-testid="programs-in-preparation">
                {t.availability.inPreparation(p.availability.in_preparation)}
              </p>
            ) : null}
          </EmptyState>
        </div>
      ) : (
        <ul className="mt-6 grid gap-4" data-testid="program-list">
          {programs.items.map((prog) => (
            <ChoiceCard
              pendingLabel={t.states.loading}
              key={prog.id}
              testId={`program-${prog.code}`}
              href={`/${locale}/programmes/${prog.id}`}
              title={prog.title}
              description={prog.summary ?? undefined}
            >
              <span className="flex flex-wrap gap-2 text-sm text-fg-muted">
                <span>{t.programs.levels[prog.level] ?? prog.level}</span>
                <span aria-hidden="true">·</span>
                <span>{t.programs.modules(prog.module_count)}</span>
                {prog.specialty_name ? (
                  <>
                    <span aria-hidden="true">·</span>
                    <span>{prog.specialty_name}</span>
                  </>
                ) : null}
                <span aria-hidden="true">·</span>
                <span>{t.programs.tiers[prog.access_tier] ?? prog.access_tier}</span>
              </span>
            </ChoiceCard>
          ))}
        </ul>
      )}
      <Pagination
        total={programs.total}
        limit={programs.limit}
        offset={programs.offset}
        buildHref={(o) => `${base}&offset=${o}`}
        labels={{
          label: t.a11y.pagination,
          previous: t.programs.previous,
          next: t.programs.next,
          pageOf: t.programs.pageOf,
        }}
      />
    </div>
  );
}
