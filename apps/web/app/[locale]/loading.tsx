// État de chargement commun (annoncé aux lecteurs d'écran via role="status").
export default function Loading() {
  return (
    <div role="status" aria-live="polite" className="space-y-4" data-testid="loading">
      <span className="sr-only">Chargement… / Loading…</span>
      <div aria-hidden="true" className="h-9 w-2/3 animate-pulse rounded-md bg-surface" />
      <div aria-hidden="true" className="h-24 animate-pulse rounded-lg bg-surface" />
      <div aria-hidden="true" className="h-24 animate-pulse rounded-lg bg-surface" />
    </div>
  );
}
