import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Phase 2.1 — identité : inscription → courriel local (Mailpit) → vérification → connexion →
// compte et sessions → déconnexion → mot de passe oublié → réinitialisation. FR et EN, axe.
// Nécessite MAILPIT_URL (API configurée avec MAIL_BACKEND=smtp vers ce Mailpit).
const MAILPIT = process.env.MAILPIT_URL?.replace(/\/$/, "");
test.skip(!MAILPIT, "MAILPIT_URL non défini : parcours d'identité ignoré");

const PASSWORD = "parcours-e2e-Phase21-sur";
const NEW_PASSWORD = "nouvelle-phrase-e2e-2026-ok";

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(blocking.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

function uniqueEmail(tag: string): string {
  return `e2e-${tag}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.test`;
}

// Attend le courriel contenant le chemin indiqué et renvoie le jeton du lien.
async function tokenFromMail(request: APIRequestContext, to: string, path: string): Promise<string> {
  for (let attempt = 0; attempt < 30; attempt++) {
    const search = await request.get(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`);
    if (search.ok()) {
      const { messages = [] } = (await search.json()) as { messages?: { ID: string }[] };
      for (const message of messages) {
        const detail = await request.get(`${MAILPIT}/api/v1/message/${message.ID}`);
        const text = ((await detail.json()) as { Text?: string }).Text ?? "";
        const match = new RegExp(`${path.replace("?", "\\?")}([A-Za-z0-9_-]{20,128})`).exec(text);
        if (match) return match[1];
      }
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Aucun courriel ${path} reçu pour ${to}`);
}

async function register(page: Page, locale: "fr" | "en", email: string, password = PASSWORD) {
  await page.goto(`/${locale}/inscription`);
  await page.locator("#display_name").fill("Personne E2E");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.getByTestId("adult").check();
  await page.getByTestId("terms").check();
  await page.getByTestId("privacy").check();
  await page.getByTestId("register-submit").click();
  await expect(page).toHaveURL(new RegExp(`/${locale}/verification\\?etat=registered`));
  await expect(page.getByTestId("form-status")).toBeVisible();
}

async function verify(page: Page, locale: "fr" | "en", email: string) {
  const token = await tokenFromMail(page.request, email, "/verification?jeton=");
  await page.goto(`/${locale}/verification?jeton=${token}`);
  await page.getByTestId("verify-submit").click();
  await expect(page).toHaveURL(new RegExp(`/${locale}/connexion\\?etat=verified`));
  return token;
}

async function login(page: Page, locale: "fr" | "en", email: string, password = PASSWORD) {
  await page.goto(`/${locale}/connexion`);
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(password);
  await page.getByTestId("login-submit").click();
}

test("parcours complet FR : inscription, courriel, vérification, connexion, sessions, déconnexion", async ({
  page,
  context,
}) => {
  const email = uniqueEmail("fr");
  await page.goto("/fr/inscription");
  await expectAccessible(page);
  await register(page, "fr", email);
  await expectAccessible(page);

  // Connexion refusée tant que l'adresse n'est pas vérifiée.
  await login(page, "fr", email);
  await expect(page.getByTestId("form-error")).toContainText("pas encore vérifiée");

  const token = await verify(page, "fr", email);
  await expect(page.getByTestId("form-status")).toContainText("Adresse vérifiée");

  // Le lien de vérification est à usage unique.
  await page.goto(`/fr/verification?jeton=${token}`);
  await page.getByTestId("verify-submit").click();
  await expect(page.getByTestId("form-error")).toContainText("invalide, expiré ou déjà utilisé");

  await login(page, "fr", email);
  await expect(page).toHaveURL(/\/fr\/compte$/);
  await expect(page.getByTestId("account-email")).toHaveText(email);
  await expect(page.getByTestId("account-link")).toHaveText("Mon compte");
  // Rôle étudiant uniquement ; aucune interface d'invitation.
  await expect(page.getByTestId("account-roles").locator("li")).toHaveCount(1);
  await expect(page.getByTestId("account-roles").locator("li")).toHaveAttribute("data-role", "student");
  await expect(page.getByTestId("invitations-link")).toHaveCount(0);
  await expect(page.getByTestId("session-list").locator("li")).toHaveCount(1);
  await expectAccessible(page);

  // Cookie de session : HttpOnly, SameSite=Lax, jamais lisible par le JavaScript de la page.
  const cookie = (await context.cookies()).find((c) => c.name.endsWith("nl_session"));
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
  expect(await page.evaluate(() => document.cookie)).not.toContain("nl_session");

  // Un étudiant n'accède pas à l'interface d'invitation.
  const forbidden = await page.goto("/fr/compte/invitations");
  expect(forbidden?.status()).toBe(404);

  await page.goto("/fr/compte");
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/\/fr\/connexion\?etat=loggedOut/);
  await expect(page.getByTestId("account-link")).toHaveText("Connexion");
  await page.goto("/fr/compte");
  await expect(page).toHaveURL(/\/fr\/connexion/);
});

test("mot de passe oublié et réinitialisation (EN) : lien à usage unique, ancien mot de passe refusé", async ({
  page,
}) => {
  const email = uniqueEmail("en");
  await register(page, "en", email);
  await verify(page, "en", email);

  await page.goto("/en/mot-de-passe-oublie");
  await expectAccessible(page);
  await page.locator("#email").fill(email);
  await page.getByTestId("forgot-submit").click();
  await expect(page.getByTestId("form-status")).toBeVisible();

  const token = await tokenFromMail(page.request, email, "/reinitialisation?jeton=");
  await page.goto(`/en/reinitialisation?jeton=${token}`);
  await expectAccessible(page);
  await page.locator("#new_password").fill(NEW_PASSWORD);
  await page.getByTestId("reset-submit").click();
  await expect(page).toHaveURL(/\/en\/connexion\?etat=resetDone/);

  // Le lien ne sert qu'une fois.
  await page.goto(`/en/reinitialisation?jeton=${token}`);
  await page.locator("#new_password").fill("encore-une-autre-phrase-2026");
  await page.getByTestId("reset-submit").click();
  await expect(page.getByTestId("form-error")).toContainText("invalid, expired or already used");

  await login(page, "en", email, PASSWORD);
  await expect(page.getByTestId("form-error")).toBeVisible();
  await login(page, "en", email, NEW_PASSWORD);
  await expect(page).toHaveURL(/\/en\/compte$/);
  await expect(page.locator("h1")).toHaveText("My account");
});

test("pages d'identité accessibles en français et en anglais", async ({ page }) => {
  for (const locale of ["fr", "en"] as const) {
    for (const path of [
      "connexion",
      "inscription",
      "verification",
      "mot-de-passe-oublie",
      "conditions",
      "confidentialite",
    ]) {
      await page.goto(`/${locale}/${path}`);
      await expect(page.locator("html")).toHaveAttribute("lang", locale);
      await expectAccessible(page);
    }
  }
  await page.goto("/en/inscription");
  await expect(page.locator("h1")).toHaveText("Create a student account");
});

test("redirection après connexion limitée au site (pas de redirection ouverte)", async ({ page }) => {
  await page.goto("/fr/connexion?suite=https://evil.example/");
  await expect(page.locator('input[name="next"]')).toHaveValue("/fr/compte");
});
