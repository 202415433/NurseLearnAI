// Rafraîchissement de session exécuté par le middleware (au plus une fois par minute et par
// navigateur, grâce au cookie témoin nl_chk) : prolonge l'activité, applique la rotation de
// l'identifiant et efface le cookie d'une session expirée ou révoquée.
export const CHECK_COOKIE = "nl_chk";
export const CHECK_SECONDS = 60;

type Keep = { kind: "keep" };
type Rotate = { kind: "rotate"; token: string; expiresAt: string };
type Clear = { kind: "clear" };
export type RefreshOutcome = Keep | Rotate | Clear;

export function interpretRefresh(status: number, body: unknown): RefreshOutcome {
  if (status === 401) return { kind: "clear" };
  if (status !== 200 || typeof body !== "object" || body === null) return { kind: "keep" };
  const data = body as { rotated?: unknown; session_token?: unknown; expires_at?: unknown };
  const token = typeof data.session_token === "string" ? data.session_token : "";
  const expiresAt = typeof data.expires_at === "string" ? data.expires_at : "";
  const valid = /^[A-Za-z0-9_-]{20,128}$/.test(token);
  if (data.rotated === true && valid && expiresAt) return { kind: "rotate", token, expiresAt };
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
