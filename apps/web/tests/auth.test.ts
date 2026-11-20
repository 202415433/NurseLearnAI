// @vitest-environment node
import { describe, expect, it } from "vitest";

import { isSameOrigin, safeNext } from "@/lib/csrf";
import { getDictionary } from "@/lib/i18n";
import { statusParams } from "@/lib/route";
import { interpretRefresh, rewriteCookieHeader } from "@/lib/session-refresh";

describe("contrôle d'origine des actions serveur (CSRF)", () => {
  it("accepte une requête de même origine", () => {
    expect(isSameOrigin("http://localhost:3000", "localhost:3000", "same-origin")).toBe(true);
    expect(isSameOrigin("https://app.example", "app.example", null)).toBe(true);
  });

  it("refuse une origine tierce ou un site croisé", () => {
    expect(isSameOrigin("https://evil.example", "app.example", null)).toBe(false);
    expect(isSameOrigin("https://app.example", "app.example", "cross-site")).toBe(false);
    expect(isSameOrigin(null, "app.example", null)).toBe(false);
    expect(isSameOrigin("not a url", "app.example", null)).toBe(false);
  });
});

describe("redirection après connexion", () => {
  it("n'accepte que les chemins internes de la langue courante", () => {
    expect(safeNext("/fr/compte/invitations", "fr")).toBe("/fr/compte/invitations");
    expect(safeNext("/fr/invitation?jeton=abc", "fr")).toBe("/fr/invitation?jeton=abc");
  });

  it("neutralise les redirections ouvertes", () => {
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil.example", "/en/compte", "", undefined]) {
      expect(safeNext(bad, "fr")).toBe("/fr/compte");
    }
  });
});

describe("paramètres d'état lus dans l'adresse", () => {
  it("ne garde que des codes et des jetons bien formés", () => {
    const token = "A".repeat(43);
    expect(statusParams({ etat: "verified", erreur: "invalid_link", jeton: token })).toEqual({
      status: "verified",
      error: "invalid_link",
      token,
    });
    expect(statusParams({ etat: "<script>", erreur: ["a", "b"], jeton: "court" })).toEqual({
      status: undefined,
      error: undefined,
      token: undefined,
    });
  });
});

describe("rafraîchissement de session (middleware)", () => {
  it("efface le cookie d'une session refusée", () => {
    expect(interpretRefresh(401, null)).toEqual({ kind: "clear" });
  });

  it("applique la rotation", () => {
    const token = "b".repeat(43);
    expect(interpretRefresh(200, { rotated: true, session_token: token, expires_at: "2026-11-26T00:00:00Z" })).toEqual({
      kind: "rotate",
      token,
      expiresAt: "2026-11-26T00:00:00Z",
    });
  });

  it("conserve la session sinon (pas de rotation, réponse inattendue, API indisponible)", () => {
    expect(interpretRefresh(200, { rotated: false, expires_at: "x" })).toEqual({ kind: "keep" });
    expect(interpretRefresh(200, { rotated: true, session_token: "bad token!", expires_at: "x" })).toEqual({
      kind: "keep",
    });
    expect(interpretRefresh(503, null)).toEqual({ kind: "keep" });
  });

  it("réécrit l'en-tête Cookie transmis au rendu", () => {
    expect(rewriteCookieHeader("a=1; nl_session=old; b=2", "nl_session", "new")).toBe("a=1; b=2; nl_session=new");
    expect(rewriteCookieHeader("a=1; nl_session=old", "nl_session", null)).toBe("a=1");
    expect(rewriteCookieHeader(null, "nl_session", "x")).toBe("nl_session=x");
  });
});

describe("dictionnaire d'identité", () => {
  const keys = (value: unknown, prefix = ""): string[] =>
    typeof value === "object" && value !== null && !Array.isArray(value)
      ? Object.entries(value).flatMap(([k, v]) => keys(v, `${prefix}${k}.`))
      : [prefix];

  it("a les mêmes clés en français et en anglais", () => {
    expect(keys(getDictionary("en").auth).sort()).toEqual(keys(getDictionary("fr").auth).sort());
  });

  it("présente les cinq rôles et rappelle que l'attestation n'est pas un permis d'exercer", () => {
    for (const locale of ["fr", "en"] as const) {
      const t = getDictionary(locale).auth;
      expect(Object.keys(t.account.roleNames).sort()).toEqual(
        ["admin", "instructor", "medical_reviewer", "org_admin", "student"].sort(),
      );
      expect(t.legal.terms.join(" ")).toMatch(locale === "fr" ? /ni un diplôme/ : /not a diploma/);
    }
  });
});
