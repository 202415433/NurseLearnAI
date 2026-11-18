import Link from "next/link";

// Pagination par liens (fonctionne sans JavaScript). buildHref reçoit le nouvel offset.
export function Pagination({
  total,
  limit,
  offset,
  buildHref,
  labels,
}: {
  total: number;
  limit: number;
  offset: number;
  buildHref: (offset: number) => string;
  labels: { label: string; previous: string; next: string; pageOf: (page: number, pages: number) => string };
}) {
  const pages = Math.max(1, Math.ceil(total / limit));
  if (pages <= 1) return null;
  const page = Math.floor(offset / limit) + 1;
  const linkClass = "inline-flex min-h-11 items-center rounded-md border border-border px-4 hover:border-primary";
  return (
    <nav aria-label={labels.label} className="mt-6 flex items-center justify-between gap-4">
      {page > 1 ? (
        <Link href={buildHref(Math.max(0, offset - limit))} rel="prev" className={linkClass}>
          {labels.previous}
        </Link>
      ) : (
        <span />
      )}
      <span className="text-fg-muted">{labels.pageOf(page, pages)}</span>
      {page < pages ? (
        <Link href={buildHref(offset + limit)} rel="next" className={linkClass}>
          {labels.next}
        </Link>
      ) : (
        <span />
      )}
    </nav>
  );
}
