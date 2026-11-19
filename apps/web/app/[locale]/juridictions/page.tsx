import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/Breadcrumbs";
import { ChoiceCard } from "@/components/ChoiceCard";
import { EmptyState } from "@/components/EmptyState";
import { api, isValid } from "@/lib/api";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, orNotFound, single, type SearchParams } from "@/lib/route";

export const dynamic = "force-dynamic";

export default async function JurisdictionsPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale);
  const query = await searchParams;
  const country = single(query.pays);
  const framework = single(query.portee) === "cadre";

  if (!framework && !isValid("country", country)) notFound();
  const data = await orNotFound(api.jurisdictions(framework ? { scope: "framework" } : { country }, locale));

  let heading = t.jurisdictions.frameworkTitle;
  let crumb = t.countries.frameworkTitle;
  if (!framework) {
    const countries = await api.countries(locale);
    const found = countries.items.find((c) => c.code === country!.toUpperCase());
    if (!found) notFound();
    heading = t.jurisdictions.title;
    crumb = found.name;
  }

  return (
    <div>
      <Breadcrumbs
        label={t.a11y.breadcrumb}
        items={[
          { label: t.crumbs.home, href: `/${locale}` },
          { label: t.crumbs.countries, href: `/${locale}/pays` },
          { label: crumb },
        ]}
      />
      <h1 className="text-3xl font-bold">{heading}</h1>
      {data.items.length === 0 ? (
        <div className="mt-6">
          <EmptyState title={t.jurisdictions.empty} />
        </div>
      ) : (
        <ul className="mt-6 grid gap-4 sm:grid-cols-2" data-testid="jurisdiction-list">
          {data.items.map((j) => (
            <ChoiceCard
              pendingLabel={t.states.loading}
              key={j.code}
              testId={`jurisdiction-${j.code}`}
              href={`/${locale}/metiers?j=${encodeURIComponent(j.code)}`}
              title={j.name}
              description={t.jurisdictions.kinds[j.kind] ?? j.kind}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
