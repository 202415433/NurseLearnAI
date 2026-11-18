import { AvailabilityBadge } from "@/components/AvailabilityBadge";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { ChoiceCard } from "@/components/ChoiceCard";
import { EmptyState } from "@/components/EmptyState";
import { Notice } from "@/components/Notice";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, resolvePath, single, type SearchParams } from "@/lib/route";

export const dynamic = "force-dynamic";

export default async function ProfessionsPage({ params, searchParams }: { params: Promise<{ locale: string }>; searchParams: SearchParams }) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale);
  const query = await searchParams;
  const { jurisdiction, professions } = await resolvePath(locale, single(query.j));
  const back = jurisdiction.country_code
    ? `/${locale}/juridictions?pays=${jurisdiction.country_code}`
    : `/${locale}/juridictions?portee=cadre`;

  return (
    <div>
      <Breadcrumbs
        label={t.a11y.breadcrumb}
        items={[
          { label: t.crumbs.home, href: `/${locale}` },
          { label: t.crumbs.countries, href: `/${locale}/pays` },
          { label: jurisdiction.name, href: back },
          { label: t.crumbs.professions },
        ]}
      />
      <h1 className="text-3xl font-bold">{t.professions.title}</h1>
      <p className="mt-2 text-lg">{jurisdiction.name}</p>
      <div className="mt-4">
        <Notice>{t.professions.lead}</Notice>
      </div>
      {professions.length === 0 ? (
        <div className="mt-6">
          <EmptyState title={t.professions.empty} />
        </div>
      ) : (
        <ul className="mt-6 grid gap-4" data-testid="profession-list">
          {professions.map((p) => (
            <ChoiceCard
              key={p.code}
              testId={`profession-${p.code}`}
              href={`/${locale}/specialites?j=${encodeURIComponent(jurisdiction.code)}&p=${encodeURIComponent(p.code)}`}
              title={p.title}
              description={`${t.professions.categories[p.category] ?? p.category} · ${
                p.is_regulated ? t.professions.regulated : t.professions.notRegulated
              }${p.regulator_name ? ` · ${t.professions.regulator} : ${p.regulator_name}` : ""}`}
            >
              <AvailabilityBadge availability={p.availability} t={t.availability} />
              {p.validation_status !== "validated" ? (
                <span className="mt-2 block text-sm text-fg-muted" data-testid="profession-pending">
                  {t.professions.pending}
                </span>
              ) : null}
            </ChoiceCard>
          ))}
        </ul>
      )}
    </div>
  );
}
