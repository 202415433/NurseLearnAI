import type { Availability } from "@/lib/api";
import type { Dictionary } from "@/lib/i18n";

// Affiche honnêtement la disponibilité : publié, en préparation (non validé) ou rien.
export function AvailabilityBadge({ availability, t }: { availability: Availability; t: Dictionary["availability"] }) {
  const badges: { text: string; className: string; testId: string }[] = [];
  if (availability.published > 0) {
    badges.push({ text: t.published(availability.published), className: "border-success text-success", testId: "badge-published" });
  }
  if (availability.in_preparation > 0) {
    badges.push({
      text: t.inPreparation(availability.in_preparation),
      className: "border-warning bg-warning-soft text-fg",
      testId: "badge-preparation",
    });
  }
  if (badges.length === 0) badges.push({ text: t.none, className: "border-border text-fg-muted", testId: "badge-none" });
  return (
    <span className="flex flex-wrap gap-2">
      {badges.map((b) => (
        <span key={b.testId} data-testid={b.testId} className={`inline-flex items-center rounded-full border px-3 py-1 text-sm ${b.className}`}>
          {b.text}
        </span>
      ))}
    </span>
  );
}
