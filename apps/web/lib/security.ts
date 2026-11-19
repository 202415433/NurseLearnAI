// Politique de sécurité du contenu (CSP) et en-têtes associés, appliqués par le middleware.
// Scripts : uniquement ceux portant le nonce de la requête (Next.js l'ajoute à ses propres scripts,
// la mise en page l'ajoute au script de thème). 'strict-dynamic' autorise les scripts qu'ils chargent.

export function createNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}

export function buildCsp(nonce: string, options: { dev?: boolean; https?: boolean } = {}): string {
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    // 'unsafe-eval' seulement en développement (rechargement à chaud de Next.js).
    "script-src": ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'", ...(options.dev ? ["'unsafe-eval'"] : [])],
    // Styles en ligne : utilisés par Next.js (annonces de route) ; aucun style fourni par l'utilisateur.
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'"],
    "connect-src": ["'self'", ...(options.dev ? ["ws:"] : [])],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'"],
    "frame-ancestors": ["'none'"],
  };
  const parts = Object.entries(directives).map(([name, values]) => `${name} ${values.join(" ")}`);
  if (options.https) parts.push("upgrade-insecure-requests");
  return parts.join("; ");
}
