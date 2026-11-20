import { Field, FormStatus, SubmitButton } from "@/components/Form";
import { forgotPasswordAction } from "@/lib/actions";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

export default async function ForgotPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const t = getDictionary(locale).auth;
  const { status, error } = statusParams(await searchParams);
  return (
    <div className="mx-auto max-w-md space-y-6">
      <h1 className="text-3xl font-bold">{t.forgot.title}</h1>
      <p className="text-fg-muted">{t.forgot.lead}</p>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />
      <form action={forgotPasswordAction} className="space-y-5" data-testid="forgot-form">
        <input type="hidden" name="locale" value={locale} />
        <Field name="email" type="email" label={t.fields.email} autoComplete="email" maxLength={254} />
        <SubmitButton testId="forgot-submit">{t.forgot.submit}</SubmitButton>
      </form>
    </div>
  );
}
