import Link from "next/link";

import { currentUser } from "@/lib/auth";
import { canEdit } from "@/lib/edition";

// Lien d'en-tête vers l'espace d'édition, affiché seulement pour les rôles éditoriaux (l'API revérifie).
export async function EditionLink({ locale, label }: { locale: string; label: string }) {
  const me = await currentUser();
  if (!canEdit(me)) return null;
  return (
    <Link
      href={`/${locale}/edition`}
      data-testid="edition-link"
      className="inline-flex min-h-11 items-center rounded-md px-3 font-semibold text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
    >
      {label}
    </Link>
  );
}
