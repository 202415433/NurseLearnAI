// @vitest-environment node
import { describe, expect, it } from "vitest";

import { buildCsp, createNonce } from "@/lib/security";

describe("CSP", () => {
  it("n'autorise que les scripts portant le nonce", () => {
    const csp = buildCsp("abc123");
    expect(csp).toContain("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).not.toContain("upgrade-insecure-requests");
  });

  it("assouplit uniquement en développement, durcit en HTTPS", () => {
    expect(buildCsp("n", { dev: true })).toContain("'unsafe-eval'");
    expect(buildCsp("n", { https: true })).toContain("upgrade-insecure-requests");
  });

  it("génère des nonces aléatoires de 128 bits", () => {
    const a = createNonce();
    const b = createNonce();
    expect(a).not.toBe(b);
    expect(atob(a)).toHaveLength(16);
  });
});
