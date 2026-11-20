import Link from "next/link";
import { redirect } from "next/navigation";

import { Checkbox, Field, FormStatus, SubmitButton } from "@/components/Form";
import { registerAction } from "@/lib/actions";
import { currentUser } from "@/lib/auth";
import { getDictionary } from "@/lib/i18n";
import { localeFrom, statusParams, type SearchParams } from "@/lib/route";

export default async function RegisterPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: SearchParams;
}) {
  const locale = await localeFrom(params);
  if (await currentUser()) redirect(`/${locale}/compte`);
  const t = getDictionary(locale).auth;
  const { error } = statusParams(await searchParams);
  return (
    <div className="mx-auto max-w-xl space-y-6">
      <h1 className="text-3xl font-bold">{t.register.title}</h1>
      <p className="text-fg-muted">{t.register.lead}</p>
      <FormStatus error={error} messages={t.messages} errors={t.errors} />
      <form action={registerAction} className="space-y-5" data-testid="register-form">
        <input type="hidden" name="locale" value={locale} />
        <Field name="display_name" label={t.fields.displayName} autoComplete="name" maxLength={100} />
        <Field name="email" type="email" label={t.fields.email} autoComplete="email" maxLength={254} />
        <Field
          name="password"
          type="password"
          label={t.fields.password}
          autoComplete="new-password"
          hint={t.fields.passwordHint}
          minLength={12}
          maxLength={128}
        />
        {/* Champ piège : invisible et ignoré par les personnes, rempli par les robots. */}
        <div className="hidden" aria-hidden="true">
          <label htmlFor="website">{t.fields.honeypot}</label>
          <input id="website" name="website" type="text" tabIndex={-1} autoComplete="off" />
        </div>
        <fieldset className="space-y-3">
          <legend className="sr-only">{t.register.title}</legend>
          <Checkbox name="adult_attestation" testId="adult">
            {t.register.adult}
          </Checkbox>
          <Checkbox name="accept_terms" testId="terms">
            {t.register.terms} (
            <Link href={`/${locale}/conditions`} className="underline">
              {t.register.read}
            </Link>
            )
          </Checkbox>
          <Checkbox name="accept_privacy" testId="privacy">
            {t.register.privacy} (
            <Link href={`/${locale}/confidentialite`} className="underline">
              {t.register.read}
            </Link>
            )
          </Checkbox>
        </fieldset>
        <SubmitButton testId="register-submit">{t.register.submit}</SubmitButton>
      </form>
      <p>
        {t.register.haveAccount}{" "}
        <Link href={`/${locale}/connexion`} className="font-semibold underline">
          {t.nav.signIn}
        </Link>
      </p>
    </div>
  );
}
