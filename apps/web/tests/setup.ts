import { cleanup } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, vi } from "vitest";

// next/link est remplacé par un simple <a> : les tests portent sur nos composants, pas sur Next.js.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children?: ReactNode; [key: string]: unknown }) =>
    createElement("a", { href, ...rest }, children),
  useLinkStatus: () => ({ pending: false }),
}));

// next/navigation : routeur factice (les lecteurs relisent la page serveur après un échec).
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn(), replace: vi.fn() }) }));

afterEach(() => cleanup());
