import Link from "next/link";
import { notFound } from "next/navigation";

import { Breadcrumbs } from "@/components/Breadcrumbs";
import { FormStatus } from "@/components/Form";
import { Notice } from "@/components/Notice";
import { MediaPlayer } from "@/components/media/MediaPlayer";
import { StateBadge } from "@/components/media/StateBadge";
import { edition } from "@/lib/edition";
import { editorOrRedirect } from "@/lib/edition-guard";
import { getDictionary } from "@/lib/i18n";
import { getMediaDictionary } from "@/lib/i18n-media";
import { attachMediaAction, detachMediaAction } from "@/lib/media-actions";
import { MEDIA_KINDS, formatBytes, isBlocked, isMediaKind, isUuid } from "@/lib/media";
import { localeFrom, single, statusParams, type SearchParams } from "@/lib/route";

// Sélecteur de médias de l'éditeur pédagogique : rattacher et retirer des médias d'un BROUILLON de
// version de leçon. Une version soumise, approuvée ou publiée est affichée en lecture seule (figée).
// Les médias proposés sont ceux de la même portée que la leçon ; l'API revérifie tout (0010).
export default async function VersionMedia({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string; id: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const { id } = await params;
  if (!isUuid(id)) notFound();
  const path = `/${locale}/edition/versions/${id}/medias`;
  const { allowed } = await editorOrRedirect(locale, path);
  const t = getMediaDictionary(locale);
  const base = getDictionary(locale);
  if (!allowed) return <Notice tone="warning">{t.hub.forbidden}</Notice>;
  const query = await searchParams;
  const { status, error } = statusParams(query);
  const [version, attached] = await Promise.all([edition.version(id, locale), edition.versionMedia(id, locale)]);
  if (!version.ok || !attached.ok) notFound(); // hors portée : existence non révélée
  const v = version.data;
  const list = attached.data;
  const q = (single(query.q) ?? "").slice(0, 100).trim();
  const kindRaw = single(query.format);
  const kind = isMediaKind(kindRaw) ? kindRaw : undefined;
  const candidates = list.editable
    ? await edition.library({ q: q || undefined, kind, includeDeleted: false, limit: 30, offset: 0 }, locale)
    : null;
  // Même portée que la leçon seulement (l'API refuserait sinon avec media_scope) ; bloqués exclus.
  const offered = (candidates?.ok ? candidates.data : []).filter(
    (m) => m.organization_id === v.organization_id && m.state !== "pending_upload",
  );
  const attachedIds = new Set(list.items.map((i) => i.media_id));
  const field =
    "block min-h-11 w-full rounded-md border border-border-strong bg-bg px-3 text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";
  const hidden = (media: string) => (
    <>
      <input type="hidden" name="locale" value={locale} />
      <input type="hidden" name="retour" value={path} />
      <input type="hidden" name="version" value={id} />
      <input type="hidden" name="media" value={media} />
    </>
  );

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: base.crumbs.home, href: `/${locale}` },
          { label: t.hub.title, href: `/${locale}/edition` },
          { label: t.picker.title },
        ]}
        label={base.a11y.breadcrumb}
      />
      <header className="space-y-2">
        <h1 className="text-3xl font-bold">
          {t.picker.title} — {v.title}
        </h1>
        <p className="text-fg-muted">
          {t.picker.version(v.version_no, v.locale)} · {t.picker.status} :{" "}
          <span data-testid="version-status">{t.picker.statuses[v.status] ?? v.status}</span>
        </p>
        <p>
          <Link href={`/${locale}/edition/modules/${v.module_id}`} className="underline underline-offset-2">
            {t.nav.modules}
          </Link>
        </p>
      </header>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />
      {!list.editable ? (
        <div data-testid="version-frozen">
          <Notice tone="info">{t.picker.frozen}</Notice>
        </div>
      ) : null}

      <section aria-labelledby="rattaches-titre" className="space-y-3">
        <h2 id="rattaches-titre" className="text-xl font-semibold">
          {t.picker.attachedTitle}
        </h2>
        {list.items.length ? (
          <ul className="space-y-4" data-testid="attached-list">
            {list.items.map((item) => (
              <li
                key={item.media_id}
                className="rounded-lg border border-border p-4"
                data-testid="attached-item"
                data-media={item.media_id}
              >
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="space-y-1">
                    <p className="font-medium">
                      <Link
                        href={`/${locale}/edition/medias/${item.media_id}`}
                        className="text-primary underline-offset-2 hover:underline"
                      >
                        {item.display_name}
                      </Link>{" "}
                      <span className="text-sm text-fg-muted">
                        ({t.kinds[item.kind]}, {formatBytes(item.byte_size, locale)})
                      </span>
                    </p>
                    <StateBadge state={item.state} t={t} />
                  </div>
                  {list.editable ? (
                    <form action={detachMediaAction}>
                      {hidden(item.media_id)}
                      <button
                        type="submit"
                        className="inline-flex min-h-11 items-center rounded-md border border-danger bg-surface px-4 font-semibold text-danger hover:bg-danger-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                        data-testid="detach"
                      >
                        {t.picker.detach(item.display_name)}
                      </button>
                    </form>
                  ) : null}
                </div>
                {item.state === "clean" && item.kind !== "captions" ? (
                  <div className="mt-3">
                    <MediaPlayer
                      media={{
                        id: item.media_id,
                        kind: item.kind,
                        mime_type: item.mime_type,
                        display_name: item.display_name,
                        alt_text: item.alt_text,
                      }}
                      source={{ mode: "preview" }}
                      captions={
                        item.captions_id
                          ? [{ id: item.captions_id, srclang: v.locale, label: t.picker.captionsOf }]
                          : []
                      }
                      locale={locale}
                    />
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-fg-muted">{t.picker.attachedEmpty}</p>
        )}
      </section>

      {list.editable ? (
        <section aria-labelledby="ajout-titre" className="space-y-3">
          <h2 id="ajout-titre" className="text-xl font-semibold">
            {t.picker.searchTitle}
          </h2>
          <p className="max-w-prose text-fg-muted">{t.picker.searchLead}</p>
          <form
            method="get"
            action={path}
            role="search"
            className="grid gap-3 sm:grid-cols-[1fr_12rem_auto] sm:items-end"
          >
            <div className="space-y-1">
              <label htmlFor="p-q" className="block font-semibold">
                {t.library.search}
              </label>
              <input id="p-q" name="q" type="search" maxLength={100} defaultValue={q} className={field} />
            </div>
            <div className="space-y-1">
              <label htmlFor="p-format" className="block font-semibold">
                {t.library.kind}
              </label>
              <select id="p-format" name="format" defaultValue={kind ?? ""} className={field}>
                <option value="">{t.library.any}</option>
                {MEDIA_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {t.kinds[k]}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              className="inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              {t.library.apply}
            </button>
          </form>
          {offered.length ? (
            <ul className="divide-y divide-border rounded-lg border border-border" data-testid="candidate-list">
              {offered.map((m) => (
                <li
                  key={m.id}
                  className="flex flex-wrap items-center justify-between gap-3 p-3"
                  data-testid="candidate"
                  data-media={m.id}
                >
                  <div className="space-y-1">
                    <p className="font-medium">
                      {m.display_name}{" "}
                      <span className="text-sm text-fg-muted">
                        ({t.kinds[m.kind]}, {formatBytes(m.byte_size ?? m.declared_bytes, locale)})
                      </span>
                    </p>
                    <StateBadge state={m.state} t={t} />
                  </div>
                  {attachedIds.has(m.id) ? (
                    <span className="text-fg-muted">{t.picker.attached}</span>
                  ) : isBlocked(m.state) ? (
                    <span className="text-fg-muted">{t.picker.notAttachable}</span>
                  ) : (
                    <form action={attachMediaAction}>
                      {hidden(m.id)}
                      <button
                        type="submit"
                        className="inline-flex min-h-11 items-center rounded-md border border-border-strong bg-surface px-4 font-semibold hover:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                        data-testid="attach"
                      >
                        {t.picker.attach(m.display_name)}
                      </button>
                    </form>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-fg-muted">{t.picker.noResults}</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
