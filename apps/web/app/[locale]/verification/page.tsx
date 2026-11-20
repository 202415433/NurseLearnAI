import { Field, FormCard, FormStatus, SubmitButton } from "@/components/Form";
import { resendVerificationAction, verifyEmailAction } from "@/lib/actions";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

// Le lien reçu ouvre cette page ; la confirmation exige un clic (les analyseurs de liens des
// messageries ne consomment donc pas le jeton à usage unique).
export default async function VerifyPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale).auth;
  const { status, error, token } = statusParams(await searchParams);
  return (
    <div className="mx-auto max-w-xl space-y-6">
      <h1 className="text-3xl font-bold">{t.verify.title}</h1>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />
      {token ? (
        <form action={verifyEmailAction} className="space-y-4" data-testid="verify-form">
          <p>{t.verify.lead}</p>
          <input type="hidden" name="locale" value={locale} />
          <input type="hidden" name="token" value={token} />
          <SubmitButton testId="verify-submit">{t.verify.confirm}</SubmitButton>
        </form>
      ) : (
        <FormCard title={t.verify.resendTitle}>
          <form action={resendVerificationAction} className="space-y-4">
            <p className="text-fg-muted">{t.verify.resendLead}</p>
            <input type="hidden" name="locale" value={locale} />
            <Field name="email" type="email" label={t.fields.email} autoComplete="email" maxLength={254} />
            <SubmitButton variant="secondary">{t.verify.resendSubmit}</SubmitButton>
          </form>
        </FormCard>
      )}
    </div>
  );
}
