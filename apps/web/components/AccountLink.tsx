import Link from "next/link";

import { currentUser } from "@/lib/auth";

// Lien d'en-tête : « Connexion » ou « Mon compte » selon la session (rendu serveur).
export async function AccountLink({ locale, labels }: { locale: string; labels: { signIn: string; account: string } }) {
  const me = await currentUser();
  return (
    <Link
      href={me ? `/${locale}/compte` : `/${locale}/connexion`}
      data-testid="account-link"
      className="inline-flex min-h-11 items-center rounded-md px-3 font-semibold text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
    >
      {me ? labels.account : labels.signIn}
    </Link>
  );
}
