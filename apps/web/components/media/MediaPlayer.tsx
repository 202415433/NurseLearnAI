"use client";

// Lecteurs réutilisables (aperçu éditorial et apprenant) : image, PDF, vidéo MP4 avec sous-titres VTT.
// Toutes les adresses pointent vers le relais Web de même origine (/relais/medias/…) : jamais vers le
// stockage, jamais avec un jeton. Chaque requête (y compris chaque plage vidéo) est revérifiée par
// media-delivery ; une révocation ou une session expirée interrompt la lecture et l'interface l'annonce.
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getMediaDictionary } from "@/lib/i18n-media";
import type { Locale } from "@/lib/i18n";
import { relay, type MediaKind } from "@/lib/media";

export type PlayableMedia = {
  id: string;
  kind: MediaKind;
  mime_type: string;
  display_name: string;
  alt_text: Record<string, string> | null;
};
export type CaptionTrack = { id: string; srclang: string; label: string };

type Source = { mode: "preview" } | { mode: "learner"; release: string; lessonVersion: string };

export function MediaPlayer({
  media,
  source,
  captions = [],
  locale,
}: {
  media: PlayableMedia;
  source: Source;
  captions?: CaptionTrack[];
  locale: Locale;
}) {
  const t = getMediaDictionary(locale).player;
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const router = useRouter();
  // Après un échec, la page serveur est relue une fois : l'état affiché (révoqué, bloqué) suit le
  // refus de la diffusion au lieu de rester « disponible ».
  useEffect(() => {
    if (!failed) return;
    const timer = window.setTimeout(() => router.refresh(), 1500);
    return () => window.clearTimeout(timer);
  }, [failed, router]);
  const url = (id: string) =>
    source.mode === "preview" ? relay.preview(id) : relay.content(id, source.release, source.lessonVersion);
  const alt = media.alt_text?.[locale] ?? media.alt_text?.fr ?? "";

  const failure = failed ? (
    <div
      role="alert"
      data-testid="player-error"
      className="mt-3 rounded-md border-l-4 border-danger bg-danger-soft p-4"
    >
      <p>{t.error}</p>
      <button
        type="button"
        onClick={() => {
          setFailed(false);
          setAttempt((n) => n + 1);
        }}
        className="mt-2 inline-flex min-h-11 items-center rounded-md border border-border-strong bg-surface px-4 font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        {t.retry}
      </button>
    </div>
  ) : null;

  if (media.kind === "image") {
    return (
      <figure data-testid="player-image">
        {/* Balise img native : le relais sert déjà l'image normalisée (l'optimiseur Next.js est exclu
            pour ne jamais copier ni mettre en cache un média protégé). */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          key={attempt}
          src={url(media.id)}
          alt={alt}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className="h-auto max-h-[70vh] w-auto max-w-full rounded-md border border-border"
        />
        {failure}
      </figure>
    );
  }

  if (media.kind === "video") {
    return (
      <div data-testid="player-video">
        <video
          key={attempt}
          controls
          preload="metadata"
          playsInline
          onError={() => setFailed(true)}
          aria-label={`${t.video} : ${media.display_name}`}
          className="w-full max-w-3xl rounded-md border border-border bg-black"
          data-testid="video"
        >
          {/* Un refus au (re)chargement est signalé sur <source>, une coupure en cours sur <video>. */}
          <source src={url(media.id)} type="video/mp4" onError={() => setFailed(true)} />
          {captions.map((c, i) => (
            <track key={c.id} kind="captions" src={url(c.id)} srcLang={c.srclang} label={c.label} default={i === 0} />
          ))}
        </video>
        {failure}
      </div>
    );
  }

  // PDF et sous-titres seuls : téléchargement contrôlé (Content-Disposition fixé par media-delivery).
  return (
    <div data-testid={media.kind === "document" ? "player-pdf" : "player-captions"}>
      <a
        href={url(media.id)}
        download
        className="inline-flex min-h-11 items-center rounded-md border border-border-strong bg-surface px-4 font-semibold text-primary underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        data-testid="player-download"
      >
        {media.kind === "document" ? t.download(media.display_name) : t.captions(media.display_name)}
      </a>
      <p className="mt-2 text-sm text-fg-muted">{t.downloadNote}</p>
    </div>
  );
}
