// Protection CSRF des actions serveur (en plus du contrôle d'origine intégré à Next.js) :
// une mutation n'est acceptée que si la requête vient de la même origine que le site.
export function isSameOrigin(origin: string | null, host: string | null, secFetchSite: string | null): boolean {
  if (secFetchSite && secFetchSite !== "same-origin") return false;
  if (!origin || !host) return secFetchSite === "same-origin";
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

// Redirection interne seulement (pas de redirection ouverte après connexion).
export function safeNext(next: string | null | undefined, locale: string): string {
  const fallback = `/${locale}/compte`;
  if (!next || !next.startsWith(`/${locale}/`) || next.startsWith("//") || next.includes("\\")) return fallback;
  return next;
}
