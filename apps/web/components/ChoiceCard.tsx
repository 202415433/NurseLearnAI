import Link from "next/link";
import type { ReactNode } from "react";

// Carte de choix entièrement cliquable (cible ≥ 44 px, focus visible hérité du lien).
export function ChoiceCard({
  href,
  title,
  description,
  children,
  testId,
}: {
  href: string;
  title: string;
  description?: string;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <li className="list-none">
      <Link
        href={href}
        data-testid={testId}
        className="block h-full min-h-11 rounded-lg border border-border bg-surface p-5 transition-colors hover:border-primary hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        <span className="block text-lg font-semibold text-fg">{title}</span>
        {description ? <span className="mt-1 block text-fg-muted">{description}</span> : null}
        {children ? <span className="mt-3 block">{children}</span> : null}
      </Link>
    </li>
  );
}
