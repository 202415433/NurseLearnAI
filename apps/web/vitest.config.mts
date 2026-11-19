import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  // Vite 8 (Oxc) : JSX automatique explicite, prioritaire sur « jsx: preserve » du tsconfig de Next.js.
  oxc: { jsx: { runtime: "automatic", importSource: "react" } },
  resolve: { alias: { "@": fileURLToPath(new URL("./", import.meta.url)) } },
  test: {
    environment: "jsdom",
    include: ["tests/**/*.test.{ts,tsx}"],
    setupFiles: ["tests/setup.ts"],
  },
});
