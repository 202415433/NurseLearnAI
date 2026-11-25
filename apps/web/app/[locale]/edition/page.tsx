import Link from "next/link";

import { Breadcrumbs } from "@/components/Breadcrumbs";
import { Notice } from "@/components/Notice";
import { edition, pick } from "@/lib/edition";
import { editorOrRedirect } from "@/lib/edition-guard";
import { getDictionary } from "@/lib/i18n";
import { getMediaDictionary } from "@/lib/i18n-media";
import { localeFrom } from "@/lib/route";

export default async function EditionHome({ params }: { params: Promise<{ locale: string }> }) {
  const locale = await localeFrom(params);
  const { allowed } = await editorOrRedirect(locale, `/${locale}/edition`);
  const t = getMediaDictionary(locale);
  const base = getDictionary(locale);
  const crumbs = [{ label: base.crumbs.home, href: `/${locale}` }, { label: t.hub.title }];
  if (!allowed) {
    return (
      <div className="space-y-6">
        <Breadcrumbs items={crumbs} label={base.a11y.breadcrumb} />
        <h1 className="text-3xl font-bold">{t.hub.title}</h1>
        <Notice tone="warning">{t.hub.forbidden}</Notice>
      </div>
    );
  }
  const modules = await edition.modules(locale);
  const card =
    "block rounded-lg border border-border bg-surface p-5 hover:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";
  return (
    <div className="space-y-8">
      <Breadcrumbs items={crumbs} label={base.a11y.breadcrumb} />
      <header className="space-y-2">
        <h1 className="text-3xl font-bold">{t.hub.title}</h1>
        <p className="text-fg-muted">{t.hub.lead}</p>
      </header>
      <nav aria-label={t.nav.edition} className="grid gap-4 sm:grid-cols-2">
        <Link href={`/${locale}/edition/medias`} className={card} data-testid="hub-library">
          <span className="text-lg font-semibold text-primary">{t.nav.library}</span>
          <span className="mt-1 block text-fg-muted">{t.hub.libraryText}</span>
        </Link>
        <Link href={`/${locale}/edition/medias/televerser`} className={card} data-testid="hub-upload">
          <span className="text-lg font-semibold text-primary">{t.nav.upload}</span>
          <span className="mt-1 block text-fg-muted">{t.hub.uploadText}</span>
        </Link>
      </nav>
      <section aria-labelledby="modules-titre" className="space-y-3">
        <h2 id="modules-titre" className="text-xl font-semibold">
          {t.hub.modulesTitle}
        </h2>
        {modules.ok && modules.data.length ? (
          <ul className="divide-y divide-border rounded-lg border border-border" data-testid="module-list">
            {modules.data.map((m) => (
              <li key={m.id}>
                <Link
                  href={`/${locale}/edition/modules/${m.id}`}
                  className="flex min-h-11 items-center justify-between gap-4 px-4 py-3 hover:bg-surface"
                >
                  <span className="font-medium">{pick(m.title, locale) || m.code}</span>
                  <span className="text-sm text-fg-muted">{m.code}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-fg-muted">{t.hub.modulesEmpty}</p>
        )}
      </section>
    </div>
  );
}
