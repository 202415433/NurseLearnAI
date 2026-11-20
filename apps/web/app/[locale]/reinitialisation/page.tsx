import { Field, FormStatus, SubmitButton } from "@/components/Form";
import { resetPasswordAction } from "@/lib/actions";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

export default async function ResetPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale).auth;
  const { error, token } = statusParams(await searchParams);
  return (
    <div className="mx-auto max-w-md space-y-6">
      <h1 className="text-3xl font-bold">{t.reset.title}</h1>
      <FormStatus error={error} messages={t.messages} errors={t.errors} />
      {token ? (
        <form action={resetPasswordAction} className="space-y-5" data-testid="reset-form">
          <p className="text-fg-muted">{t.reset.lead}</p>
          <input type="hidden" name="locale" value={locale} />
          <input type="hidden" name="token" value={token} />
          <Field
            name="new_password"
            type="password"
            label={t.fields.newPassword}
            autoComplete="new-password"
            hint={t.fields.passwordHint}
            minLength={12}
            maxLength={128}
          />
          <SubmitButton testId="reset-submit">{t.reset.submit}</SubmitButton>
        </form>
      ) : null}
    </div>
  );
}
