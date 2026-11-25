import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MediaPlayer } from "@/components/media/MediaPlayer";
import { MediaUploader } from "@/components/media/MediaUploader";
import { QuotaPanel } from "@/components/media/QuotaPanel";
import { StateBadge } from "@/components/media/StateBadge";
import type { Me } from "@/lib/auth";
import { getMediaDictionary } from "@/lib/i18n-media";
import { MEDIA_STATES, MIB } from "@/lib/media";

const ID = "11111111-2222-4333-8444-555555555555";
const CAP = "22222222-2222-4333-8444-555555555555";
const t = getMediaDictionary("fr");

const me: Me = {
  id: "u",
  email: "auteur@example.test",
  display_name: "Auteur fictif",
  locale: "fr",
  status: "active",
  roles: [{ role: "instructor", organization_id: "org-1", organization_name: "Établissement fictif" }],
  permissions: [],
  sensitive_fresh: true,
  session_expires_at: "2026-12-01T00:00:00Z",
};

describe("StateBadge", () => {
  it("affiche chaque état en texte (jamais la couleur seule)", () => {
    for (const state of MEDIA_STATES) {
      const { unmount } = render(<StateBadge state={state} t={t} />);
      const badge = screen.getByTestId("media-state");
      expect(badge.textContent).toContain(t.states[state]);
      expect(badge.getAttribute("data-state")).toBe(state);
      unmount();
    }
  });
});

describe("MediaPlayer", () => {
  const base = { id: ID, mime_type: "image/png", display_name: "Schéma fictif", alt_text: { fr: "Schéma des mains" } };

  it("image : texte alternatif, adresse de relais, erreur annoncée", () => {
    render(<MediaPlayer media={{ ...base, kind: "image" }} source={{ mode: "preview" }} locale="fr" />);
    const img = screen.getByRole("img");
    expect(img.getAttribute("alt")).toBe("Schéma des mains");
    expect(img.getAttribute("src")).toBe(`/relais/medias/${ID}/apercu`);
    fireEvent.error(img);
    expect(screen.getByTestId("player-error").getAttribute("role")).toBe("alert");
  });

  it("vidéo : source MP4 et piste de sous-titres par le relais apprenant", () => {
    const { container } = render(
      <MediaPlayer
        media={{ ...base, kind: "video", mime_type: "video/mp4" }}
        source={{ mode: "learner", release: "r-1", lessonVersion: "lv-1" }}
        captions={[{ id: CAP, srclang: "fr", label: "Français" }]}
        locale="fr"
      />,
    );
    const source = container.querySelector("source");
    expect(source?.getAttribute("src")).toBe(`/relais/medias/${ID}/diffusion?release=r-1&lesson_version=lv-1`);
    const track = container.querySelector("track");
    expect(track?.getAttribute("kind")).toBe("captions");
    expect(track?.getAttribute("src")).toBe(`/relais/medias/${CAP}/diffusion?release=r-1&lesson_version=lv-1`);
    expect(container.querySelector("video")?.hasAttribute("controls")).toBe(true);
    // Révocation pendant la lecture : la requête suivante échoue → message explicite et reprise possible.
    fireEvent.error(container.querySelector("video") as HTMLVideoElement);
    expect(screen.getByTestId("player-error").textContent).toContain("révoqué");
    fireEvent.click(screen.getByRole("button", { name: t.player.retry }));
    expect(screen.queryByTestId("player-error")).toBeNull();
  });

  it("PDF : téléchargement contrôlé par le relais", () => {
    render(
      <MediaPlayer
        media={{ ...base, kind: "document", mime_type: "application/pdf" }}
        source={{ mode: "preview" }}
        locale="en"
      />,
    );
    const link = screen.getByTestId("player-download");
    expect(link.getAttribute("href")).toBe(`/relais/medias/${ID}/apercu`);
    expect(link.hasAttribute("download")).toBe(true);
    expect(link.textContent).toContain("Download");
  });
});

describe("QuotaPanel", () => {
  it("annonce l'occupation de chaque portée et les limites par fichier", () => {
    render(
      <QuotaPanel
        me={me}
        locale="fr"
        t={t}
        quotas={{
          scopes: [
            { scope: "user", organization_id: null, used_bytes: 10 * MIB, reserved_bytes: 0, quota_bytes: 1073741824 },
            {
              scope: "organization",
              organization_id: "org-1",
              used_bytes: 0,
              reserved_bytes: 5 * MIB,
              quota_bytes: 5 * 1073741824,
            },
          ],
          file_limits: { image: 5 * MIB, document: 20 * MIB, video: 200 * MIB, captions: MIB },
        }}
      />,
    );
    const meters = screen.getAllByRole("meter");
    expect(meters).toHaveLength(2);
    expect(screen.getByLabelText("Vos médias")).toBe(meters[0]);
    expect(screen.getByText(/Établissement fictif/)).toBeTruthy();
    expect(screen.getByTestId("file-limits").textContent).toContain("200 Mio");
  });
});

describe("MediaUploader", () => {
  afterEach(() => vi.unstubAllGlobals());
  const scopes = [{ value: "", label: "Catalogue public" }];

  function pick(name: string, type: string, size: number) {
    const file = new File(["x"], name, { type });
    Object.defineProperty(file, "size", { value: size });
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [file] } });
  }

  it("refuse un fichier trop volumineux ou d'un format non pris en charge, avec un message explicite", () => {
    render(<MediaUploader locale="fr" scopes={scopes} videos={[]} detailBase="/fr/edition/medias" />);
    pick("photo.png", "image/png", 5 * MIB + 1);
    expect(screen.getByTestId("upload-error").textContent).toContain("5 Mio");
    pick("anim.gif", "image/gif", 10);
    expect(screen.getByTestId("upload-error").textContent).toContain("Format non pris en charge");
  });

  it("exige le texte alternatif d'une image avant tout envoi", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<MediaUploader locale="fr" scopes={scopes} videos={[]} detailBase="/fr/edition/medias" />);
    pick("photo.png", "image/png", 1000);
    expect(screen.getByTestId("file-selected").textContent).toContain("photo.png");
    expect(screen.getByLabelText(/Texte alternatif en français/)).toBeTruthy();
    fireEvent.click(screen.getByTestId("upload-submit"));
    expect(screen.getByTestId("upload-error").textContent).toContain("texte alternatif");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("affiche le glisser-déposer et les limites, reliés au champ fichier", () => {
    render(<MediaUploader locale="en" scopes={scopes} videos={[]} detailBase="/en/edition/medias" />);
    const input = screen.getByLabelText("File");
    expect(input.getAttribute("aria-describedby")).toContain("media-limits");
    expect(screen.getByTestId("drop-zone").textContent).toContain("or drop it here");
    expect(document.getElementById("media-limits")?.textContent).toContain("200 MiB");
  });

  it("réserve, envoie par le relais avec une progression réelle, puis suit l'analyse", async () => {
    const fetchMock = vi.fn(async (url: string) =>
      url === "/relais/medias"
        ? new Response(JSON.stringify({ id: ID }), { status: 201 })
        : new Response(JSON.stringify({ state: "clean" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const sent: { url?: string; method?: string; body?: unknown } = {};
    class FakeXhr {
      upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
      status = 0;
      responseText = "";
      onload?: () => void;
      onerror?: () => void;
      onabort?: () => void;
      open(method: string, url: string) {
        sent.method = method;
        sent.url = url;
      }
      setRequestHeader() {}
      abort() {}
      send(body: unknown) {
        sent.body = body;
        this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
        this.status = 200;
        this.responseText = JSON.stringify({ id: ID, state: "clean" });
        this.onload?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    render(<MediaUploader locale="fr" scopes={scopes} videos={[]} detailBase="/fr/edition/medias" />);
    pick("doc.pdf", "application/pdf", 1000);
    fireEvent.change(screen.getByLabelText("Licence"), { target: { value: "CC-BY-4.0" } });
    fireEvent.click(screen.getByTestId("upload-submit"));
    await screen.findByTestId("upload-open");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/relais/medias");
    const body = JSON.parse(String(init.body));
    expect(body).toMatchObject({
      kind: "document",
      mime_type: "application/pdf",
      size_bytes: 1000,
      license: "CC-BY-4.0",
    });
    expect(sent).toMatchObject({ method: "PUT", url: `/relais/medias/${ID}/contenu` });
    expect(screen.getByTestId("upload-state").getAttribute("data-state")).toBe("clean");
    expect(screen.getByTestId("upload-open").getAttribute("href")).toBe(`/fr/edition/medias/${ID}`);
  });
});
