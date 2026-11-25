"use client";

// Actualisation contrôlée pendant l'analyse antivirus : la page serveur est rechargée toutes les
// 3 secondes (au plus 100 fois), sans recharger le navigateur ni perdre le focus.
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export function ScanWatcher({ active, intervalMs = 3000 }: { active: boolean; intervalMs?: number }) {
  const router = useRouter();
  useEffect(() => {
    if (!active) return;
    let count = 0;
    const timer = window.setInterval(() => {
      count += 1;
      if (count > 100) window.clearInterval(timer);
      else router.refresh();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs, router]);
  return null;
}
