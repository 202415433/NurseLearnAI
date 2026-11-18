"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import type { Locale } from "@/lib/i18n";

const NAMES: Record<Locale, string> = { fr: "Français", en: "English" };

export function LanguageSwitcher({ locale, label }: { locale: Locale; label: string }) {
  const pathname = usePathname() ?? `/${locale}`;
  const search = useSearchParams()?.toString();
  const target: Locale = locale === "fr" ? "en" : "fr";
  const rest = pathname.split("/").slice(2).join("/");
  const href = `/${target}${rest ? `/${rest}` : ""}${search ? `?${search}` : ""}`;
  return (
    <nav aria-label={label}>
      <Link
        href={href}
        hrefLang={target}
        lang={target}
        className="inline-flex min-h-11 items-center rounded-md px-3 text-sm text-primary underline-offset-4 hover:underline"
      >
        {NAMES[target]}
      </Link>
    </nav>
  );
}
