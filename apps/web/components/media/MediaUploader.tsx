"use client";

// Téléversement d'un média (2.2.3-e) : sélection ou glisser-déposer accessible, validation INDICATIVE,
// progression RÉELLE de l'envoi (XMLHttpRequest), annulation, suivi de l'analyse antivirus.
// Le fichier part vers le relais Web de même origine, qui le transmet en flux à l'API (quarantaine
// privée) : aucun envoi direct au stockage, aucune adresse présignée, aucun jeton côté navigateur.
import { useEffect, useRef, useState } from "react";

import { getMediaDictionary } from "@/lib/i18n-media";
import type { Locale } from "@/lib/i18n";
import {
  ACCEPT,
  FILE_LIMITS,
  MEDIA_KINDS,
  checkFile,
  formatBytes,
  isAwaitingScan,
  isMediaState,
  relay,
  suggestedName,
  type MediaKind,
  type MediaState,
} from "@/lib/media";

export type ScopeOption = { value: string; label: string };
export type VideoOption = { id: string; label: string };

type Phase =
  "idle" | "reserving" | "uploading" | "processing" | "scanning" | "done" | "cancelled" | "timeout" | "error";

const POLL_MS = 2000;
const POLL_MAX = 150; // 5 minutes de suivi ; l'analyse continue ensuite côté serveur

/** Traduit un code d'erreur relayé (liste fermée) en message ; tout code inconnu → message générique. */
export function uploadErrorKey(code: string | undefined, status: number): string {
  if (status === 0) return "network";
  if (code === "reauthentication_required") return "reauth";
  if (code === "quota_exceeded") return "quota_exceeded";
  if (code === "rate_limited" || status === 429) return "rate_limited";
  if (code === "media_too_large" || status === 413) return "media_too_large";
  if (code === "media_unavailable" || status === 503) return "media_unavailable";
  if (code === "unauthenticated" || status === 401) return "unauthenticated";
  if (code === "forbidden" || status === 403 || status === 404) return "forbidden";
  if (code && code.startsWith("media_")) return "media_invalid";
  if (status === 422) return "invalid_input";
  return "generic";
}

async function readError(response: Response): Promise<string | undefined> {
  try {
    const data = (await response.json()) as { code?: unknown };
    return typeof data.code === "string" ? data.code : undefined;
  } catch {
    return undefined;
  }
}

export function MediaUploader({
  locale,
  scopes,
  videos,
  detailBase,
}: {
  locale: Locale;
  scopes: ScopeOption[];
  videos: VideoOption[];
  detailBase: string;
}) {
  const t = getMediaDictionary(locale);
  const u = t.upload;
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<MediaKind | null>(null);
  const [mime, setMime] = useState<string>("");
  const [name, setName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [pct, setPct] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [mediaId, setMediaId] = useState<string | null>(null);
  const [state, setState] = useState<MediaState | null>(null);
  const xhr = useRef<XMLHttpRequest | null>(null);
  const polling = useRef<number | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const errorRef = useRef<HTMLDivElement | null>(null);

  useEffect(
    () => () => {
      xhr.current?.abort();
      if (polling.current) window.clearTimeout(polling.current);
    },
    [],
  );
  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  const busy = phase === "reserving" || phase === "uploading" || phase === "processing";

  function message(key: string, limit?: number): string {
    const entry = u.errors[key] ?? u.errors.generic;
    return typeof entry === "function" ? entry(formatBytes(limit ?? 0, locale)) : entry;
  }

  function select(selected: File | null) {
    setError(null);
    setPhase("idle");
    setState(null);
    setMediaId(null);
    setPct(0);
    if (!selected) {
      setFile(null);
      setKind(null);
      return;
    }
    const check = checkFile(selected);
    if (!check.ok) {
      setFile(null);
      setKind(null);
      setError(message(check.code, check.limit));
      return;
    }
    setFile(selected);
    setKind(check.kind);
    setMime(check.mime);
    setName(suggestedName(selected.name));
  }

  function poll(id: string, count: number) {
    polling.current = window.setTimeout(async () => {
      try {
        const r = await fetch(relay.metadata(id), { cache: "no-store", credentials: "same-origin" });
        if (r.ok) {
          const data = (await r.json()) as { state?: unknown };
          if (isMediaState(data.state)) {
            setState(data.state);
            if (!isAwaitingScan(data.state)) {
              setPhase("done");
              return;
            }
          }
        }
      } catch {
        // réseau momentanément indisponible : nouvel essai au prochain intervalle
      }
      if (count + 1 >= POLL_MAX) setPhase("timeout");
      else poll(id, count + 1);
    }, POLL_MS);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!file || !kind) {
      setError(message("no_file"));
      return;
    }
    const form = new FormData(event.currentTarget);
    const str = (k: string) => (typeof form.get(k) === "string" ? String(form.get(k)).trim() : "");
    const body: Record<string, unknown> = {
      kind,
      mime_type: mime,
      size_bytes: file.size,
      display_name: name.trim() || suggestedName(file.name),
      license: str("license"),
    };
    if (str("attribution")) body.attribution = str("attribution");
    if (kind === "image") {
      if (!str("alt_fr")) {
        setError(message("alt_required"));
        return;
      }
      body.alt_text = { fr: str("alt_fr"), ...(str("alt_en") ? { en: str("alt_en") } : {}) };
    }
    if (kind === "captions") {
      if (!str("captions_for")) {
        setError(message("captions_required"));
        return;
      }
      body.captions_for = str("captions_for");
    }
    if (str("organization_id")) body.organization_id = str("organization_id");

    setPhase("reserving");
    let reserved: Response;
    try {
      reserved = await fetch(relay.reserve(), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        credentials: "same-origin",
      });
    } catch {
      setPhase("error");
      setError(message("network"));
      return;
    }
    if (!reserved.ok) {
      setPhase("error");
      setError(message(uploadErrorKey(await readError(reserved), reserved.status)));
      return;
    }
    const media = (await reserved.json()) as { id: string };
    setMediaId(media.id);
    setPhase("uploading");
    setPct(0);

    const request = new XMLHttpRequest();
    xhr.current = request;
    request.open("PUT", relay.upload(media.id));
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const value = Math.min(100, Math.floor((e.loaded / e.total) * 100));
        setPct(value);
        if (value >= 100) setPhase("processing");
      }
    };
    request.onload = () => {
      xhr.current = null;
      let code: string | undefined;
      let next: unknown;
      try {
        const data = JSON.parse(request.responseText || "{}") as { code?: unknown; state?: unknown };
        code = typeof data.code === "string" ? data.code : undefined;
        next = data.state;
      } catch {
        code = undefined;
      }
      if (request.status !== 200) {
        setPhase("error");
        setError(message(uploadErrorKey(code, request.status)));
        return;
      }
      setPct(100);
      if (isMediaState(next)) setState(next);
      if (isMediaState(next) && !isAwaitingScan(next)) {
        setPhase("done");
        return;
      }
      setPhase("scanning");
      poll(media.id, 0);
    };
    request.onerror = () => {
      xhr.current = null;
      setPhase("error");
      setError(message("network"));
    };
    request.onabort = () => {
      xhr.current = null;
    };
    request.send(file);
  }

  async function cancel() {
    if (xhr.current) xhr.current.abort();
    if (polling.current) window.clearTimeout(polling.current);
    if (mediaId && (phase === "uploading" || phase === "reserving" || phase === "processing")) {
      // Abandon de la réservation (le quota réservé est libéré ; aucun contenu n'est conservé).
      await fetch(relay.metadata(mediaId), { method: "DELETE", credentials: "same-origin" }).catch(() => undefined);
    }
    setPhase("cancelled");
  }

  function reset() {
    select(null);
    setName("");
    if (inputRef.current) inputRef.current.value = "";
    inputRef.current?.focus();
  }

  const stepText =
    phase === "uploading"
      ? u.steps.uploading(pct)
      : phase === "idle" || phase === "error"
        ? ""
        : (u.steps[phase] as string);

  return (
    <form onSubmit={submit} className="space-y-6" data-testid="upload-form" noValidate>
      <div
        data-testid="drop-zone"
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!busy) select(e.dataTransfer.files?.[0] ?? null);
        }}
        className={`rounded-lg border-2 border-dashed p-6 ${dragging ? "border-primary bg-primary-soft" : "border-border-strong bg-surface"}`}
      >
        <label htmlFor="media-file" className="block font-semibold">
          {u.file}
        </label>
        <p id="media-file-help" className="mb-2 text-sm text-fg-muted">
          {dragging ? u.dropActive : `${u.choose} ${u.drop}`}
        </p>
        <input
          ref={inputRef}
          id="media-file"
          name="file"
          type="file"
          accept={ACCEPT}
          disabled={busy}
          aria-describedby="media-file-help media-limits"
          onChange={(e) => select(e.target.files?.[0] ?? null)}
          className="block min-h-11 w-full text-fg file:mr-4 file:min-h-11 file:rounded-md file:border-0 file:bg-primary file:px-4 file:font-semibold file:text-on-primary"
          data-testid="file-input"
        />
        {file ? (
          <p className="mt-2" data-testid="file-selected">
            {u.selected(file.name, formatBytes(file.size, locale))} — {kind ? t.kinds[kind] : ""}
          </p>
        ) : null}
        <div id="media-limits" className="mt-3 text-sm text-fg-muted">
          <p className="font-medium">{u.limits}</p>
          <ul className="grid grid-cols-2 gap-x-4">
            {MEDIA_KINDS.map((k) => (
              <li key={k}>
                {t.kinds[k]}
                {k === "image" ? " (JPEG, PNG, WebP)" : k === "video" ? " (MP4)" : k === "captions" ? " (WebVTT)" : ""}
                {locale === "fr" ? "\u00a0: " : ": "}
                {formatBytes(FILE_LIMITS[k], locale)}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField id="media-name" label={u.displayName} value={name} onChange={setName} max={200} required />
        <TextField id="media-license" name="license" label={u.license} hint={u.licenseHint} max={200} required />
        <TextField id="media-attribution" name="attribution" label={u.attribution} max={500} />
        <div className="space-y-1">
          <label htmlFor="media-scope" className="block font-semibold">
            {u.scope}
          </label>
          <select
            id="media-scope"
            name="organization_id"
            className="block min-h-11 w-full rounded-md border border-border-strong bg-bg px-3"
            data-testid="scope-select"
          >
            {scopes.map((s) => (
              <option key={s.value || "public"} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {kind === "image" ? (
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField id="media-alt-fr" name="alt_fr" label={u.altFr} hint={u.altHint} max={500} required />
          <TextField id="media-alt-en" name="alt_en" label={u.altEn} max={500} />
        </div>
      ) : null}

      {kind === "captions" ? (
        <div className="space-y-1">
          <label htmlFor="media-captions-for" className="block font-semibold">
            {u.captionsFor}
          </label>
          {videos.length ? (
            <select
              id="media-captions-for"
              name="captions_for"
              required
              className="block min-h-11 w-full rounded-md border border-border-strong bg-bg px-3"
            >
              {videos.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label}
                </option>
              ))}
            </select>
          ) : (
            <p className="text-fg-muted">{u.captionsNone}</p>
          )}
        </div>
      ) : null}

      {error ? (
        <div
          ref={errorRef}
          tabIndex={-1}
          role="alert"
          data-testid="upload-error"
          className="rounded-md border-l-4 border-danger bg-danger-soft p-4"
        >
          {error}
        </div>
      ) : null}

      {phase !== "idle" && phase !== "error" ? (
        <div className="space-y-2" data-testid="upload-progress">
          <label htmlFor="media-progress" className="block font-medium">
            {u.progressLabel}
          </label>
          <progress
            id="media-progress"
            max={100}
            value={phase === "reserving" ? undefined : pct}
            className="h-3 w-full"
            data-testid="progress"
          />
          <p role="status" aria-live="polite" data-testid="upload-status">
            {stepText}
          </p>
          {state ? (
            <p data-testid="upload-state" data-state={state}>
              {t.states[state]} — {t.stateHelp[state]}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-wrap gap-3">
        {phase === "idle" || phase === "error" ? (
          <button
            type="submit"
            data-testid="upload-submit"
            className="inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          >
            {u.submit}
          </button>
        ) : null}
        {busy || phase === "scanning" ? (
          <button
            type="button"
            onClick={cancel}
            data-testid="upload-cancel"
            className="inline-flex min-h-11 items-center rounded-md border border-border-strong bg-surface px-5 font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          >
            {u.cancel}
          </button>
        ) : null}
        {phase === "done" || phase === "timeout" || phase === "cancelled" ? (
          <>
            {mediaId && phase !== "cancelled" ? (
              <a
                href={`${detailBase}/${mediaId}`}
                data-testid="upload-open"
                className="inline-flex min-h-11 items-center rounded-md bg-primary px-5 font-semibold text-on-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                {u.viewMedia}
              </a>
            ) : null}
            <button
              type="button"
              onClick={reset}
              className="inline-flex min-h-11 items-center rounded-md border border-border-strong bg-surface px-5 font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              {u.another}
            </button>
          </>
        ) : null}
      </div>
    </form>
  );
}

function TextField({
  id,
  name,
  label,
  hint,
  max,
  required = false,
  value,
  onChange,
}: {
  id: string;
  name?: string;
  label: string;
  hint?: string;
  max: number;
  required?: boolean;
  value?: string;
  onChange?: (v: string) => void;
}) {
  const hintId = hint ? `${id}-aide` : undefined;
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block font-semibold">
        {label}
      </label>
      <input
        id={id}
        name={name ?? id}
        type="text"
        maxLength={max}
        required={required}
        aria-required={required || undefined}
        aria-describedby={hintId}
        {...(onChange ? { value, onChange: (e: React.ChangeEvent<HTMLInputElement>) => onChange(e.target.value) } : {})}
        className="block min-h-11 w-full rounded-md border border-border-strong bg-bg px-3 text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      />
      {hint ? (
        <p id={hintId} className="text-sm text-fg-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
