import { defineConfig, devices } from "@playwright/test";

// Deux modes :
// - E2E_BASE_URL défini (ex. http://web:3000 dans Docker) : on teste un serveur déjà démarré ;
// - sinon : « npm run start » est lancé (prérequis : « npm run build » et l'API sur API_INTERNAL_URL).
const external = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: external ?? "http://localhost:3000", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: external
    ? undefined
    : { command: "npm run start", url: "http://localhost:3000/fr", reuseExistingServer: true, timeout: 120_000 },
});
