import Link from "next/link";

import { Breadcrumbs } from "@/components/Breadcrumbs";
import { FormStatus } from "@/components/Form";
import { Notice } from "@/components/Notice";
import { Pagination } from "@/components/Pagination";
import { QuotaPanel } from "@/components/media/QuotaPanel";
import { FlagBadge, StateBadge } from "@/components/media/StateBadge";
import { edition, type LibraryQuery } from "@/lib/edition";
import { editorOrRedirect } from "@/lib/edition-guard";
import { getDictionary } from "@/lib/i18n";
import { getMediaDictionary } from "@/lib/i18n-media";
import { MEDIA_KINDS, MEDIA_STATES, formatBytes, isMediaKind, isMediaState } from "@/lib/media";
import { localeFrom, single, statusParams, type SearchParams } from "@/lib/route";

const LIMIT = 20;

export default async function MediaLibrary({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const path = `/${locale}/edition/medias`;
  const { me, allowed } = await editorOrRedirect(locale, path);
  const t = getMediaDictionary(locale);
  const base = getDictionary(locale);
  const query = await searchParams;
  const { status, error } = statusParams(query);
  const crumbs = [
    { label: base.crumbs.home, href: `/${locale}` },
    { label: t.hub.title, href: `/${locale}/edition` },
    { label: t.library.title },
  ];
  if (!allowed) {
    return (
      <div className="space-y-6">
        <Breadcrumbs items={crumbs} label={base.a11y.breadcrumb} />
        <h1 className="text-3xl font-bold">{t.library.title}</h1>
        <Notice tone="warning">{t.hub.forbidden}</Notice>
      </div>
    );
  }

  // Paramètres validés (liste fermée) : aucune valeur arbitraire n'est transmise à l'API.
  const rawQ = (single(query.q) ?? "").slice(0, 100);
  const kind = single(query.format);
  const state = single(query.analyse);
  const offsetRaw = Number(single(query.debut) ?? "0");
  const filter: LibraryQuery = {
    q: rawQ.trim() || undefined,
    kind: isMediaKind(kind) ? kind : undefined,
    state: isMediaState(state) ? state : undefined,
    includeDeleted: single(query.supprimes) === "1",
    limit: LIMIT,
    offset: Number.isInteger(offsetRaw) && offsetRaw >= 0 && offsetRaw <= 100000 ? offsetRaw : 0,
  };
  const [list, quotas] = await Promise.all([edition.library(filter, locale), edition.quotas(locale)]);
  const items = list.ok ? list.data : [];
  const total = list.ok ? (list.total ?? items.length) : 0;
  const date = new Intl.DateTimeFormat(locale === "fr" ? "fr-CA" : "en-CA", { dateStyle: "medium" });
  const href = (offset: number) => {
    const p = new URLSearchParams();
    if (filter.q) p.set("q", filter.q);
    if (filter.kind) p.set("format", filter.kind);
    if (filter.state) p.set("analyse", filter.state);
    if (filter.includeDeleted) p.set("supprimes", "1");
    if (offset) p.set("debut", String(offset));
    const s = p.toString();
    return s ? `${path}?${s}` : path;
  };
  const control =
    "block min-h-11 w-full rounded-md border border-border-strong bg-bg px-3 text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

  return (
    <div className="space-y-6">
      <Breadcrumbs items={crumbs} label={base.a11y.breadcrumb} />
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold">{t.library.title}</h1>
          <p className="max-w-prose text-fg-muted">{t.library.lead}</p>
        </div>
        <Link
          href={`/${locale}/edition/medias/televerser`}
          className="inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          data-testid="library-upload"
        >
          {t.library.uploadCta}
        </Link>
      </header>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />

      <div className="grid gap-6 lg:grid-cols-[1fr_18rem]">
        <div className="min-w-0 space-y-4">
          <form
            method="get"
            action={path}
            role="search"
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
            data-testid="library-filters"
          >
            <div className="space-y-1 sm:col-span-2">
              <label htmlFor="f-q" className="block font-semibold">
                {t.library.search}
              </label>
              <input
                id="f-q"
                name="q"
                type="search"
                maxLength={100}
                defaultValue={filter.q ?? ""}
                className={control}
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="f-format" className="block font-semibold">
                {t.library.kind}
              </label>
              <select id="f-format" name="format" defaultValue={filter.kind ?? ""} className={control}>
                <option value="">{t.library.any}</option>
                {MEDIA_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {t.kinds[k]}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <label htmlFor="f-analyse" className="block font-semibold">
                {t.library.state}
              </label>
              <select id="f-analyse" name="analyse" defaultValue={filter.state ?? ""} className={control}>
                <option value="">{t.library.any}</option>
                {MEDIA_STATES.map((s) => (
                  <option key={s} value={s}>
                    {t.states[s]}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 sm:col-span-2">
              <input
                id="f-supprimes"
                name="supprimes"
                type="checkbox"
                value="1"
                defaultChecked={filter.includeDeleted}
                className="h-5 w-5 accent-[var(--color-primary)]"
              />
              <label htmlFor="f-supprimes">{t.library.includeDeleted}</label>
            </div>
            <div className="flex items-end gap-3 sm:col-span-2">
              <button
                type="submit"
                className="inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                data-testid="library-apply"
              >
                {t.library.apply}
              </button>
              <Link href={path} className="inline-flex min-h-11 items-center px-2 underline underline-offset-2">
                {t.library.reset}
              </Link>
            </div>
          </form>

          <p role="status" className="text-fg-muted" data-testid="library-count">
            {t.library.results(total)}
          </p>

          {items.length ? (
            <div
              className="overflow-x-auto rounded-lg border border-border focus-visible:outline-2 focus-visible:outline-primary"
              tabIndex={0}
              role="region"
              aria-label={t.library.caption}
            >
              <table className="w-full min-w-[40rem] text-left text-sm" data-testid="library-table">
                <caption className="sr-only">{t.library.caption}</caption>
                <thead className="bg-surface">
                  <tr>
                    <th scope="col" className="px-3 py-2">
                      {t.library.cols.name}
                    </th>
                    <th scope="col" className="px-3 py-2">
                      {t.library.cols.kind}
                    </th>
                    <th scope="col" className="px-3 py-2">
                      {t.library.cols.size}
                    </th>
                    <th scope="col" className="px-3 py-2">
                      {t.library.cols.date}
                    </th>
                    <th scope="col" className="px-3 py-2">
                      {t.library.cols.state}
                    </th>
                    <th scope="col" className="px-3 py-2">
                      {t.library.cols.attachments}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {items.map((m) => (
                    <tr key={m.id} data-testid="library-row" data-media={m.id}>
                      <th scope="row" className="px-3 py-2 font-medium">
                        <Link
                          href={`${path}/${m.id}`}
                          className="text-primary underline-offset-2 hover:underline"
                          aria-label={t.library.open(m.display_name)}
                        >
                          {m.display_name}
                        </Link>
                        <span className="mt-1 flex flex-wrap gap-1">
                          {m.legal_hold ? <FlagBadge testId="badge-hold">{t.badges.legalHold}</FlagBadge> : null}
                          {m.deleted_at ? <FlagBadge testId="badge-deleted">{t.badges.deleted}</FlagBadge> : null}
                        </span>
                      </th>
                      <td className="px-3 py-2">{t.kinds[m.kind]}</td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {formatBytes(m.byte_size ?? m.declared_bytes, locale)}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <time dateTime={m.created_at}>{date.format(new Date(m.created_at))}</time>
                      </td>
                      <td className="px-3 py-2">
                        <StateBadge state={m.state} t={t} />
                      </td>
                      <td className="px-3 py-2">{t.library.attachments(m.attachment_count ?? 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="rounded-lg border border-border bg-surface p-6 text-fg-muted" data-testid="library-empty">
              {t.library.empty}
            </p>
          )}
          <Pagination total={total} limit={LIMIT} offset={filter.offset} buildHref={href} labels={t.pagination} />
        </div>
        <aside>
          <QuotaPanel quotas={quotas.ok ? quotas.data : null} me={me} locale={locale} t={t} />
        </aside>
      </div>
    </div>
  );
}
