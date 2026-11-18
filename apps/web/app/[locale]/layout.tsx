import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeToggle } from "@/components/ThemeToggle";
import { getDictionary, isLocale, locales } from "@/lib/i18n";

import "../globals.css";

// Applique le thème mémorisé avant le premier affichage (évite le flash de couleurs).
const themeScript = `try{var t=localStorage.getItem("nl-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

export function generateStaticParams() {
  return locales.map((locale) => ({ locale }));
}

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params;
  if (!isLocale(locale)) return {};
  const t = getDictionary(locale);
  return { title: t.meta.title, description: t.meta.description, icons: { icon: "/favicon.svg" } };
}

export default async function LocaleLayout({ children, params }: { children: ReactNode; params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const t = getDictionary(locale);

  return (
    <html lang={locale} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen bg-bg text-fg antialiased">
        <a href="#contenu" className="skip-link">
          {t.a11y.skip}
        </a>
        <header className="border-b border-border bg-surface-raised">
          <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3">
            <Link href={`/${locale}`} className="text-lg font-bold text-primary">
              NurseLearn <span className="text-accent">AI</span>
            </Link>
            <div className="flex items-center gap-2">
              <Suspense fallback={null}>
                <LanguageSwitcher locale={locale} label={t.a11y.language} />
              </Suspense>
              <ThemeToggle labels={t.theme} />
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
        </footer>
      </body>
    </html>
  );
}
