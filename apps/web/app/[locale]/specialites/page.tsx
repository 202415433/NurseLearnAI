import Link from "next/link";

import { AvailabilityBadge } from "@/components/AvailabilityBadge";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { ChoiceCard } from "@/components/ChoiceCard";
import { api } from "@/lib/api";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, resolvePath, single, type SearchParams } from "@/lib/route";
export const dynamic = "force-dynamic";

export default async function SpecialtiesPage({ params, searchParams }: { params: Promise<{ locale: string }>; searchParams: SearchParams }) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale);
  const query = await searchParams;
  const { jurisdiction, profession } = await resolvePath(locale, single(query.j), single(query.p) ?? "");
  const p = profession!;
  const specialties = await api.specialties({ jurisdiction: jurisdiction.code, profession: p.code }, locale);
  const base = `/${locale}/programmes?j=${encodeURIComponent(jurisdiction.code)}&p=${encodeURIComponent(p.code)}`;

  return (
    <div>
      <Breadcrumbs
        label={t.a11y.breadcrumb}
        items={[
          { label: t.crumbs.home, href: `/${locale}` },
          { label: t.crumbs.countries, href: `/${locale}/pays` },
          { label: jurisdiction.name, href: `/${locale}/metiers?j=${encodeURIComponent(jurisdiction.code)}` },
          { label: p.title },
          { label: t.crumbs.specialties },
        ]}
      />
      <h1 className="text-3xl font-bold">{t.specialties.title}</h1>
      <p className="mt-2 text-lg">
        {p.title} · {jurisdiction.name}
      </p>
      <div className="mt-3">
        <AvailabilityBadge availability={p.availability} t={t.availability} />
      </div>
      <p className="mt-4 max-w-prose text-fg-muted">{t.specialties.lead}</p>
      <Link href={base} data-testid="all-programs" className="mt-4 inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary hover:bg-primary-hover">
        {t.specialties.all}
      </Link>
      <ul className="mt-6 grid gap-3 sm:grid-cols-2" data-testid="specialty-list">
        {specialties.items.map((s) => (
          <ChoiceCard
            key={s.code}
            testId={`specialty-${s.code}`}
            href={`${base}&s=${encodeURIComponent(s.code)}`}
            title={s.name}
            description={`${t.specialties.group} ${s.group.toUpperCase()} · ${t.specialties.count(s.published_programs)}`}
          />
        ))}
      </ul>
    </div>
  );
}
