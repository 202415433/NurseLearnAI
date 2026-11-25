import { Breadcrumbs } from "@/components/Breadcrumbs";
import { FormStatus } from "@/components/Form";
import { Notice } from "@/components/Notice";
import { MediaUploader, type ScopeOption } from "@/components/media/MediaUploader";
import { QuotaPanel } from "@/components/media/QuotaPanel";
import { ReauthCard } from "@/components/media/ReauthCard";
import { edition } from "@/lib/edition";
import { editorOrRedirect } from "@/lib/edition-guard";
import { getDictionary } from "@/lib/i18n";
import { getMediaDictionary } from "@/lib/i18n-media";
import { isBlocked } from "@/lib/media";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

export default async function UploadPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const path = `/${locale}/edition/medias/televerser`;
  const { me, allowed } = await editorOrRedirect(locale, path);
  const t = getMediaDictionary(locale);
  const base = getDictionary(locale);
  const { status, error } = statusParams(await searchParams);
  const crumbs = [
    { label: base.crumbs.home, href: `/${locale}` },
    { label: t.hub.title, href: `/${locale}/edition` },
    { label: t.library.title, href: `/${locale}/edition/medias` },
    { label: t.upload.title },
  ];
  if (!allowed) {
    return (
      <div className="space-y-6">
        <Breadcrumbs items={crumbs} label={base.a11y.breadcrumb} />
        <h1 className="text-3xl font-bold">{t.upload.title}</h1>
        <Notice tone="warning">{t.hub.forbidden}</Notice>
      </div>
    );
  }
  const [quotas, videos] = await Promise.all([
    edition.quotas(locale),
    edition.library({ kind: "video", includeDeleted: false, limit: 100, offset: 0 }, locale),
  ]);
  // Portées proposées = portées où l'API accepte une création (lues dans les quotas) ; l'API revérifie.
  const scopes: ScopeOption[] = [];
  for (const s of quotas.ok ? quotas.data.scopes : []) {
    if (s.scope === "organization" && s.organization_id) {
      const name = me.roles.find((r) => r.organization_id === s.organization_id)?.organization_name;
      scopes.push({
        value: s.organization_id,
        label: `${t.quotas.unknownOrg} : ${name ?? s.organization_id.slice(0, 8)}`,
      });
    }
    if (s.scope === "platform") scopes.unshift({ value: "", label: t.upload.scopePublic });
  }
  if (!scopes.length) scopes.push({ value: "", label: t.upload.scopePublic });
  const videoOptions = (videos.ok ? videos.data : [])
    .filter((v) => !isBlocked(v.state))
    .map((v) => ({ id: v.id, label: `${v.display_name} — ${t.states[v.state]}` }));

  return (
    <div className="space-y-6">
      <Breadcrumbs items={crumbs} label={base.a11y.breadcrumb} />
      <header className="space-y-2">
        <h1 className="text-3xl font-bold">{t.upload.title}</h1>
        <p className="max-w-prose text-fg-muted">{t.upload.lead}</p>
      </header>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />
      {!me.sensitive_fresh ? <ReauthCard locale={locale} returnTo={path} t={t} /> : null}
      <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
        <section className="min-w-0 rounded-lg border border-border bg-surface-raised p-6">
          <MediaUploader
            locale={locale}
            scopes={scopes}
            videos={videoOptions}
            detailBase={`/${locale}/edition/medias`}
          />
        </section>
        <aside>
          <QuotaPanel quotas={quotas.ok ? quotas.data : null} me={me} locale={locale} t={t} />
        </aside>
      </div>
    </div>
  );
}
