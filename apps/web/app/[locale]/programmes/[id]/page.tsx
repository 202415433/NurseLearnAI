import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/Breadcrumbs";
import { EmptyState } from "@/components/EmptyState";
import { Notice } from "@/components/Notice";
import { api, isValid } from "@/lib/api";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, orNotFound } from "@/lib/route";

export const dynamic = "force-dynamic";

export default async function ProgramPage({ params }: { params: Promise<{ locale: string; id: string }> }) {
  const locale = await localeFrom(params);
  const { id } = await params;
  if (!isValid("uuid", id)) notFound();
  const t = getDictionary(locale);
  const program = await orNotFound(api.program(id, locale));
  const list = `/${locale}/programmes?j=${encodeURIComponent(program.jurisdiction_code)}&p=${encodeURIComponent(program.profession_code)}`;

  return (
    <article>
      <Breadcrumbs
        label={t.a11y.breadcrumb}
        items={[
          { label: t.crumbs.home, href: `/${locale}` },
          { label: t.crumbs.countries, href: `/${locale}/pays` },
          { label: program.profession_title, href: `/${locale}/metiers?j=${encodeURIComponent(program.jurisdiction_code)}` },
          { label: t.crumbs.programs, href: list },
          { label: program.title },
        ]}
      />
      <h1 className="text-3xl font-bold" data-testid="program-title">
        {program.title}
      </h1>
      {program.summary ? <p className="mt-2 max-w-prose text-lg text-fg-muted">{program.summary}</p> : null}
      <p className="mt-3 flex flex-wrap gap-2 text-sm text-fg-muted">
        <span>{program.profession_title}</span>
        <span aria-hidden="true">·</span>
        <span>{t.programs.levels[program.level] ?? program.level}</span>
        <span aria-hidden="true">·</span>
        <span>{t.programs.tiers[program.access_tier] ?? program.access_tier}</span>
      </p>
      <div className="mt-4">
        <Notice tone="warning">{t.disclaimer}</Notice>
      </div>

      <h2 className="mt-8 text-2xl font-semibold">{t.program.modulesTitle}</h2>
      {program.modules.length === 0 ? (
        <div className="mt-4">
          <EmptyState title={t.programs.modules(0)} />
        </div>
      ) : (
        <ol className="mt-4 space-y-4" data-testid="module-list">
          {program.modules.map((m) => (
            <li key={m.code} className="rounded-lg border border-border bg-surface p-5">
              <h3 className="text-lg font-semibold">
                {m.position}. {m.title}
              </h3>
              <p className="mt-1 text-sm text-fg-muted">
                {t.programs.levels[m.level] ?? m.level} · {t.program.minutes(m.estimated_minutes)} ·{" "}
                {m.is_required ? t.program.required : t.program.optional}
              </p>
              {m.summary ? <p className="mt-2">{m.summary}</p> : null}
              {m.lessons.length === 0 ? (
                <p className="mt-3 text-fg-muted">{t.program.noLessons}</p>
              ) : (
                <>
                  <h4 className="mt-4 font-semibold">{t.program.lessons}</h4>
                  <ol className="mt-2 space-y-2" data-testid="lesson-list">
                    {m.lessons.map((l) => (
                      <li key={l.code} className="flex flex-wrap items-baseline justify-between gap-2 border-t border-border pt-2">
                        <span lang={l.locale !== locale ? l.locale : undefined}>
                          {l.position}. {l.title}
                          {l.jurisdiction_specific ? (
                            <span className="ml-2 rounded-full bg-accent-soft px-2 py-0.5 text-xs">{t.program.local}</span>
                          ) : null}
                        </span>
                        <span className="text-sm text-fg-muted">{t.program.minutes(l.estimated_minutes)}</span>
                      </li>
                    ))}
                  </ol>
                </>
              )}
            </li>
          ))}
        </ol>
      )}
      <p className="mt-6 text-sm text-fg-muted">{t.program.readerSoon}</p>
    </article>
  );
}
