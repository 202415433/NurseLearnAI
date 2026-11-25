import type { Me } from "@/lib/auth";
import type { MediaDictionary } from "@/lib/i18n-media";
import { FILE_LIMITS, MEDIA_KINDS, formatBytes, type Quotas } from "@/lib/media";

// Occupation du stockage (lecture seule) et limites par fichier. <meter> : valeur annoncée aux
// technologies d'assistance, doublée d'un texte explicite.
export function QuotaPanel({
  quotas,
  me,
  locale,
  t,
}: {
  quotas: Quotas | null;
  me: Me;
  locale: string;
  t: MediaDictionary;
}) {
  const orgName = (id: string | null) =>
    me.roles.find((r) => r.organization_id === id)?.organization_name ?? t.quotas.unknownOrg;
  const limits = quotas?.file_limits ?? FILE_LIMITS;
  return (
    <section
      aria-labelledby="quotas-titre"
      className="rounded-lg border border-border bg-surface p-5"
      data-testid="quotas"
    >
      <h2 id="quotas-titre" className="mb-3 text-lg font-semibold">
        {t.quotas.title}
      </h2>
      {quotas ? (
        <ul className="space-y-3">
          {quotas.scopes.map((s) => {
            const label =
              s.scope === "user"
                ? t.quotas.user
                : s.scope === "platform"
                  ? t.quotas.platform
                  : t.quotas.organization(orgName(s.organization_id));
            const used = s.used_bytes + s.reserved_bytes;
            const id = `quota-${s.scope}-${s.organization_id ?? "x"}`;
            return (
              <li key={id} data-testid="quota-scope" data-scope={s.scope}>
                <label htmlFor={id} className="block font-medium">
                  {label}
                </label>
                <meter
                  id={id}
                  min={0}
                  max={s.quota_bytes}
                  low={s.quota_bytes * 0.75}
                  high={s.quota_bytes * 0.9}
                  optimum={0}
                  value={Math.min(used, s.quota_bytes)}
                  className="h-3 w-full"
                />
                <p className="text-sm text-fg-muted">
                  {t.quotas.usage(formatBytes(used, locale), formatBytes(s.quota_bytes, locale))}
                  {s.reserved_bytes > 0 ? ` — ${t.quotas.reserved(formatBytes(s.reserved_bytes, locale))}` : ""}
                </p>
              </li>
            );
          })}
        </ul>
      ) : null}
      <h3 className="mt-4 font-semibold">{t.quotas.limitsTitle}</h3>
      <ul className="mt-1 grid grid-cols-2 gap-x-4 text-sm text-fg-muted" data-testid="file-limits">
        {MEDIA_KINDS.map((k) => (
          <li key={k}>
            {t.kinds[k]}
            {locale === "fr" ? "\u00a0: " : ": "}
            {formatBytes(limits[k], locale)}
          </li>
        ))}
      </ul>
    </section>
  );
}
