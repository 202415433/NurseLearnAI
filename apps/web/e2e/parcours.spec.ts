import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Aucune violation d'accessibilité « serious » ou « critical » (objectif WCAG 2.2 AA).
async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const blocking = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(blocking.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

test("la racine redirige vers une langue", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/(fr|en)$/);
});

test("parcours : accueil → pays → métier → catalogue (FR)", async ({ page }) => {
  await page.goto("/fr");
  await expect(page.locator("html")).toHaveAttribute("lang", "fr");
  await expect(page.getByTestId("disclaimer")).toContainText("ni un diplôme");
  await expectAccessible(page);

  await page.getByTestId("cta-start").click();
  await expect(page).toHaveURL(/\/fr\/pays$/);
  await expect(page.getByTestId("jurisdiction-CA-QC")).toHaveText("Québec");
  await expectAccessible(page);

  await page.getByTestId("jurisdiction-CA-QC").click();
  await expect(page).toHaveURL(/\/fr\/metier\?j=CA-QC$/);
  const pab = page.getByTestId("profession-pab");
  await expect(pab).toContainText("Préposé(e) aux bénéficiaires");
  await expect(pab.getByTestId("pending-badge")).toBeVisible();
  await expectAccessible(page);

  await pab.getByRole("link").click();
  await expect(page).toHaveURL(/\/fr\/catalogue\?j=CA-QC&p=pab$/);
  // Le module pilote est en brouillon : il ne doit jamais apparaître.
  await expect(page.getByTestId("catalog-empty")).toBeVisible();
  await expect(page.getByText("Hygiène des mains")).toHaveCount(0);
  await expectAccessible(page);
});

test("version anglaise et changement de langue", async ({ page }) => {
  await page.goto("/fr/metier?j=CA-QC");
  await page.getByRole("link", { name: "English" }).click();
  await expect(page).toHaveURL(/\/en\/metier\?j=CA-QC$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByTestId("profession-pab")).toContainText("Patient attendant");
  await expectAccessible(page);
});

test("thème sombre : bascule, mémorisation et accessibilité", async ({ page }) => {
  await page.goto("/fr");
  await page.getByTestId("theme-toggle").click();
  const theme = await page.locator("html").getAttribute("data-theme");
  expect(theme === "dark" || theme === "light").toBeTruthy();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme!);
  await expectAccessible(page);
});

test("juridiction inconnue : message clair, pas de plantage", async ({ page }) => {
  await page.goto("/fr/metier?j=XX-ZZ");
  await expect(page.getByRole("alert")).toBeVisible();
});
