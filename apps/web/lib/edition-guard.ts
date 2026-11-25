// Garde des pages d'édition : session obligatoire (sinon connexion puis retour), droit éditorial affiché.
import { redirect } from "next/navigation";

import { currentUser, type Me } from "./auth";
import { canEdit } from "./edition";
import type { Locale } from "./i18n";

export async function editorOrRedirect(locale: Locale, path: string): Promise<{ me: Me; allowed: boolean }> {
  const me = await currentUser();
  if (!me) redirect(`/${locale}/connexion?suite=${encodeURIComponent(path)}`);
  return { me, allowed: canEdit(me) };
}
