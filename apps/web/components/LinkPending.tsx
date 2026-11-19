"use client";

import { useLinkStatus } from "next/link";

// État de chargement d'une navigation, affiché dans le lien cliqué (remplace loading.tsx, qui
// obligeait Next.js à envoyer la réponse avant de savoir si la page existe : statut 200 au lieu de 404).
export function LinkPending({ label }: { label: string }) {
  const { pending } = useLinkStatus();
  if (!pending) return null;
  return (
    <span
      role="status"
      data-testid="link-pending"
      className="mt-2 inline-flex items-center gap-2 text-sm text-fg-muted"
    >
      <span
        aria-hidden="true"
        className="h-3 w-3 animate-spin rounded-full border-2 border-primary border-t-transparent"
      />
      {label}
    </span>
  );
}
