import Link from "next/link";
import { redirect } from "next/navigation";

import { Field, FormCard, FormStatus, SubmitButton } from "@/components/Form";
import {
  changePasswordAction,
  logoutAction,
  logoutAllAction,
  reauthenticateAction,
  revokeSessionAction,
  updateProfileAction,
} from "@/lib/actions";
import { callApi, canInvite, currentUser, sessionToken } from "@/lib/auth";
import { getDictionary, locales } from "@/lib/i18n";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

type DeviceSession = {
  id: string;
  created_at: string;
  last_seen_at: string;
  user_agent: string | null;
  ip_hint: string | null;
  current: boolean;
};

export default async function AccountPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const me = await currentUser();
  if (!me) redirect(`/${locale}/connexion?suite=${encodeURIComponent(`/${locale}/compte`)}`);
  const t = getDictionary(locale).auth;
  const { status, error } = statusParams(await searchParams);
  const sessions = await callApi<DeviceSession[]>("/api/v1/me/sessions", {
    method: "GET",
    token: await sessionToken(),
  });
  const devices = sessions.ok && Array.isArray(sessions.data) ? sessions.data : [];
  const date = new Intl.DateTimeFormat(locale === "fr" ? "fr-CA" : "en-CA", {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const needsReauth = !me.sensitive_fresh || error === "reauth";

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <h1 className="text-3xl font-bold">{t.account.title}</h1>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />

      {needsReauth ? (
        <FormCard title={t.account.reauthTitle}>
          <form action={reauthenticateAction} className="space-y-4" data-testid="reauth-form">
            <p className="text-fg-muted">{t.account.reauthLead}</p>
            <input type="hidden" name="locale" value={locale} />
            <Field
              id="reauth-password"
              name="password"
              type="password"
              label={t.fields.password}
              autoComplete="current-password"
              maxLength={1024}
            />
            <SubmitButton variant="secondary" testId="reauth-submit">
              {t.account.reauthSubmit}
            </SubmitButton>
          </form>
        </FormCard>
      ) : null}

      <FormCard title={t.account.profile}>
        <p className="mb-4" data-testid="account-email">
          {me.email}
        </p>
        <form action={updateProfileAction} className="space-y-4">
          <input type="hidden" name="locale" value={locale} />
          <Field
            name="display_name"
            label={t.fields.displayName}
            defaultValue={me.display_name}
            autoComplete="name"
            maxLength={100}
          />
          <div className="space-y-1">
            <label htmlFor="preferred_locale" className="block font-semibold">
              {t.fields.language}
            </label>
            <select
              id="preferred_locale"
              name="preferred_locale"
              defaultValue={me.locale}
              className="block min-h-11 rounded-md border border-border-strong bg-bg px-3 text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              {locales.map((l) => (
                <option key={l} value={l}>
                  {l === "fr" ? "Français" : "English"}
                </option>
              ))}
            </select>
          </div>
          <SubmitButton variant="secondary">{t.account.save}</SubmitButton>
        </form>
      </FormCard>

      <FormCard title={t.account.roles}>
        <ul className="list-disc space-y-1 pl-6" data-testid="account-roles">
          {me.roles.map((r) => (
            <li key={`${r.role}-${r.organization_id ?? "global"}`} data-role={r.role}>
              {t.account.roleNames[r.role] ?? r.role}
              {r.organization_name ? ` — ${r.organization_name}` : ""}
            </li>
          ))}
        </ul>
        {canInvite(me) ? (
          <p className="mt-4">
            <Link
              href={`/${locale}/compte/invitations`}
              className="font-semibold underline"
              data-testid="invitations-link"
            >
              {t.account.invitations}
            </Link>
          </p>
        ) : null}
      </FormCard>

      <FormCard title={t.account.sessions}>
        <ul className="space-y-3" data-testid="session-list">
          {devices.map((d) => (
            <li
              key={d.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3"
            >
              <div>
                <p className="font-semibold">
                  {d.user_agent ? d.user_agent.slice(0, 80) : t.account.unknownDevice}
                  {d.current ? ` (${t.account.current})` : ""}
                </p>
                <p className="text-sm text-fg-muted">
                  {t.account.lastSeen} : {date.format(new Date(d.last_seen_at))}
                  {d.ip_hint ? ` · ${d.ip_hint}` : ""}
                </p>
              </div>
              {d.current ? null : (
                <form action={revokeSessionAction}>
                  <input type="hidden" name="locale" value={locale} />
                  <input type="hidden" name="session_id" value={d.id} />
                  <SubmitButton variant="danger">{t.account.closeSession}</SubmitButton>
                </form>
              )}
            </li>
          ))}
        </ul>
        <div className="mt-4 flex flex-wrap gap-3">
          <form action={logoutAction}>
            <input type="hidden" name="locale" value={locale} />
            <SubmitButton variant="secondary" testId="logout">
              {t.account.logout}
            </SubmitButton>
          </form>
          <form action={logoutAllAction}>
            <input type="hidden" name="locale" value={locale} />
            <SubmitButton variant="danger" testId="logout-all">
              {t.account.logoutAll}
            </SubmitButton>
          </form>
        </div>
      </FormCard>

      <FormCard title={t.account.passwordTitle}>
        <form action={changePasswordAction} className="space-y-4" data-testid="change-password-form">
          <input type="hidden" name="locale" value={locale} />
          <Field
            name="current_password"
            type="password"
            label={t.fields.currentPassword}
            autoComplete="current-password"
            maxLength={1024}
          />
          <Field
            name="new_password"
            type="password"
            label={t.fields.newPassword}
            autoComplete="new-password"
            hint={t.fields.passwordHint}
            minLength={12}
            maxLength={128}
          />
          <SubmitButton variant="secondary">{t.account.changePassword}</SubmitButton>
        </form>
      </FormCard>
    </div>
  );
}
