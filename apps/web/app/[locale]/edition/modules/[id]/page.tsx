import Link from "next/link";
import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/Breadcrumbs";
import { Notice } from "@/components/Notice";
import { edition, pick } from "@/lib/edition";
import { editorOrRedirect } from "@/lib/edition-guard";
import { getDictionary } from "@/lib/i18n";
import { getMediaDictionary } from "@/lib/i18n-media";
import { isUuid } from "@/lib/media";
import { localeFrom } from "@/lib/route";

export default async function ModulePage({ params }: { params: Promise<{ locale: string; id: string }> }) {
  const locale = await localeFrom(params);
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const { allowed } = await editorOrRedirect(locale, `/${locale}/edition/modules/${id}`);
  const t = getMediaDictionary(locale);
  const base = getDictionary(locale);
  if (!allowed) return <Notice tone="warning">{t.hub.forbidden}</Notice>;
  const mod = await edition.module(id, locale);
  if (!mod.ok) notFound();
  const lessons = await Promise.all(mod.data.lessons.map((l) => edition.lesson(l.id, locale)));
  const title = pick(mod.data.title, locale) || mod.data.code;
  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: base.crumbs.home, href: `/${locale}` },
          { label: t.hub.title, href: `/${locale}/edition` },
          { label: title },
        ]}
        label={base.a11y.breadcrumb}
      />
      <h1 className="text-3xl font-bold">{title}</h1>
      <section aria-labelledby="lecons-titre" className="space-y-3">
        <h2 id="lecons-titre" className="text-xl font-semibold">
          {t.modules.lessons}
        </h2>
        {lessons.length ? (
          <ol className="space-y-3" data-testid="lesson-list">
            {lessons.map((l, i) =>
              l.ok ? (
                <li key={l.data.id} className="rounded-lg border border-border p-4">
                  <p className="font-medium">
                    {i + 1}. {l.data.code}
                  </p>
                  {l.data.versions.length ? (
                    <ul className="mt-2 flex flex-wrap gap-2">
                      {l.data.versions.map((v) => (
                        <li key={v.id}>
                          <Link
                            href={`/${locale}/edition/versions/${v.id}/medias`}
                            className="inline-flex min-h-11 items-center rounded-md border border-border-strong px-3 hover:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                            data-testid="version-link"
                          >
                            {t.modules.manageMedia(v.version_no, v.locale)} — {t.picker.statuses[v.status] ?? v.status}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-fg-muted">{t.modules.noVersion}</p>
                  )}
                </li>
              ) : null,
            )}
          </ol>
        ) : (
          <p className="text-fg-muted">{t.modules.noLessons}</p>
        )}
      </section>
    </div>
  );
}
