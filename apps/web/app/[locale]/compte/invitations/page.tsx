import { notFound, redirect } from "next/navigation";

import { Field, FormCard, FormStatus, SubmitButton } from "@/components/Form";
import { createInvitationAction, revokeInvitationAction } from "@/lib/actions";
import { callApi, canInvite, currentUser, sessionToken } from "@/lib/auth";
import { getDictionary, locales } from "@/lib/i18n";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

type Organization = { id: string; slug: string; name: string; roles: string[] };
type Invitation = {
  id: string;
  email: string;
  role: string;
  organization_id: string | null;
  status: string;
  expires_at: string;
};

const selectClass =
  "block min-h-11 w-full rounded-md border border-border-strong bg-bg px-3 text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

// Interface d'invitation : le formulaire ne propose que ce que l'API autorisera (l'API reste
// l'autorité : un administrateur d'établissement n'invite que des formateurs dans son établissement).
export default async function InvitationsPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const me = await currentUser();
  if (!me) redirect(`/${locale}/connexion?suite=${encodeURIComponent(`/${locale}/compte/invitations`)}`);
  if (!canInvite(me)) notFound();
  const t = getDictionary(locale).auth;
  const { status, error } = statusParams(await searchParams);
  const token = await sessionToken();
  const platformAdmin = me.permissions.includes("user:manage");

  let organizations: Organization[] = [];
  if (platformAdmin) {
    const all = await callApi<Organization[]>("/api/v1/admin/organizations", { method: "GET", token });
    organizations = all.ok && Array.isArray(all.data) ? all.data : [];
  } else {
    const mine = await callApi<Organization[]>("/api/v1/organizations/mine", { method: "GET", token });
    organizations = (mine.ok && Array.isArray(mine.data) ? mine.data : []).filter((o) => o.roles.includes("org_admin"));
  }
  const roles = platformAdmin ? ["instructor", "medical_reviewer", "org_admin"] : ["instructor"];

  const lists: { title: string; items: Invitation[] }[] = [];
  if (platformAdmin) {
    const global = await callApi<Invitation[]>("/api/v1/invitations", { method: "GET", token });
    if (global.ok && Array.isArray(global.data)) lists.push({ title: t.invitations.global, items: global.data });
  }
  for (const org of organizations) {
    const result = await callApi<Invitation[]>(`/api/v1/organizations/${org.id}/invitations`, { method: "GET", token });
    if (result.ok && Array.isArray(result.data)) lists.push({ title: org.name, items: result.data });
  }
  const date = new Intl.DateTimeFormat(locale === "fr" ? "fr-CA" : "en-CA", {
    dateStyle: "medium",
    timeStyle: "short",
  });

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <h1 className="text-3xl font-bold">{t.invitations.title}</h1>
      <p className="text-fg-muted">{t.invitations.lead}</p>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />

      <FormCard title={t.invitations.submit}>
        <form action={createInvitationAction} className="space-y-4" data-testid="invite-form">
          <input type="hidden" name="locale" value={locale} />
          <Field name="email" type="email" label={t.fields.email} autoComplete="off" maxLength={254} />
          <div className="space-y-1">
            <label htmlFor="role" className="block font-semibold">
              {t.fields.role}
            </label>
            <select id="role" name="role" className={selectClass} data-testid="invite-role">
              {roles.map((r) => (
                <option key={r} value={r}>
                  {t.account.roleNames[r] ?? r}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label htmlFor="organization_id" className="block font-semibold">
              {t.fields.organization}
            </label>
            <select id="organization_id" name="organization_id" className={selectClass} data-testid="invite-org">
              {platformAdmin ? <option value="">{t.invitations.global}</option> : null}
              {organizations.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <label htmlFor="invite_locale" className="block font-semibold">
              {t.fields.language}
            </label>
            <select id="invite_locale" name="invite_locale" defaultValue={locale} className={selectClass}>
              {locales.map((l) => (
                <option key={l} value={l}>
                  {l === "fr" ? "Français" : "English"}
                </option>
              ))}
            </select>
          </div>
          <SubmitButton testId="invite-submit">{t.invitations.submit}</SubmitButton>
        </form>
      </FormCard>

      <section className="space-y-6" aria-labelledby="invitations-list">
        <h2 id="invitations-list" className="text-2xl font-semibold">
          {t.invitations.listTitle}
        </h2>
        {lists.map((list) => (
          <div key={list.title} className="space-y-3">
            <h3 className="text-lg font-semibold">{list.title}</h3>
            {list.items.length === 0 ? (
              <p className="text-fg-muted">{t.invitations.none}</p>
            ) : (
              <ul className="space-y-3" data-testid="invitation-list">
                {list.items.map((inv) => (
                  <li
                    key={inv.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3"
                  >
                    <div>
                      <p className="font-semibold">{inv.email}</p>
                      <p className="text-sm text-fg-muted">
                        {t.account.roleNames[inv.role] ?? inv.role} · {t.invitations.status[inv.status] ?? inv.status} ·{" "}
                        {t.invitations.expires} {date.format(new Date(inv.expires_at))}
                      </p>
                    </div>
                    {inv.status === "pending" ? (
                      <form action={revokeInvitationAction}>
                        <input type="hidden" name="locale" value={locale} />
                        <input type="hidden" name="invitation_id" value={inv.id} />
                        <SubmitButton variant="danger">{t.invitations.revoke}</SubmitButton>
                      </form>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </section>
    </div>
  );
}
