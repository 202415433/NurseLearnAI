"use client";

import { useEffect, useState } from "react";

type Labels = { toDark: string; toLight: string; dark: string; light: string };

function currentTheme(): "light" | "dark" {
  const attr = document.documentElement.dataset.theme;
  if (attr === "dark" || attr === "light") return attr;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeToggle({ labels }: { labels: Labels }) {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => setTheme(currentTheme()), []);

  function toggle() {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("nl-theme", next);
    } catch {
      /* stockage indisponible : le choix vaut pour la page courante */
    }
    setTheme(next);
  }

  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={isDark ? labels.toLight : labels.toDark}
      aria-pressed={isDark}
      data-testid="theme-toggle"
      className="min-h-11 min-w-11 rounded-md border border-border-strong px-3 text-sm text-fg hover:bg-surface"
    >
      {isDark ? labels.light : labels.dark}
    </button>
  );
}
