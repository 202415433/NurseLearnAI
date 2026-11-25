import Link from "next/link";
import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/Breadcrumbs";
import { FormCard, FormStatus, SubmitButton } from "@/components/Form";
import { Notice } from "@/components/Notice";
import { MediaPlayer } from "@/components/media/MediaPlayer";
import { ReauthCard } from "@/components/media/ReauthCard";
import { ScanWatcher } from "@/components/media/ScanWatcher";
import { FlagBadge, StateBadge } from "@/components/media/StateBadge";
import { canHold, canRevoke, edition } from "@/lib/edition";
import { editorOrRedirect } from "@/lib/edition-guard";
import { getDictionary } from "@/lib/i18n";
import { getMediaDictionary } from "@/lib/i18n-media";
import { deleteMediaAction, legalHoldAction, revokeMediaAction } from "@/lib/media-actions";
import { formatBytes, isAvailable, isAwaitingScan, isUuid } from "@/lib/media";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

export default async function MediaDetail({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string; id: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params.then((p) => ({ locale: p.locale })));
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const path = `/${locale}/edition/medias/${id}`;
  const { me, allowed } = await editorOrRedirect(locale, path);
  const t = getMediaDictionary(locale);
  const base = getDictionary(locale);
  const { status, error } = statusParams(await searchParams);
  if (!allowed) {
    return <Notice tone="warning">{t.hub.forbidden}</Notice>;
  }
  const media = await edition.media(id, locale);
  if (!media.ok) notFound(); // hors portée ou inexistant : même réponse (existence non révélée)
  const m = media.data;
  const hold = canHold(me);
  const [history, captionsList] = await Promise.all([
    hold ? edition.holdHistory(id, locale) : Promise.resolve(null),
    m.kind === "video"
      ? edition.library({ kind: "captions", state: "clean", includeDeleted: false, limit: 100, offset: 0 }, locale)
      : Promise.resolve(null),
  ]);
  const captions = (captionsList?.ok ? captionsList.data : [])
    .filter((c) => c.captions_for === m.id)
    .map((c) => ({ id: c.id, srclang: locale, label: c.display_name }));
  const date = new Intl.DateTimeFormat(locale === "fr" ? "fr-CA" : "en-CA", {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const revocable = canRevoke(me) && m.state !== "revoked" && m.state !== "pending_upload";
  const deletable = !m.deleted_at;
  const sensitive = revocable || hold || deletable;
  const org = m.organization_id
    ? (me.roles.find((r) => r.organization_id === m.organization_id)?.organization_name ?? t.detail.meta.organization)
    : t.detail.meta.public;
  const crumbs = [
    { label: base.crumbs.home, href: `/${locale}` },
    { label: t.hub.title, href: `/${locale}/edition` },
    { label: t.library.title, href: `/${locale}/edition/medias` },
    { label: m.display_name },
  ];
  const row = (label: string, value: React.ReactNode) => (
    <div className="grid grid-cols-[10rem_1fr] gap-2 py-1">
      <dt className="font-medium text-fg-muted">{label}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
  const textarea =
    "block min-h-24 w-full rounded-md border border-border-strong bg-bg p-3 text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

  return (
    <div className="space-y-6">
      <ScanWatcher active={isAwaitingScan(m.state) && !m.deleted_at} />
      <Breadcrumbs items={crumbs} label={base.a11y.breadcrumb} />
      <header className="space-y-3">
        <h1 className="text-3xl font-bold break-words">{m.display_name}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <StateBadge state={m.state} t={t} />
          {m.legal_hold ? <FlagBadge testId="badge-hold">{t.badges.legalHold}</FlagBadge> : null}
          {m.deleted_at ? <FlagBadge testId="badge-deleted">{t.badges.deleted}</FlagBadge> : null}
        </div>
        <p className="text-fg-muted" data-testid="state-help" aria-live="polite">
          {t.stateHelp[m.state]}
        </p>
      </header>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />

      <div className="grid gap-6 lg:grid-cols-2">
        <section aria-labelledby="apercu-titre" className="space-y-3">
          <h2 id="apercu-titre" className="text-xl font-semibold">
            {t.detail.preview}
          </h2>
          {isAvailable(m) ? (
            <MediaPlayer media={m} source={{ mode: "preview" }} captions={captions} locale={locale} />
          ) : (
            <Notice tone="warning">{t.detail.previewBlocked}</Notice>
          )}
        </section>
        <section aria-labelledby="meta-titre" className="rounded-lg border border-border bg-surface p-5">
          <h2 id="meta-titre" className="mb-2 text-xl font-semibold">
            {t.detail.title}
          </h2>
          <dl data-testid="media-meta">
            {row(t.detail.meta.kind, t.kinds[m.kind])}
            {row(t.detail.meta.mime, <code>{m.mime_type}</code>)}
            {row(t.detail.meta.size, formatBytes(m.byte_size ?? m.declared_bytes, locale))}
            {row(t.detail.meta.scope, org)}
            {row(t.detail.meta.license, m.license)}
            {m.attribution ? row(t.detail.meta.attribution, m.attribution) : null}
            {m.alt_text ? row(t.detail.meta.alt, m.alt_text[locale] ?? m.alt_text.fr ?? "") : null}
            {row(t.detail.meta.created, <time dateTime={m.created_at}>{date.format(new Date(m.created_at))}</time>)}
            {row(t.detail.meta.updated, <time dateTime={m.updated_at}>{date.format(new Date(m.updated_at))}</time>)}
          </dl>
        </section>
      </div>

      {sensitive && !me.sensitive_fresh ? <ReauthCard locale={locale} returnTo={path} t={t} /> : null}

      {revocable ? (
        <FormCard title={t.detail.revokeTitle}>
          <form action={revokeMediaAction} className="space-y-3" data-testid="revoke-form">
            <p className="text-fg-muted">{t.detail.revokeLead}</p>
            <input type="hidden" name="locale" value={locale} />
            <input type="hidden" name="retour" value={path} />
            <input type="hidden" name="media" value={m.id} />
            <label htmlFor="revoke-reason" className="block font-semibold">
              {t.detail.reason}
            </label>
            <textarea id="revoke-reason" name="reason" required minLength={5} maxLength={1000} className={textarea} />
            <SubmitButton variant="danger" testId="revoke-submit">
              {t.detail.revokeSubmit}
            </SubmitButton>
          </form>
        </FormCard>
      ) : null}

      {hold ? (
        <FormCard title={t.detail.holdTitle}>
          <form action={legalHoldAction} className="space-y-3" data-testid="hold-form">
            <p className="text-fg-muted">{t.detail.holdLead}</p>
            <input type="hidden" name="locale" value={locale} />
            <input type="hidden" name="retour" value={path} />
            <input type="hidden" name="media" value={m.id} />
            <input type="hidden" name="hold" value={m.legal_hold ? "false" : "true"} />
            <label htmlFor="hold-reason" className="block font-semibold">
              {t.detail.reason}
            </label>
            <textarea id="hold-reason" name="reason" required minLength={5} maxLength={1000} className={textarea} />
            <SubmitButton variant="secondary" testId="hold-submit">
              {m.legal_hold ? t.detail.holdRelease : t.detail.holdSet}
            </SubmitButton>
          </form>
          <h3 className="mt-6 mb-2 font-semibold">{t.detail.holdHistory}</h3>
          {history?.ok && history.data.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm" data-testid="hold-history">
                <thead>
                  <tr>
                    <th scope="col" className="py-1 pr-3">
                      {t.detail.holdCols.date}
                    </th>
                    <th scope="col" className="py-1 pr-3">
                      {t.detail.holdCols.action}
                    </th>
                    <th scope="col" className="py-1 pr-3">
                      {t.detail.holdCols.actor}
                    </th>
                    <th scope="col" className="py-1">
                      {t.detail.holdCols.reason}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {history.data.map((e) => (
                    <tr key={e.id}>
                      <td className="py-1 pr-3 whitespace-nowrap">
                        <time dateTime={e.created_at}>{date.format(new Date(e.created_at))}</time>
                      </td>
                      <td className="py-1 pr-3">{t.detail.holdActions[e.action] ?? e.action}</td>
                      <td className="py-1 pr-3">{e.actor_name ?? "—"}</td>
                      <td className="py-1 break-words">{e.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-fg-muted">{t.detail.holdEmpty}</p>
          )}
        </FormCard>
      ) : null}

      {deletable ? (
        <FormCard title={t.detail.deleteTitle}>
          <form action={deleteMediaAction} className="space-y-3" data-testid="delete-form">
            <p className="text-fg-muted">{t.detail.deleteLead}</p>
            <input type="hidden" name="locale" value={locale} />
            <input type="hidden" name="retour" value={path} />
            <input type="hidden" name="media" value={m.id} />
            <SubmitButton variant="danger" testId="delete-submit">
              {t.detail.deleteSubmit}
            </SubmitButton>
          </form>
        </FormCard>
      ) : null}

      <p>
        <Link href={`/${locale}/edition/medias`} className="underline underline-offset-2">
          {t.detail.back}
        </Link>
      </p>
    </div>
  );
}
