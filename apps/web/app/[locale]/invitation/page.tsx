import Link from "next/link";

import { FormStatus, SubmitButton } from "@/components/Form";
import { acceptInvitationAction } from "@/lib/actions";
import { callApi, currentUser, sessionToken } from "@/lib/auth";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

type Preview = { role: string; organization_name: string | null; expires_at: string };

// L'invitation s'accepte une fois connecté(e), avec l'adresse vérifiée qui l'a reçue.
export default async function InvitationPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale).auth;
  const { error, token } = statusParams(await searchParams);
  const me = await currentUser();
  const here = `/${locale}/invitation${token ? `?jeton=${token}` : ""}`;

  let preview: Preview | null = null;
  let invalid = false;
  if (me && token) {
    const result = await callApi<Preview>("/api/v1/invitations/inspect", {
      token: await sessionToken(),
      body: { token },
    });
    if (result.ok) preview = result.data;
    else invalid = true;
  }

  return (
    <div className="mx-auto max-w-xl space-y-6">
      <h1 className="text-3xl font-bold">{t.accept.title}</h1>
      <FormStatus
        error={error ?? (invalid || (!token && !error) ? "invalid_link" : undefined)}
        messages={t.messages}
        errors={t.errors}
      />
      {!me && token ? (
        <p>
          {t.accept.mustLogin}{" "}
          <Link href={`/${locale}/connexion?suite=${encodeURIComponent(here)}`} className="font-semibold underline">
            {t.nav.signIn}
          </Link>
        </p>
      ) : null}
      {preview && token ? (
        <form action={acceptInvitationAction} className="space-y-4" data-testid="accept-form">
          <p>{t.accept.lead}</p>
          <p className="text-xl font-semibold" data-testid="invited-role">
            {t.account.roleNames[preview.role] ?? preview.role}
            {preview.organization_name ? ` — ${preview.organization_name}` : ""}
          </p>
          <input type="hidden" name="locale" value={locale} />
          <input type="hidden" name="token" value={token} />
          <SubmitButton testId="accept-submit">{t.accept.accept}</SubmitButton>
        </form>
      ) : null}
    </div>
  );
}
