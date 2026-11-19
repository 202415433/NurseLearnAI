import type { ReactNode } from "react";

export function EmptyState({ title, children, testId }: { title: string; children?: ReactNode; testId?: string }) {
  return (
    <div
      data-testid={testId}
      className="rounded-lg border border-dashed border-border-strong bg-surface p-8 text-center"
    >
      <p className="text-lg font-semibold">{title}</p>
      {children ? <div className="mx-auto mt-2 max-w-prose text-fg-muted">{children}</div> : null}
    </div>
  );
}
