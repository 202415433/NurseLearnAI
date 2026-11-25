import type { Metadata } from "next";
import { headers } from "next/headers";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { AccountLink } from "@/components/AccountLink";
import { EditionLink } from "@/components/EditionLink";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeToggle } from "@/components/ThemeToggle";
import { getDictionary, isLocale } from "@/lib/i18n";
import { getMediaDictionary } from "@/lib/i18n-media";

import "../globals.css";

// Applique le thème mémorisé avant le premier affichage (évite le flash de couleurs).
const themeScript = `try{var t=localStorage.getItem("nl-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

// Rendu dynamique de toutes les pages : chaque réponse porte son propre nonce CSP.
export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const t = getDictionary(locale);
  return { title: t.meta.title, description: t.meta.description, icons: { icon: "/favicon.svg" } };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = getDictionary(locale);
  // Nonce CSP posé par le middleware : seul ce script en ligne est autorisé.
  const nonce = (await headers()).get("x-nonce") ?? undefined;

  return (
    <html lang={locale} suppressHydrationWarning>
      <head>
        <script nonce={nonce} dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen bg-bg text-fg antialiased">
        <a href="#contenu" className="skip-link">
          {t.a11y.skip}
        </a>
        <header className="border-b border-border bg-surface-raised">
          <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
            <Link href={`/${locale}`} className="text-lg font-bold text-primary">
              NurseLearn <span className="text-accent">AI</span>
            </Link>
            <div className="flex flex-wrap items-center gap-2">
              <Suspense fallback={null}>
                <LanguageSwitcher locale={locale} label={t.a11y.language} />
              </Suspense>
              <ThemeToggle labels={t.theme} />
              <Suspense fallback={null}>
                <EditionLink locale={locale} label={getMediaDictionary(locale).nav.edition} />
              </Suspense>
              <Suspense fallback={null}>
                <AccountLink locale={locale} labels={t.auth.nav} />
              </Suspense>
            </div>
          </div>
        </header>
        <main id="contenu" tabIndex={-1} className="mx-auto max-w-5xl px-4 py-8 focus:outline-none">
          {children}
        </main>
        <footer className="border-t border-border bg-surface">
          <p className="mx-auto max-w-5xl px-4 py-6 text-sm text-fg-muted" data-testid="disclaimer">
            {t.disclaimer}
          </p>
          <nav aria-label={t.auth.legal.navLabel} className="mx-auto flex max-w-5xl gap-4 px-4 pb-6 text-sm">
            <Link href={`/${locale}/confidentialite`} className="underline">
              {t.auth.legal.privacyTitle}
            </Link>
            <Link href={`/${locale}/conditions`} className="underline">
              {t.auth.legal.termsTitle}
            </Link>
          </nav>
        </footer>
      </body>
    </html>
  );
}
