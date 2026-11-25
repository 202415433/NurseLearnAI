import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

// 2.2.3-e (E3) — 24 parcours des médias protégés sur la PILE RÉELLE (Next.js, API, media-delivery,
// PostgreSQL, Redis, RustFS, ClamAV réel, media-worker). Données ENTIÈREMENT FICTIVES préparées par
// scripts/e2e_media_setup.py ; aucune publication. Exécution en série (un parcours prépare le suivant).
//
// E2E_MEDIA_FILE   : fichier JSON des comptes et identifiants de la préparation (mots de passe aléatoires)
// E2E_MEDIA        : même contenu en base64 (repli)
// E2E_MEDIA_FILES  : dossier des fichiers de test (PNG, JPEG, WebP, PDF, MP4 H.264 réel, WebVTT)
// E2E_CAPTURES     : dossier des captures d'écran (facultatif)
type Account = { email: string; password: string; id: string };
type Setup = {
  tag: string;
  accounts: Record<"author" | "author2" | "quota" | "reviewer" | "admin" | "orgA" | "orgB", Account>;
  orgA: string;
  orgB: string;
  module: string;
  draftVersion: string;
  frozenVersion: string;
  orgBDraftVersion: string;
  orgBMedia: string;
  infectedMedia: string;
};

// Fichier temporaire (préféré : aucun mot de passe en ligne de commande) ou variable en base64.
const RAW = process.env.E2E_MEDIA_FILE
  ? readFileSync(process.env.E2E_MEDIA_FILE, "utf8")
  : process.env.E2E_MEDIA
    ? Buffer.from(process.env.E2E_MEDIA, "base64").toString("utf8")
    : undefined;
test.skip(!RAW, "E2E_MEDIA(_FILE) absent : parcours des médias réservés à la validation 2.2.3-e (pile réelle)");
const S: Setup = RAW ? JSON.parse(RAW) : ({} as Setup);
const FILES = process.env.E2E_MEDIA_FILES ?? "/fixtures";
const CAPTURES = process.env.E2E_CAPTURES;
const SCAN_TIMEOUT = 180_000;
const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

test.describe.configure({ mode: "serial" });

// Identifiants produits au fil des parcours.
const ids: Record<string, string> = {};
const names = {
  image: `E2E ${S.tag} schéma`,
  video: `E2E ${S.tag} vidéo`,
  pdf: `E2E ${S.tag} fiche`,
  vtt: `E2E ${S.tag} sous-titres`,
};
let author: BrowserContext;
let authorPage: Page;

async function capture(page: Page, name: string) {
  if (!CAPTURES) return;
  mkdirSync(CAPTURES, { recursive: true });
  await page.screenshot({ path: join(CAPTURES, `${name}.png`), fullPage: true });
}

async function expectAccessible(page: Page) {
  await expect(page).toHaveTitle(/NurseLearn AI/);
  const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  expect(results.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length}`)).toEqual([]);
}

async function login(page: Page, who: Account, locale: "fr" | "en" = "fr") {
  await page.goto(`/${locale}/connexion`);
  await page.locator("#email").fill(who.email);
  await page.locator("#password").fill(who.password);
  await page.getByTestId("login-submit").click();
  await expect(page).toHaveURL(new RegExp(`/${locale}/compte`));
}

async function newSession(browser: Browser, who: Account, viewport = { width: 1280, height: 900 }) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await login(page, who);
  return { context, page };
}

/** En-têtes d'une requête de même origine (le relais exige l'origine pour toute mutation). */
function sameOrigin(page: Page): Record<string, string> {
  const origin = new URL(page.url()).origin;
  return { Origin: origin, "Sec-Fetch-Site": "same-origin" };
}

async function state(request: APIRequestContext, id: string): Promise<string> {
  const r = await request.get(`/relais/medias/${id}`);
  return r.ok() ? ((await r.json()) as { state: string }).state : `http-${r.status()}`;
}

async function waitState(request: APIRequestContext, id: string, expected: string[], timeout = SCAN_TIMEOUT) {
  await expect
    .poll(() => state(request, id), { timeout, intervals: [1000, 2000, 3000] })
    .toMatch(new RegExp(`^(${expected.join("|")})$`));
}

/** Téléversement par l'interface ; renvoie l'identifiant et l'état renvoyé par la réception. */
async function uploadThroughUi(
  page: Page,
  file: string,
  name: string,
  extra: (page: Page) => Promise<void> = async () => undefined,
): Promise<{ id: string; receivedState: string }> {
  await page.goto("/fr/edition/medias/televerser");
  await page.getByTestId("file-input").setInputFiles(join(FILES, file));
  await expect(page.getByTestId("file-selected")).toContainText(file);
  await page.locator("#media-name").fill(name);
  await page.locator("#media-license").fill("CC-BY-4.0");
  await extra(page);
  const reserved = page.waitForResponse((r) => r.url().endsWith("/relais/medias") && r.request().method() === "POST");
  const received = page.waitForResponse((r) => r.url().includes("/contenu") && r.request().method() === "PUT", {
    timeout: 120_000,
  });
  await page.getByTestId("upload-submit").click();
  const reservation = await reserved;
  expect(reservation.status()).toBe(201);
  const { id } = (await reservation.json()) as { id: string };
  expect((await received).status()).toBe(200);
  // État annoncé par l'interface à la réception (réponse du relais), avant tout suivi de l'analyse.
  const announced = page.getByTestId("upload-state");
  await expect(announced).toBeVisible();
  const receivedState = (await announced.getAttribute("data-state")) ?? "";
  return { id, receivedState };
}

test.beforeAll(async ({ browser }) => {
  ({ context: author, page: authorPage } = await newSession(browser, S.accounts.author));
});
test.afterAll(async () => {
  await author?.close();
});

// --------------------------------------------------------------------------------------------- 1–8
test("01 connexion d'un auteur et accès à l'espace d'édition", async ({ page }) => {
  await login(page, S.accounts.author);
  await expect(page.getByTestId("edition-link")).toBeVisible();
  await page.getByTestId("edition-link").click();
  await expect(page).toHaveURL(/\/fr\/edition$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Espace d'édition");
  await expectAccessible(page);
});

test("02 médiathèque : liste, filtres, quotas", async () => {
  const page = authorPage;
  await page.goto("/fr/edition/medias");
  await expect(page.getByTestId("quotas")).toBeVisible();
  await expect(page.getByTestId("quota-scope").first()).toBeVisible();
  await expect(page.getByTestId("file-limits")).toContainText("200 Mio");
  await page.locator("#f-format").selectOption("image");
  await page.locator("#f-analyse").selectOption("clean");
  await page.getByTestId("library-apply").click();
  await expect(page).toHaveURL(/format=image/);
  await expect(page).toHaveURL(/analyse=clean/);
  for (const badge of await page.getByTestId("media-state").all()) {
    await expect(badge).toHaveAttribute("data-state", "clean");
  }
  await expectAccessible(page);
  await capture(page, "02-mediatheque");
});

test("03 téléversement d'une image (progression, relais, aucun envoi direct au stockage)", async () => {
  const page = authorPage;
  const requests: string[] = [];
  page.on("request", (r) => requests.push(r.url()));
  const { id, receivedState } = await uploadThroughUi(page, "schema-fictif.png", names.image, async (p) => {
    await expect(p.locator("#media-alt-fr")).toBeVisible();
    await p.locator("#media-alt-fr").fill("Schéma fictif de démonstration");
  });
  ids.image = id;
  ids.imageReceivedState = receivedState;
  await expect(page.getByTestId("upload-progress")).toBeVisible();
  const base = new URL(page.url()).origin;
  expect(requests.every((u) => u.startsWith(base) || u.startsWith("data:"))).toBe(true);
  expect(requests.some((u) => u.includes(`/relais/medias/${id}/contenu`))).toBe(true);
  await capture(page, "03-televersement");
});

test("04 quarantaine : jamais présentée comme disponible ni servie avant le verdict", async () => {
  const page = authorPage;
  // La réception renvoie toujours un état d'attente (le verdict ne vient que de ClamAV).
  expect(["quarantined", "scanning"]).toContain(ids.imageReceivedState);
  const current = await state(page.request, ids.image);
  if (current === "quarantined" || current === "scanning") {
    expect((await page.request.get(`/relais/medias/${ids.image}/apercu`)).status()).toBe(404);
    await page.goto(`/fr/edition/medias/${ids.image}`);
    await expect(page.getByTestId("media-state")).not.toContainText("disponible");
  }
  // Fichier reçu, jamais analysé : reste en attente, refusé par la diffusion.
  await page.goto("/fr/edition/medias?analyse=quarantined");
  for (const badge of await page.getByTestId("media-state").all()) {
    await expect(badge).toHaveText(/En quarantaine/);
    await expect(badge).not.toHaveText(/disponible/);
  }
});

test("05 analyse par le vrai ClamAV (media-worker)", async () => {
  await waitState(authorPage.request, ids.image, ["clean"]);
});

test("06 média sain : disponible et prévisualisable", async () => {
  const page = authorPage;
  await page.goto(`/fr/edition/medias/${ids.image}`);
  await expect(page.getByTestId("media-state")).toHaveAttribute("data-state", "clean");
  const img = page.getByTestId("player-image").locator("img");
  await expect(img).toHaveAttribute("src", `/relais/medias/${ids.image}/apercu`);
  await expect(img).toHaveAttribute("alt", "Schéma fictif de démonstration");
  await expect.poll(() => img.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await expectAccessible(page);
  await capture(page, "06-media-sain");
});

test("07 rattachement à une leçon modifiable (brouillon)", async () => {
  const page = authorPage;
  await page.goto(`/fr/edition/versions/${S.draftVersion}/medias?q=${encodeURIComponent(names.image)}`);
  await expect(page.getByTestId("version-frozen")).toHaveCount(0);
  const candidate = page.locator(`[data-testid="candidate"][data-media="${ids.image}"]`);
  await candidate.getByTestId("attach").click();
  await expect(page).toHaveURL(/etat=attached/);
  await expect(page.getByTestId("form-status")).toContainText("rattaché");
  await expect(page.locator(`[data-testid="attached-item"][data-media="${ids.image}"]`)).toBeVisible();
  await expectAccessible(page);
  await capture(page, "07-selecteur-lecon");
});

test("08 aperçu éditorial dans le sélecteur", async () => {
  const page = authorPage;
  const img = page.locator(`[data-testid="attached-item"][data-media="${ids.image}"] img`);
  await expect(img).toHaveAttribute("src", `/relais/medias/${ids.image}/apercu`);
  await expect.poll(() => img.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
});

// --------------------------------------------------------------------------------------------- 9–13
test("09 vidéo MP4 : téléversement, analyse, lecture et requêtes Range", async () => {
  test.setTimeout(400_000);
  const page = authorPage;
  const { id } = await uploadThroughUi(page, "video-fictive.mp4", names.video);
  ids.video = id;
  await waitState(page.request, id, ["clean"]);
  const ranged = await page.request.get(`/relais/medias/${id}/apercu`, { headers: { Range: "bytes=0-1023" } });
  expect(ranged.status()).toBe(206);
  expect(ranged.headers()["content-range"]).toMatch(/^bytes 0-1023\/\d+$/);
  expect(ranged.headers()["cache-control"]).toBe("private, no-store");
  expect((await ranged.body()).length).toBe(1024);
  expect(
    (await page.request.get(`/relais/medias/${id}/apercu`, { headers: { Range: "bytes=999999999-" } })).status(),
  ).toBe(416);
  const rangeRequest = page.waitForRequest(
    (r) => r.url().includes(`/relais/medias/${id}/apercu`) && !!r.headers()["range"],
  );
  await page.goto(`/fr/edition/medias/${id}`);
  await rangeRequest;
  const video = page.getByTestId("video");
  await video.evaluate((v: HTMLVideoElement) => {
    v.muted = true;
    return v.play();
  });
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 30_000 })
    .toBeGreaterThan(0.5);
  // Recherche dans la vidéo : nouvelle requête Range, lecture reprise.
  const seek = page.waitForRequest((r) => r.url().includes(`/relais/medias/${id}/apercu`) && !!r.headers()["range"], {
    timeout: 10_000,
  });
  await video.evaluate((v: HTMLVideoElement) => (v.currentTime = 30));
  await seek.catch(() => undefined); // déjà en tampon : aucune nouvelle requête nécessaire
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 30_000 })
    .toBeGreaterThan(29);
  await expect(page.getByTestId("player-error")).toHaveCount(0);
});

test("10 PDF : téléchargement contrôlé par le relais", async () => {
  test.setTimeout(300_000);
  const page = authorPage;
  const { id } = await uploadThroughUi(page, "fiche-fictive.pdf", names.pdf);
  ids.pdf = id;
  await waitState(page.request, id, ["clean"]);
  await page.goto(`/fr/edition/medias/${id}`);
  const download = page.waitForEvent("download");
  await page.getByTestId("player-download").click();
  const file = await download;
  const path = await file.path();
  expect(readFileSync(path).subarray(0, 4).toString()).toBe("%PDF");
  expect(file.suggestedFilename()).toMatch(/\.pdf$/);
  const head = await page.request.get(`/relais/medias/${id}/apercu`);
  expect(head.headers()["content-disposition"]).toMatch(/^attachment;/);
});

test("11 sous-titres VTT rattachés à la vidéo", async () => {
  test.setTimeout(300_000);
  const page = authorPage;
  const { id } = await uploadThroughUi(page, "sous-titres-fictifs.vtt", names.vtt, async (p) => {
    await p.locator("#media-captions-for").selectOption(ids.video);
  });
  ids.vtt = id;
  await waitState(page.request, id, ["clean"]);
  await page.goto(`/fr/edition/medias/${ids.video}`);
  const track = page.locator("video track");
  await expect(track).toHaveAttribute("src", `/relais/medias/${id}/apercu`);
  await expect(track).toHaveAttribute("kind", "captions");
  const vtt = await page.request.get(`/relais/medias/${id}/apercu`);
  expect(vtt.status()).toBe(200);
  expect(vtt.headers()["content-type"]).toMatch(/^text\/vtt/);
  expect(await vtt.text()).toMatch(/^WEBVTT/);
  expect(await page.getByTestId("video").evaluate((v: HTMLVideoElement) => v.textTracks.length)).toBe(1);
  await expectAccessible(page);
  await capture(page, "11-video-sous-titres");
});

test("12 média infecté (EICAR, vrai ClamAV) : bloqué, jamais servi ni rattachable", async () => {
  test.setTimeout(300_000);
  const page = authorPage;
  await waitState(page.request, S.infectedMedia, ["infected"]);
  await page.goto(`/fr/edition/medias/${S.infectedMedia}`);
  await expect(page.getByTestId("media-state")).toHaveAttribute("data-state", "infected");
  await expect(page.getByTestId("media-state")).toContainText("Infecté — bloqué");
  await expect(page.getByTestId("player-image")).toHaveCount(0);
  expect((await page.request.get(`/relais/medias/${S.infectedMedia}/apercu`)).status()).toBe(404);
  await page.goto(`/fr/edition/versions/${S.draftVersion}/medias?q=${encodeURIComponent("antivirus fictif " + S.tag)}`);
  const row = page.locator(`[data-testid="candidate"][data-media="${S.infectedMedia}"]`);
  await expect(row).toContainText("Non rattachable");
  await expect(row.getByTestId("attach")).toHaveCount(0);
  await capture(page, "12-media-infecte");
});

test("13 média non analysé (fichier jamais reçu) : refusé partout", async () => {
  const page = authorPage;
  await page.goto("/fr/edition/medias");
  const reserved = await page.request.post("/relais/medias", {
    headers: sameOrigin(page),
    data: {
      kind: "document",
      mime_type: "application/pdf",
      size_bytes: 1000,
      display_name: `E2E ${S.tag} jamais reçu`,
      license: "CC-BY-4.0",
    },
  });
  expect(reserved.status()).toBe(201);
  const { id } = (await reserved.json()) as { id: string };
  ids.pending = id;
  expect(await state(page.request, id)).toBe("pending_upload");
  expect((await page.request.get(`/relais/medias/${id}/apercu`)).status()).toBe(404);
  await page.goto(`/fr/edition/medias/${id}`);
  await expect(page.getByTestId("media-state")).toHaveAttribute("data-state", "pending_upload");
  await expect(page.getByText("Aperçu indisponible")).toBeVisible();
  await page.goto(`/fr/edition/versions/${S.draftVersion}/medias?q=${encodeURIComponent("jamais reçu")}`);
  await expect(page.locator(`[data-testid="candidate"][data-media="${id}"]`)).toHaveCount(0);
  // Abandon de la réservation (quota libéré).
  expect((await page.request.delete(`/relais/medias/${id}`, { headers: sameOrigin(page) })).status()).toBe(204);
});

// --------------------------------------------------------------------------------------------- 14–20
test("14 refus inter-établissements sans révéler l'autre établissement", async ({ browser }) => {
  const { context, page } = await newSession(browser, S.accounts.orgA);
  const detail = await page.goto(`/fr/edition/medias/${S.orgBMedia}`);
  expect(detail?.status()).toBe(404);
  expect(await page.content()).not.toContain(S.orgB);
  for (const path of [`/relais/medias/${S.orgBMedia}/apercu`, `/relais/medias/${S.orgBMedia}`]) {
    const r = await page.request.get(path);
    expect(r.status()).toBe(404);
    expect(await r.text()).not.toContain(S.orgB);
  }
  expect((await page.goto(`/fr/edition/versions/${S.orgBDraftVersion}/medias`))?.status()).toBe(404);
  await page.goto(`/fr/edition/medias?q=${encodeURIComponent("établissement B " + S.tag)}`);
  await expect(page.getByTestId("library-empty")).toBeVisible();
  await context.close();
});

test("15 édition figée : aucune modification des médias d'une version soumise", async () => {
  const page = authorPage;
  await page.goto(`/fr/edition/versions/${S.frozenVersion}/medias`);
  await expect(page.getByTestId("version-frozen")).toBeVisible();
  await expect(page.getByTestId("version-status")).not.toHaveText("Brouillon");
  await expect(page.getByTestId("attach")).toHaveCount(0);
  await expect(page.getByTestId("detach")).toHaveCount(0);
  await expectAccessible(page);
  await capture(page, "15-version-figee");
});

test("16 révocation pendant la lecture d'une vidéo", async ({ browser }) => {
  test.setTimeout(240_000);
  const page = authorPage;
  await page.goto(`/fr/edition/medias/${ids.video}`);
  const video = page.getByTestId("video");
  await video.evaluate((v: HTMLVideoElement) => {
    v.muted = true;
    return v.play();
  });
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime), { timeout: 30_000 })
    .toBeGreaterThan(0.5);
  // Un relecteur révoque le média pendant que la vidéo est lue.
  const reviewer = await newSession(browser, S.accounts.reviewer);
  await reviewer.page.goto(`/fr/edition/medias/${ids.video}`);
  await reviewer.page.locator("#revoke-reason").fill("Révocation fictive pendant la lecture (validation E3)");
  await reviewer.page.getByTestId("revoke-submit").click();
  await expect(reviewer.page).toHaveURL(/etat=revoked/);
  await expect(reviewer.page.getByTestId("media-state")).toHaveAttribute("data-state", "revoked");
  await reviewer.context.close();
  // Toute requête suivante est refusée ; la lecture s'interrompt et l'interface l'annonce.
  expect(
    (await page.request.get(`/relais/medias/${ids.video}/apercu`, { headers: { Range: "bytes=0-99" } })).status(),
  ).toBe(404);
  // Recherche vers la fin : nouvelle requête Range, refusée. Si Chrome avait déjà tout mis en tampon
  // (aucune requête), un rechargement du lecteur — comme après une éviction du cache — est refusé.
  await video.evaluate((v: HTMLVideoElement) => (v.currentTime = Math.max(0, v.duration - 3)));
  try {
    await expect(page.getByTestId("player-error")).toBeVisible({ timeout: 25_000 });
  } catch {
    await video.evaluate((v: HTMLVideoElement) => v.load());
    await expect(page.getByTestId("player-error")).toBeVisible({ timeout: 30_000 });
  }
  await expect(page.getByTestId("player-error")).toHaveAttribute("role", "alert");
  await capture(page, "16-revocation-pendant-lecture");
  // La page est relue : l'état affiché devient « Révoqué — bloqué », le lecteur disparaît.
  await expect(page.getByTestId("media-state")).toHaveAttribute("data-state", "revoked", { timeout: 15_000 });
  await expect(page.getByTestId("player-video")).toHaveCount(0);
  await capture(page, "16-revocation-etat-actualise");
});

test("17 session révoquée : le relais refuse immédiatement", async ({ browser }) => {
  const { context, page } = await newSession(browser, S.accounts.author2);
  const cookie = (await context.cookies()).find((c) => c.name.endsWith("nl_session"));
  expect(cookie?.httpOnly).toBe(true);
  expect((await page.request.get(`/relais/medias/${ids.image}/apercu`)).status()).toBe(200);
  await page.goto("/fr/compte");
  await page.getByTestId("logout").click();
  await expect(page).toHaveURL(/connexion/);
  // L'ancien identifiant de session, rejoué, est refusé (session révoquée côté serveur).
  const replay = await browser.newContext();
  await replay.addCookies([{ ...cookie!, expires: -1 }]);
  const r = await replay.request.get(`${new URL(page.url()).origin}/relais/medias/${ids.image}/apercu`);
  expect(r.status()).toBe(401);
  await replay.close();
  await context.close();
});

test("18 limites de débit de la diffusion", async ({ browser }) => {
  test.setTimeout(120_000);
  const { context, page } = await newSession(browser, S.accounts.author2);
  const statuses: number[] = [];
  let retryAfter: string | undefined;
  for (let batch = 0; batch < 14 && !statuses.includes(429); batch++) {
    const results = await Promise.all(
      Array.from({ length: 10 }, () => page.request.get(`/relais/medias/${ids.image}/apercu`)),
    );
    for (const r of results) {
      statuses.push(r.status());
      if (r.status() === 429) retryAfter = r.headers()["retry-after"];
    }
  }
  expect(statuses).toContain(429);
  expect(statuses.filter((s) => s !== 200 && s !== 429)).toEqual([]);
  expect(retryAfter).toMatch(/^\d+$/);
  await context.close();
});

test("19 quota dépassé : refus explicite, réservations libérées", async ({ browser }) => {
  test.setTimeout(120_000);
  const { context, page } = await newSession(browser, S.accounts.quota);
  await page.goto("/fr/edition/medias");
  const reservations: string[] = [];
  const reserve = async (size: number) => {
    const r = await page.request.post("/relais/medias", {
      headers: sameOrigin(page),
      data: {
        kind: "video",
        mime_type: "video/mp4",
        size_bytes: size,
        display_name: `E2E ${S.tag} réservation`,
        license: "CC-BY-4.0",
      },
    });
    if (r.status() === 201) reservations.push(((await r.json()) as { id: string }).id);
    return r;
  };
  const MIB = 1048576;
  for (let i = 0; i < 5; i++) expect((await reserve(200 * MIB)).status()).toBe(201);
  expect((await reserve(20 * MIB)).status()).toBe(201); // 1 020 Mio réservés sur 1 Gio
  const over = await reserve(10 * MIB);
  expect(over.status()).toBe(413);
  expect(((await over.json()) as { code?: string }).code).toBe("quota_exceeded");
  // L'interface annonce clairement le dépassement (image : 5 Mio réservés > 4 Mio restants).
  await page.goto("/fr/edition/medias/televerser");
  await page.getByTestId("file-input").setInputFiles(join(FILES, "photo-fictive.jpg"));
  await page.locator("#media-license").fill("CC-BY-4.0");
  await page.locator("#media-alt-fr").fill("Photo fictive");
  await page.getByTestId("upload-submit").click();
  await expect(page.getByTestId("upload-error")).toContainText("Espace de stockage insuffisant");
  await expect(page.getByTestId("quotas")).toContainText("réservés");
  for (const id of reservations) {
    expect((await page.request.delete(`/relais/medias/${id}`, { headers: sameOrigin(page) })).status()).toBe(204);
  }
  await context.close();
});

test("20 conservation légale : administration, motif obligatoire, historique, jamais d'accès ouvert", async ({
  browser,
}) => {
  const { context, page } = await newSession(browser, S.accounts.admin);
  await page.goto(`/fr/edition/medias/${ids.image}`);
  // Motif trop court : bloqué par le formulaire (aucun envoi).
  await page.locator("#hold-reason").fill("abc");
  await page.getByTestId("hold-submit").click();
  await expect(page).not.toHaveURL(/etat=/);
  await page.locator("#hold-reason").fill("Conservation fictive pour la validation E3");
  await page.getByTestId("hold-submit").click();
  await expect(page).toHaveURL(/etat=holdSet/);
  await expect(page.getByTestId("badge-hold")).toBeVisible();
  await expect(page.getByTestId("hold-history").locator("tbody tr")).toHaveCount(1);
  await capture(page, "20-conservation-legale");
  await page.locator("#hold-reason").fill("Fin de la conservation fictive (validation E3)");
  await page.getByTestId("hold-submit").click();
  await expect(page).toHaveURL(/etat=holdReleased/);
  await expect(page.getByTestId("badge-hold")).toHaveCount(0);
  await expect(page.getByTestId("hold-history").locator("tbody tr")).toHaveCount(2);
  // Conservation sur un média révoqué : il reste non diffusable.
  await page.goto(`/fr/edition/medias/${ids.video}`);
  await page.locator("#hold-reason").fill("Preuve fictive conservée (validation E3)");
  await page.getByTestId("hold-submit").click();
  await expect(page).toHaveURL(/etat=holdSet/);
  expect((await page.request.get(`/relais/medias/${ids.video}/apercu`)).status()).toBe(404);
  await page.locator("#hold-reason").fill("Fin de la preuve fictive (validation E3)");
  await page.getByTestId("hold-submit").click();
  await expect(page).toHaveURL(/etat=holdReleased/);
  // Un auteur ne voit ni le formulaire ni l'historique.
  await authorPage.goto(`/fr/edition/medias/${ids.image}`);
  await expect(authorPage.getByTestId("hold-form")).toHaveCount(0);
  await context.close();
});

// --------------------------------------------------------------------------------------------- 21–24
test("21 navigation au clavier", async () => {
  const page = authorPage;
  await page.goto("/fr/edition/medias");
  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.locator("#f-q").focus();
  await page.keyboard.type(names.image);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`q=${encodeURIComponent(names.image).replace(/%20/g, "(\\+|%20)")}`));
  const row = page.locator(`[data-testid="library-row"][data-media="${ids.image}"]`);
  await expect(row).toBeVisible();
  // Le lien du média est atteignable au clavier et s'ouvre avec Entrée.
  const link = row.getByRole("link");
  for (let i = 0; i < 40 && !(await link.evaluate((el) => el === document.activeElement)); i++)
    await page.keyboard.press("Tab");
  await expect(link).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/edition/medias/${ids.image}`));
  // Téléversement : le champ fichier, puis le bouton, sont atteignables au clavier.
  await page.goto("/fr/edition/medias/televerser");
  const input = page.getByTestId("file-input");
  for (let i = 0; i < 40 && !(await input.evaluate((el) => el === document.activeElement)); i++)
    await page.keyboard.press("Tab");
  await expect(input).toBeFocused();
  const submit = page.getByTestId("upload-submit");
  for (let i = 0; i < 20 && !(await submit.evaluate((el) => el === document.activeElement)); i++)
    await page.keyboard.press("Tab");
  await expect(submit).toBeFocused();
});

test("22 français et anglais", async () => {
  const page = authorPage;
  await page.goto(`/en/edition/medias/${ids.image}`);
  await expect(page.getByTestId("media-state")).toHaveText(/Clean — available/);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.goto("/en/edition/medias");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Media library");
  await expectAccessible(page);
  await page.goto("/fr/edition/medias");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Médiathèque");
  await page.goto("/en/edition/medias/televerser");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Upload media");
  await expect(page.getByTestId("drop-zone")).toContainText("or drop it here");
});

test("23 affichage mobile sans débordement horizontal", async ({ browser }) => {
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await mobile.addCookies(await author.cookies());
  const page = await mobile.newPage();
  for (const [path, name] of [
    ["/fr/edition/medias", "23-mobile-mediatheque"],
    ["/en/edition/medias/televerser", "23-mobile-televersement"],
    [`/fr/edition/medias/${ids.image}`, "23-mobile-media"],
    [`/fr/edition/versions/${S.draftVersion}/medias`, "23-mobile-selecteur"],
  ] as const) {
    await page.goto(path);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, path).toBeLessThanOrEqual(1);
    await expectAccessible(page);
    await capture(page, name);
  }
  await mobile.close();
});

test("24 aucun jeton ni secret exposé au navigateur", async ({ browser }) => {
  test.setTimeout(120_000);
  const { context, page } = await newSession(browser, S.accounts.author);
  const token = (await context.cookies()).find((c) => c.name.endsWith("nl_session"))!.value;
  const origin = new URL(page.url()).origin;
  const urls: string[] = [];
  const leaks: string[] = [];
  const FORBIDDEN = [
    token,
    "Bearer ",
    "nl-media-quarantine",
    "nl-media-clean",
    "X-Amz-",
    "presign",
    "media-delivery:",
    "s3:9000",
  ];
  page.on("request", (r) => urls.push(r.url()));
  page.on("response", async (r) => {
    const type = r.headers()["content-type"] ?? "";
    for (const [k, v] of Object.entries(r.headers())) {
      if (k === "set-cookie") continue;
      if (FORBIDDEN.some((f) => v.includes(f))) leaks.push(`${r.url()} en-tête ${k}`);
    }
    if (/text\/html|javascript|json/.test(type)) {
      const body = await r.text().catch(() => "");
      for (const f of FORBIDDEN) if (body.includes(f)) leaks.push(`${r.url()} contient ${f.slice(0, 12)}`);
    }
  });
  for (const path of [
    "/fr/edition",
    "/fr/edition/medias",
    "/fr/edition/medias/televerser",
    `/fr/edition/medias/${ids.image}`,
    `/fr/edition/medias/${ids.pdf}`,
    `/fr/edition/versions/${S.draftVersion}/medias`,
  ]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }
  expect(leaks).toEqual([]);
  expect(urls.filter((u) => !u.startsWith(origin) && !u.startsWith("data:"))).toEqual([]);
  expect(urls.filter((u) => u.includes(token))).toEqual([]);
  // Cookie HttpOnly : invisible au JavaScript ; aucun stockage persistant d'un jeton.
  const exposed = await page.evaluate(() => ({
    cookie: document.cookie,
    local: JSON.stringify(Object.entries(localStorage)),
    session: JSON.stringify(Object.entries(sessionStorage)),
  }));
  expect(exposed.cookie).not.toContain("nl_session");
  expect(exposed.local + exposed.session).not.toContain(token);
  await context.close();
});
