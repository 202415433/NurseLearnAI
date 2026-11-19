// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, isValid } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

describe("validation des paramètres d'URL", () => {
  it("accepte les codes attendus", () => {
    expect(isValid("country", "CA")).toBe(true);
    expect(isValid("jurisdiction", "CA-QC")).toBe(true);
    expect(isValid("code", "aide_soignant")).toBe(true);
    expect(isValid("uuid", "3f2b8a0e-1c2d-4e5f-8a9b-0c1d2e3f4a5b")).toBe(true);
  });

  it("refuse les valeurs dangereuses ou absentes", () => {
    expect(isValid("country", "CAN")).toBe(false);
    expect(isValid("jurisdiction", "<script>")).toBe(false);
    expect(isValid("code", "PAB")).toBe(false);
    expect(isValid("code", "../etc")).toBe(false);
    expect(isValid("uuid", "1 OR 1=1")).toBe(false);
    expect(isValid("jurisdiction", undefined)).toBe(false);
  });
});

describe("client API", () => {
  it("appelle le bon point d'accès avec la langue", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0, limit: 100, offset: 0 })));
    vi.stubGlobal("fetch", fetchMock);
    await api.professions("CA-QC", "en");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/v1\/professions\?jurisdiction=CA-QC&limit=100$/);
    expect(init.headers["Accept-Language"]).toBe("en");
    expect(init.cache).toBe("no-store");
  });

  it("omet les filtres vides", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    await api.programs({ jurisdiction: "CA-QC", profession: "pab", specialty: undefined }, "fr");
    expect(String(fetchMock.mock.calls[0][0])).not.toContain("specialty");
  });

  it("transforme les erreurs HTTP et réseau en ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 404 })));
    await expect(api.program("3f2b8a0e-1c2d-4e5f-8a9b-0c1d2e3f4a5b", "fr")).rejects.toMatchObject({ status: 404 });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await expect(api.countries("fr")).rejects.toBeInstanceOf(ApiError);
  });
});
