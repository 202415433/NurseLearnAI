import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Aucune violation d'accessibilité « serious » ou « critical » (objectif WCAG 2.2 AA).
async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(blocking.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

// Données publiées fictives chargées seulement en CI (database/fixtures/e2e_demo_published.sql).
const demo = process.env.E2E_DEMO === "1";

test("la racine redirige vers une langue", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/(fr|en)$/);
});

test("parcours Québec : pays → juridiction → métier → spécialités → programmes (FR)", async ({ page }) => {
  await page.goto("/fr");
  await expect(page.locator("html")).toHaveAttribute("lang", "fr");
  await expect(page.getByTestId("disclaimer")).toContainText("ni un diplôme");
  await expectAccessible(page);

  await page.getByTestId("cta-start").click();
  await expect(page).toHaveURL(/\/fr\/pays$/);
  await expect(page.getByTestId("country-CA")).toContainText("Canada");
  await expect(page.getByTestId("country-FR")).toBeVisible();
  await expect(page.getByTestId("country-SN")).toContainText("Sénégal");
  await expectAccessible(page);

  await page.getByTestId("country-CA").click();
  await expect(page).toHaveURL(/\/fr\/juridictions\?pays=CA$/);
  await expect(page.getByTestId("jurisdiction-CA-QC")).toContainText("Québec");
  await expect(page.getByTestId("jurisdiction-CA-ON")).toContainText("Ontario");
  await expectAccessible(page);

  await page.getByTestId("jurisdiction-CA-QC").click();
  await expect(page).toHaveURL(/\/fr\/metiers\?j=CA-QC$/);
  const pab = page.getByTestId("profession-pab");
  await expect(pab).toContainText("Préposé(e) aux bénéficiaires");
  // Le pilote est en brouillon : il est annoncé « en préparation, non encore validé », jamais comme disponible.
  await expect(pab.getByTestId("badge-preparation")).toContainText("non encore validé");
  await expect(pab.getByTestId("badge-published")).toHaveCount(0);
  // Les métiers d'une autre juridiction n'apparaissent pas (aucune équivalence déduite).
  await expect(page.getByTestId("profession-psw")).toHaveCount(0);
  await expectAccessible(page);

  await pab.click();
  await expect(page).toHaveURL(/\/fr\/specialites\?j=CA-QC&p=pab$/);
  await expect(page.getByTestId("specialty-care_assistant_core")).toBeVisible();
  await expectAccessible(page);

  await page.getByTestId("all-programs").click();
  await expect(page).toHaveURL(/\/fr\/programmes\?j=CA-QC&p=pab$/);
  await expect(page.getByTestId("programs-empty")).toBeVisible();
  await expect(page.getByTestId("programs-in-preparation")).toBeVisible();
  // Le contenu en brouillon ne doit jamais apparaître.
  await expect(page.getByText("Hygiène des mains")).toHaveCount(0);
  await expectAccessible(page);
});

test("Ontario, France et Sénégal ont leurs propres métiers", async ({ page }) => {
  await page.goto("/fr/metiers?j=CA-ON");
  await expect(page.getByTestId("profession-psw")).toBeVisible();
  await expect(page.getByTestId("profession-pab")).toHaveCount(0);

  await page.goto("/fr/juridictions?pays=FR");
  await page.getByTestId("jurisdiction-FR").click();
  await expect(page.getByTestId("profession-aide_soignant")).toContainText("Aide-soignant");

  await page.goto("/fr/juridictions?pays=SN");
  await page.getByTestId("jurisdiction-SN").click();
  await expect(page.getByTestId("profession-aide_infirmier")).toBeVisible();
  await expect(page.getByTestId("profession-infirmier_etat")).toBeVisible();
  await expectAccessible(page);
});

test("référentiels transnationaux", async ({ page }) => {
  await page.goto("/fr/pays");
  await page.getByTestId("country-framework").click();
  await expect(page).toHaveURL(/portee=cadre$/);
  await expect(page.getByTestId("jurisdiction-INTL")).toBeVisible();
  await expect(page.getByTestId("jurisdiction-FRANCOPHONIE")).toBeVisible();
});

test("changement de langue en conservant le parcours", async ({ page }) => {
  await page.goto("/fr/metiers?j=CA-QC");
  await page.getByRole("link", { name: "English" }).click();
  await expect(page).toHaveURL(/\/en\/metiers\?j=CA-QC$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByTestId("profession-pab")).toContainText("Patient attendant");
  await expect(page.getByTestId("disclaimer")).toContainText("not a diploma");
  await expectAccessible(page);
});

test("thème sombre : bascule, mémorisation et accessibilité", async ({ page }) => {
  await page.goto("/fr/pays");
  await page.getByRole("button", { name: /thème sombre/i }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expectAccessible(page);
});

test("paramètres inconnus ou invalides → page introuvable", async ({ page }) => {
  for (const url of [
    "/fr/adresse-inexistante",
    "/fr/metiers?j=XX-ZZ",
    "/fr/metiers?j=%3Cscript%3E",
    "/fr/specialites?j=CA-QC&p=psw",
    "/fr/programmes?j=CA-QC&p=pab&offset=-1",
    "/fr/programmes/pas-un-uuid",
    "/fr/programmes/00000000-0000-0000-0000-000000000000",
    "/fr/juridictions?pays=ZZ",
  ]) {
    const response = await page.goto(url);
    // Statut HTTP réel 404 (Phase 2.0 : plus de réponse diffusée avant de savoir si la page existe).
    expect(response?.status(), url).toBe(404);
    await expect(page.getByTestId("not-found"), url).toBeVisible();
  }
  await expectAccessible(page);
});

test("programme publié : catalogue puis détail (données de démonstration CI)", async ({ page }) => {
  test.skip(!demo, "E2E_DEMO=1 requis (fixture chargée en CI uniquement)");
  await page.goto("/fr/metiers?j=CA-ON");
  await expect(page.getByTestId("profession-psw").getByTestId("badge-published")).toBeVisible();
  await page.goto("/fr/programmes?j=CA-ON&p=psw");
  const card = page.getByTestId("program-e2e-demo-program");
  await expect(card).toContainText("Programme de démonstration");
  await expectAccessible(page);

  await card.click();
  await expect(page).toHaveURL(/\/fr\/programmes\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId("program-title")).toContainText("Programme de démonstration");
  await expect(page.getByTestId("lesson-list")).toContainText("Leçon de démonstration");
  await expect(page.getByRole("note").filter({ hasText: "ni un diplôme" }).first()).toBeVisible();
  await expectAccessible(page);
});
