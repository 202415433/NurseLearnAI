import { Breadcrumbs } from "@/components/Breadcrumbs";
import { ChoiceCard } from "@/components/ChoiceCard";
import { EmptyState } from "@/components/EmptyState";
import { api } from "@/lib/api";
import { getDictionary } from "@/lib/i18n";
import { localeFrom } from "@/lib/route";

export const dynamic = "force-dynamic";

export default async function CountriesPage({ params }: { params: Promise<{ locale: string }> }) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale);
  const countries = await api.countries(locale);

  return (
    <div>
      <Breadcrumbs
        label={t.a11y.breadcrumb}
        items={[{ label: t.crumbs.home, href: `/${locale}` }, { label: t.crumbs.countries }]}
      />
      <h1 className="text-3xl font-bold">{t.countries.title}</h1>
      <p className="mt-2 max-w-prose text-fg-muted">{t.countries.lead}</p>
      {countries.items.length === 0 ? (
        <div className="mt-6">
          <EmptyState title={t.jurisdictions.empty} />
        </div>
      ) : (
        <ul className="mt-6 grid gap-4 sm:grid-cols-2" data-testid="country-list">
          {countries.items.map((c) => (
            <ChoiceCard
              pendingLabel={t.states.loading}
              key={c.code}
              testId={`country-${c.code}`}
              href={`/${locale}/juridictions?pays=${encodeURIComponent(c.code)}`}
              title={c.name}
              description={t.countries.jurisdictions(c.jurisdiction_count)}
            />
          ))}
          <ChoiceCard
            pendingLabel={t.states.loading}
            testId="country-framework"
            href={`/${locale}/juridictions?portee=cadre`}
            title={t.countries.frameworkTitle}
            description={t.countries.frameworkText}
          />
        </ul>
      )}
    </div>
  );
}
