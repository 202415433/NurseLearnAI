import Link from "next/link";
import { redirect } from "next/navigation";

import { Field, FormStatus, SubmitButton } from "@/components/Form";
import { loginAction } from "@/lib/actions";
import { currentUser } from "@/lib/auth";
import { safeNext } from "@/lib/csrf";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, single, statusParams, type SearchParams } from "@/lib/route";

export default async function LoginPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  const query = await searchParams;
  const next = safeNext(single(query.suite), locale);
  if (await currentUser()) redirect(next);
  const t = getDictionary(locale).auth;
  const { status, error } = statusParams(query);
  return (
    <div className="mx-auto max-w-md space-y-6">
      <h1 className="text-3xl font-bold">{t.login.title}</h1>
      <FormStatus status={status} error={error} messages={t.messages} errors={t.errors} />
      <form action={loginAction} className="space-y-5" data-testid="login-form">
        <input type="hidden" name="locale" value={locale} />
        <input type="hidden" name="next" value={next} />
        <Field name="email" type="email" label={t.fields.email} autoComplete="username" maxLength={254} />
        <Field
          name="password"
          type="password"
          label={t.fields.password}
          autoComplete="current-password"
          maxLength={1024}
        />
        <SubmitButton testId="login-submit">{t.login.submit}</SubmitButton>
      </form>
      <p>
        <Link href={`/${locale}/mot-de-passe-oublie`} className="underline">
          {t.login.forgot}
        </Link>
      </p>
      <p>
        {t.login.noAccount}{" "}
        <Link href={`/${locale}/inscription`} className="font-semibold underline">
          {t.login.register}
        </Link>
      </p>
    </div>
  );
}
