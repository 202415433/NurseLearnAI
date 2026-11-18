import Link from "next/link";

export type Crumb = { label: string; href?: string };

// Fil d'Ariane accessible : la dernière étape est la page courante (aria-current).
export function Breadcrumbs({ items, label }: { items: Crumb[]; label: string }) {
  return (
    <nav aria-label={label} className="mb-6 text-sm">
      <ol className="flex flex-wrap items-center gap-1 text-fg-muted">
        {items.map((item, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${item.label}-${i}`} className="flex items-center gap-1">
              {item.href && !last ? (
                <Link href={item.href} className="underline underline-offset-2 hover:text-primary">
                  {item.label}
                </Link>
              ) : (
                <span aria-current={last ? "page" : undefined} className={last ? "font-semibold text-fg" : undefined}>
                  {item.label}
                </span>
              )}
              {!last ? <span aria-hidden="true">›</span> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
