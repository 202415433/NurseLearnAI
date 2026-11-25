import { Field, FormCard, SubmitButton } from "@/components/Form";
import type { MediaDictionary } from "@/lib/i18n-media";
import { confirmPasswordAction } from "@/lib/media-actions";

// Confirmation du mot de passe (session sensible) sans quitter l'écran d'édition.
export function ReauthCard({ locale, returnTo, t }: { locale: string; returnTo: string; t: MediaDictionary }) {
  return (
    <div data-testid="media-reauth">
      <FormCard title={t.reauth.title}>
        <form action={confirmPasswordAction} className="space-y-4">
          <p className="text-fg-muted">{t.reauth.lead}</p>
          <input type="hidden" name="locale" value={locale} />
          <input type="hidden" name="retour" value={returnTo} />
          <Field
            id="media-reauth-password"
            name="password"
            type="password"
            label={t.reauth.password}
            autoComplete="current-password"
            maxLength={1024}
          />
          <SubmitButton variant="secondary" testId="media-reauth-submit">
            {t.reauth.submit}
          </SubmitButton>
        </form>
      </FormCard>
    </div>
  );
}
