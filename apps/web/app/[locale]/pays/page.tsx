import Link from "next/link";
import { notFound } from "next/navigation";

import { Notice } from "@/components/Notice";
import { api, type Jurisdiction } from "@/lib/api";
import { getDictionary, isLocale } from "@/lib/i18n";

const GROUP_OF: Record<string, "province" | "country" | "framework"> = {
  province: "province",
  state: "province",
  region: "province",
  country: "country",
  framework: "framework",
};

export default async function JurisdictionsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = getDictionary(locale);

  let failed = false;
  let items: Jurisdiction[] = [];
  try {
    items = await api.jurisdictions(locale);
  } catch {
    failed = true;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">{t.jurisdictions.title}</h1>
      <p className="text-fg-muted">{t.jurisdictions.lead}</p>
      {failed ? (
        <Notice tone="danger">{t.errors.unavailable}</Notice>
      ) : (
        (["province", "country", "framework"] as const).map((group) => {
          const list = items.filter((j) => GROUP_OF[j.kind] === group);
          if (list.length === 0) return null;
          return (
            <section key={group} aria-labelledby={`g-${group}`} className="space-y-3">
              <h2 id={`g-${group}`} className="text-xl font-semibold">
                {t.jurisdictions.groups[group]}
              </h2>
              <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {list.map((j) => (
                  <li key={j.code}>
                    <Link
                      href={`/${locale}/metier?j=${encodeURIComponent(j.code)}`}
                      data-testid={`jurisdiction-${j.code}`}
                      className="flex min-h-11 items-center rounded-lg border border-border bg-surface px-4 py-3 font-medium hover:border-primary hover:bg-primary-soft"
                    >
                      {j.name}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          );
        })
      )}
    </div>
  );
}
