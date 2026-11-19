import { expect, test } from "@playwright/test";

// Sécurité HTTP de l'interface (Phase 2.0) : CSP à nonce, en-têtes, aucune violation CSP en navigation.

test("en-têtes de sécurité et CSP à nonce unique par requête", async ({ request }) => {
  const first = await request.get("/fr/pays");
  const second = await request.get("/fr/pays");
  expect(first.status()).toBe(200);
  const csp = first.headers()["content-security-policy"] ?? "";
  expect(csp).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'/);
  expect(csp).toContain("frame-ancestors 'none'");
  expect(csp).toContain("object-src 'none'");
  expect(csp).not.toContain("unsafe-eval");
  const nonce = (h: string) => /'nonce-([^']+)'/.exec(h)?.[1];
  expect(nonce(csp)).not.toBe(nonce(second.headers()["content-security-policy"] ?? ""));
  const headers = first.headers();
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-powered-by"]).toBeUndefined();
  // Aucune information interne : ni adresse de l'API, ni trace.
  const body = await first.text();
  expect(body).not.toContain("api:8000");
  expect(body).not.toMatch(/Traceback|at Object\.|node_modules/);
});

test("aucune violation CSP pendant le parcours (scripts Next.js et script de thème)", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (msg) => {
    if (/Content Security Policy|Refused to (execute|load)/i.test(msg.text())) violations.push(msg.text());
  });
  await page.goto("/fr");
  await page.getByTestId("cta-start").click();
  await page.getByTestId("country-CA").click();
  await page.getByTestId("jurisdiction-CA-QC").click();
  await expect(page.getByTestId("profession-pab")).toBeVisible();
  // Le script de thème (en ligne, avec nonce) s'exécute : le thème choisi est réappliqué au rechargement.
  await page.getByRole("button", { name: /thème sombre/i }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(violations).toEqual([]);
});

test("pages inconnues : statut 404 réel", async ({ request }) => {
  for (const url of ["/fr/inexistant", "/fr/metiers?j=XX-ZZ", "/en/programmes/00000000-0000-0000-0000-000000000000"]) {
    expect((await request.get(url)).status(), url).toBe(404);
  }
});
