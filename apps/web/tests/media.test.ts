import { afterEach, describe, expect, it, vi } from "vitest";

import { getMediaDictionary } from "@/lib/i18n-media";
import {
  FILE_LIMITS,
  MEDIA_STATES,
  MIB,
  checkFile,
  formatBytes,
  isAvailable,
  isAwaitingScan,
  isBlocked,
  relay,
  stateTone,
  suggestedName,
} from "@/lib/media";
import {
  DELIVERY_URL,
  SESSION_COOKIE,
  downstreamReadHeaders,
  errorBody,
  mutationAllowed,
  problemCode,
  readAllowed,
  relayedStatus,
  sessionFrom,
  upstreamReadHeaders,
} from "@/lib/media-relay";
import { relayApi, relayRead } from "@/lib/media-relay-handlers";
import { uploadErrorKey } from "@/components/media/MediaUploader";

const ID = "11111111-2222-4333-8444-555555555555";
const REL = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const LV = "99999999-8888-4777-8666-555555555555";
const TOKEN = "Abcdefghijklmnopqrstuvwxyz012345";

function keys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return [prefix];
  return Object.entries(value).flatMap(([k, v]) => keys(v, prefix ? `${prefix}.${k}` : k));
}

describe("dictionnaire des médias", () => {
  it("a les mêmes clés en français et en anglais", () => {
    expect(keys(getMediaDictionary("en")).sort()).toEqual(keys(getMediaDictionary("fr")).sort());
  });

  it("nomme les 7 états en FR/EN sans présenter la quarantaine comme disponible", () => {
    for (const locale of ["fr", "en"] as const) {
      const t = getMediaDictionary(locale);
      expect(Object.keys(t.states).sort()).toEqual([...MEDIA_STATES].sort());
      for (const state of MEDIA_STATES) {
        expect(t.states[state].length).toBeGreaterThan(3);
        expect(t.stateHelp[state].length).toBeGreaterThan(10);
        if (state !== "clean") expect(t.states[state].toLowerCase()).not.toMatch(/disponible|available/);
      }
      expect(t.states.clean.toLowerCase()).toMatch(/disponible|available/);
    }
  });
});

describe("états et disponibilité", () => {
  it("seul un média sain et non supprimé est disponible", () => {
    for (const state of MEDIA_STATES) {
      expect(isAvailable({ state, deleted_at: null })).toBe(state === "clean");
    }
    expect(isAvailable({ state: "clean", deleted_at: "2026-11-24T00:00:00Z" })).toBe(false);
  });

  it("classe les états bloqués, en attente et sains", () => {
    expect(["infected", "scan_failed", "revoked"].every((s) => isBlocked(s as never))).toBe(true);
    expect(isAwaitingScan("quarantined") && isAwaitingScan("scanning")).toBe(true);
    expect(stateTone("clean")).toBe("success");
    expect(stateTone("quarantined")).toBe("warning");
    expect(stateTone("revoked")).toBe("danger");
    expect(stateTone("pending_upload")).toBe("neutral");
  });
});

describe("validation indicative des fichiers", () => {
  it("applique les limites par format (5, 20, 200 et 1 Mio)", () => {
    const cases: [string, string, number][] = [
      ["a.png", "image/png", 5 * MIB],
      ["a.jpg", "image/jpeg", 5 * MIB],
      ["a.webp", "image/webp", 5 * MIB],
      ["a.pdf", "application/pdf", 20 * MIB],
      ["a.mp4", "video/mp4", 200 * MIB],
      ["a.vtt", "text/vtt", 1 * MIB],
    ];
    for (const [name, type, limit] of cases) {
      expect(checkFile({ name, type, size: limit }).ok).toBe(true);
      const over = checkFile({ name, type, size: limit + 1 });
      expect(over.ok).toBe(false);
      if (!over.ok) expect(over.code).toBe("too_large");
    }
    expect(FILE_LIMITS).toEqual({ image: 5 * MIB, document: 20 * MIB, video: 200 * MIB, captions: MIB });
  });

  it("refuse les formats non pris en charge et les fichiers vides ; déduit le type de l'extension", () => {
    expect(checkFile({ name: "x.gif", type: "image/gif", size: 10 })).toEqual({ ok: false, code: "unsupported" });
    expect(checkFile({ name: "x.exe", type: "", size: 10 })).toEqual({ ok: false, code: "unsupported" });
    expect(checkFile({ name: "x.svg", type: "image/svg+xml", size: 10 }).ok).toBe(false);
    expect(checkFile({ name: "x.png", type: "image/png", size: 0 })).toMatchObject({ ok: false, code: "empty" });
    expect(checkFile({ name: "Sous-titres.VTT", type: "", size: 10 })).toEqual({
      ok: true,
      kind: "captions",
      mime: "text/vtt",
    });
  });

  it("propose un nom affiché sans chemin ni caractère de contrôle", () => {
    expect(suggestedName("C:\\Users\\x\\photo.png")).toBe("photo.png");
    expect(suggestedName("../../etc/passwd")).toBe("passwd");
    expect(suggestedName("a\u0000b\u001f.png")).toBe("ab.png");
    expect(suggestedName("x".repeat(300)).length).toBe(200);
  });

  it("formate les tailles en unités binaires FR/EN", () => {
    expect(formatBytes(5 * MIB, "fr")).toBe("5 Mio");
    expect(formatBytes(5 * MIB, "en")).toBe("5 MiB");
    expect(formatBytes(1073741824, "en")).toBe("1 GiB");
    expect(formatBytes(null, "fr")).toBe("—");
  });
});

describe("adresses du relais", () => {
  it("restent de même origine, sans jeton ni adresse de stockage", () => {
    const urls = [relay.preview(ID), relay.content(ID, REL, LV), relay.metadata(ID), relay.upload(ID), relay.reserve()];
    for (const url of urls) {
      expect(url.startsWith("/relais/medias")).toBe(true);
      expect(url).not.toMatch(/token|jeton|bearer|signature|X-Amz|s3|:9000/i);
    }
    expect(relay.content(ID, REL, LV)).toBe(`/relais/medias/${ID}/diffusion?release=${REL}&lesson_version=${LV}`);
  });
});

describe("contrôles du relais", () => {
  const h = (init: Record<string, string>) => new Headers({ host: "web:3000", ...init });

  it("refuse les lectures intersites et accepte la même origine ou l'ouverture directe", () => {
    expect(readAllowed(h({ "sec-fetch-site": "same-origin" }))).toBe(true);
    expect(readAllowed(h({ "sec-fetch-site": "none" }))).toBe(true);
    expect(readAllowed(h({}))).toBe(true);
    expect(readAllowed(h({ "sec-fetch-site": "cross-site" }))).toBe(false);
    expect(readAllowed(h({ "sec-fetch-site": "same-site" }))).toBe(false);
    expect(readAllowed(h({ origin: "https://evil.example" }))).toBe(false);
    expect(readAllowed(h({ origin: "http://web:3000" }))).toBe(true);
  });

  it("exige la même origine pour toute mutation (CSRF)", () => {
    expect(mutationAllowed(h({ origin: "http://web:3000", "sec-fetch-site": "same-origin" }))).toBe(true);
    expect(mutationAllowed(h({ origin: "https://evil.example" }))).toBe(false);
    expect(mutationAllowed(h({ "sec-fetch-site": "cross-site" }))).toBe(false);
    expect(mutationAllowed(h({}))).toBe(false);
  });

  it("ne transmet que la session, Range et If-Range valides", () => {
    const out = upstreamReadHeaders(
      h({
        range: "bytes=0-1023",
        "if-range": '"abc-1"',
        cookie: "x=y",
        authorization: "Bearer autre",
        "x-forwarded-host": "evil",
        "accept-encoding": "gzip",
      }),
      TOKEN,
      "10.0.0.1",
    );
    expect(Object.fromEntries(out.entries())).toEqual({
      accept: "*/*",
      authorization: `Bearer ${TOKEN}`,
      range: "bytes=0-1023",
      "if-range": '"abc-1"',
      "x-forwarded-for": "10.0.0.1",
    });
    const bad = upstreamReadHeaders(h({ "if-range": "Wed, 21 Oct 2015 07:28:00 GMT" }), TOKEN);
    expect(bad.has("if-range")).toBe(false);
    // Plage abusive : transmise (tronquée) pour que media-delivery réponde 416.
    expect(upstreamReadHeaders(h({ range: "bytes=0-1,2-3,4-5" }), TOKEN).get("range")).toBe("bytes=0-1,2-3,4-5");
  });

  it("renvoie une liste blanche d'en-têtes, sans cookie ni cache partagé", () => {
    const out = downstreamReadHeaders(
      new Headers({
        "content-type": "video/mp4",
        "content-range": "bytes 0-1/10",
        "set-cookie": "a=b",
        server: "uvicorn",
        "x-request-id": "123",
        "cache-control": "public, max-age=3600",
      }),
    );
    expect(out.get("set-cookie")).toBeNull();
    expect(out.get("server")).toBeNull();
    expect(out.get("x-request-id")).toBeNull();
    expect(out.get("cache-control")).toBe("private, no-store");
    expect(out.get("content-range")).toBe("bytes 0-1/10");
    expect(out.get("content-security-policy")).toContain("sandbox");
  });

  it("filtre statuts, codes et sessions", () => {
    expect([200, 206, 416, 401, 404, 429].map(relayedStatus)).toEqual([200, 206, 416, 401, 404, 429]);
    expect(relayedStatus(500)).toBe(502);
    expect(relayedStatus(302)).toBe(502);
    expect(errorBody(404, "not_found")).toBe('{"status":404,"code":"not_found"}');
    expect(errorBody(404, "<script>")).toBe('{"status":404}');
    expect(problemCode({ type: "https://nurselearn.ai/problems/reauthentication-required" })).toBe(
      "reauthentication_required",
    );
    expect(sessionFrom(TOKEN)).toBe(TOKEN);
    expect(sessionFrom("a;b")).toBeNull();
    expect(sessionFrom(undefined)).toBeNull();
  });

  it("traduit les erreurs de téléversement en messages d'une liste fermée", () => {
    expect(uploadErrorKey("reauthentication_required", 401)).toBe("reauth");
    expect(uploadErrorKey("quota_exceeded", 413)).toBe("quota_exceeded");
    expect(uploadErrorKey(undefined, 429)).toBe("rate_limited");
    expect(uploadErrorKey("media_type_mismatch", 422)).toBe("media_invalid");
    expect(uploadErrorKey(undefined, 0)).toBe("network");
    expect(uploadErrorKey("inconnu", 500)).toBe("generic");
  });
});

describe("relais de lecture (flux)", () => {
  afterEach(() => vi.unstubAllGlobals());

  function request(path: string, headers: Record<string, string> = {}) {
    return new Request(`http://web:3000${path}`, {
      headers: { host: "web:3000", cookie: `${SESSION_COOKIE}=${TOKEN}`, ...headers },
    });
  }

  it("exige une session et un identifiant valide", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const anonymous = new Request(`http://web:3000/relais/medias/${ID}/apercu`, { headers: { host: "web:3000" } });
    expect((await relayRead(anonymous, ID, "preview")).status).toBe(401);
    expect((await relayRead(request("/x"), "../../etc", "preview")).status).toBe(404);
    expect((await relayRead(request("/x"), ID, "content", { release: REL, lessonVersion: null })).status).toBe(404);
    expect((await relayRead(request("/x", { "sec-fetch-site": "cross-site" }), ID, "preview")).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("relaie Range en flux vers la destination fixe et renvoie 206", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response("0123", {
          status: 206,
          headers: { "content-type": "video/mp4", "content-range": "bytes 0-3/100", "set-cookie": "x=y" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const res = await relayRead(request("/x", { range: "bytes=0-3" }), ID, "content", {
      release: REL,
      lessonVersion: LV,
    });
    expect(res.status).toBe(206);
    expect(res.headers.get("content-range")).toBe("bytes 0-3/100");
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(await res.text()).toBe("0123");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${DELIVERY_URL}/api/v1/media/${ID}/content?release=${REL}&lesson_version=${LV}`);
    expect(new Headers(init.headers).get("authorization")).toBe(`Bearer ${TOKEN}`);
    expect(new Headers(init.headers).get("range")).toBe("bytes=0-3");
    expect(init.redirect).toBe("manual");
    expect(init.signal).toBeDefined();
  });

  it("transmet 416 et les refus sans détail interne", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "clé interne abc/def", type: "x/range-not-satisfiable" }), {
            status: 416,
            headers: { "content-range": "bytes */100" },
          }),
      ),
    );
    const res = await relayRead(request("/x", { range: "bytes=500-600" }), ID, "preview");
    expect(res.status).toBe(416);
    expect(res.headers.get("content-range")).toBe("bytes */100");
    const body = await res.text();
    expect(body).not.toContain("clé interne");
    expect(body).not.toContain(TOKEN);
  });

  it("répond 503 sans détail si media-delivery est injoignable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("ECONNREFUSED 172.18.0.5:8000");
      }),
    );
    const res = await relayRead(request("/x"), ID, "preview");
    expect(res.status).toBe(503);
    expect(await res.text()).not.toContain("172.18");
  });
});

describe("relais d'API (mutations)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("refuse une mutation intersite avant tout appel", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const req = new Request("http://web:3000/relais/medias", {
      method: "POST",
      headers: { host: "web:3000", origin: "https://evil.example", cookie: `${SESSION_COOKIE}=${TOKEN}` },
      body: "{}",
    });
    const res = await relayApi(req, { method: "POST", path: "/api/v1/editorial/media", body: "{}", json: true }, true);
    expect(res.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("transmet le contenu en flux à l'API et filtre les erreurs", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ code: "quota_exceeded", detail: "occupation 123 octets" }), { status: 413 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const req = new Request(`http://web:3000/relais/medias/${ID}/contenu`, {
      method: "PUT",
      headers: {
        host: "web:3000",
        origin: "http://web:3000",
        "sec-fetch-site": "same-origin",
        cookie: `${SESSION_COOKIE}=${TOKEN}`,
        "content-length": "4",
      },
      body: "abcd",
    });
    const res = await relayApi(
      req,
      { method: "PUT", path: `/api/v1/editorial/media/${ID}/content`, body: req.body },
      true,
    );
    expect(res.status).toBe(413);
    expect(await res.json()).toEqual({ status: 413, code: "quota_exceeded" });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit & { duplex?: string }];
    expect(init.duplex).toBe("half");
    expect(new Headers(init.headers).get("content-type")).toBe("application/octet-stream");
  });
});
