import type { MediaDictionary } from "@/lib/i18n-media";
import { stateTone, type MediaState } from "@/lib/media";

// État antivirus d'un média : texte explicite (jamais la couleur seule) ; seul « sain » est disponible.
export function StateBadge({ state, t }: { state: MediaState; t: MediaDictionary }) {
  const tone = stateTone(state);
  const styles = {
    success: "border-success text-success",
    warning: "border-warning bg-warning-soft text-fg",
    danger: "border-danger bg-danger-soft text-danger",
    neutral: "border-border-strong text-fg-muted",
  }[tone];
  const icon = { success: "✓", warning: "⏳", danger: "⛔", neutral: "…" }[tone];
  return (
    <span
      data-testid="media-state"
      data-state={state}
      className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-sm font-medium ${styles}`}
    >
      <span aria-hidden="true">{icon}</span>
      {t.states[state]}
    </span>
  );
}

export function FlagBadge({ children, testId }: { children: string; testId: string }) {
  return (
    <span
      data-testid={testId}
      className="inline-flex items-center rounded-full border border-border-strong px-3 py-1 text-sm text-fg"
    >
      {children}
    </span>
  );
}
