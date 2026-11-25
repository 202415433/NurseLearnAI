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
  projects: [
    { name: "chromium", testIgnore: /medias\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    // 2.2.3-e (E3) : parcours des médias protégés sur la pile réelle (E2E_MEDIA fourni par
    // scripts/validate-phase2.2.3e.ps1). Google Chrome : décodage H.264 de la vidéo de test.
    ...(process.env.E2E_MEDIA || process.env.E2E_MEDIA_FILE
      ? [
          {
            name: "chrome-media",
            testMatch: /medias\.spec\.ts/,
            timeout: 240_000,
            retries: 0,
            // Aucune trace : elle contiendrait cookies de session et échanges réseau (artefacts CI assainis).
            use: { ...devices["Desktop Chrome"], channel: "chrome", trace: "off" as const },
          },
        ]
      : []),
  ],
  webServer: external
    ? undefined
    : { command: "npm run start", url: "http://localhost:3000/fr", reuseExistingServer: true, timeout: 120_000 },
});
