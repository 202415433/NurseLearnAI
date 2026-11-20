// Rafraîchissement de session exécuté par le middleware (au plus une fois par minute et par
// navigateur, grâce au cookie témoin nl_chk) : prolonge l'activité, applique la rotation de
// l'identifiant et efface le cookie d'une session expirée ou révoquée.
export const CHECK_COOKIE = "nl_chk";
export const CHECK_SECONDS = 60;

export type RefreshOutcome =
  | { kind: "keep" }
  | { kind: "rotate"; token: string; expiresAt: string }
  | { kind: "clear" };

export function interpretRefresh(status: number, body: unknown): RefreshOutcome {
  if (status === 401) return { kind: "clear" };
  if (status !== 200 || typeof body !== "object" || body === null) return { kind: "keep" };
  const data = body as { rotated?: unknown; session_token?: unknown; expires_at?: unknown };
  if (
    data.rotated === true &&
    typeof data.session_token === "string" &&
    /^[A-Za-z0-9_-]{20,128}$/.test(data.session_token) &&
    typeof data.expires_at === "string"
  ) {
    return { kind: "rotate", token: data.session_token, expiresAt: data.expires_at };
  }
  return { kind: "keep" };
}

// Remplace (ou retire) un cookie dans l'en-tête Cookie transmis au rendu de la page.
export function rewriteCookieHeader(header: string | null, name: string, value: string | null): string {
  const parts = (header ?? "")
    .split(";")
    .map((p) => p.trim())
    .filter((p) => p && !p.startsWith(`${name}=`));
  if (value !== null) parts.push(`${name}=${value}`);
  return parts.join("; ");
}
