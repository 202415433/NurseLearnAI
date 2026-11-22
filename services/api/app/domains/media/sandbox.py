"""Validation et normalisation d'un fichier téléversé, exécutées dans un PROCESSUS SÉPARÉ (S5).

Ce module est autonome (bibliothèque standard, Pillow, pypdf) : il est lancé par
`python -I sandbox.py <type MIME> <entrée> <sortie>` avec des limites de mémoire, de CPU, de taille
de fichier et de durée posées par le processus parent. Il n'ouvre aucune connexion, n'importe rien
de l'application et ne lit que le fichier d'entrée. Il écrit le fichier normalisé et une ligne JSON :
  {"ok": true, "mime": "...", ...}   ou   {"ok": false, "code": "...", "detail": "..."}

Formats autorisés (D15) et traitements :
  image/jpeg, image/png, image/webp : signature, décodage complet, dimensions ≤ 8000 px,
      image fixe seulement, RÉENCODAGE sans aucune métadonnée (EXIF, GPS, XMP, ICC, textes PNG) ;
  application/pdf : signature, analyse structurelle de tous les objets ; refus du chiffrement,
      JavaScript, actions automatiques ou de lancement, fichiers joints, formulaires XFA, médias riches ;
  video/mp4 : parcours borné des boîtes ISO BMFF (S4) : conteneur, pistes vidéo/audio, codecs
      autorisés, durée ≤ 3 h ; aucun décodage ;
  text/vtt : UTF-8 strict, en-tête WEBVTT, minutages valides, blocs STYLE refusés, balises
      neutralisées hors liste blanche WebVTT ; réécriture du texte.
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

MAX_SIDE = 8000
MAX_PIXELS = MAX_SIDE * MAX_SIDE
MAX_VIDEO_SECONDS = 3 * 3600
MAX_BOXES = 20_000
MAX_DEPTH = 12
MAX_PDF_OBJECTS = 200_000
MAX_PDF_PAGES = 2_000


class Rejected(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------------------- signature
def sniff(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head[4:8] == b"ftyp":
        return "video/mp4"
    text = head[3:] if head.startswith(b"\xef\xbb\xbf") else head
    if text.startswith(b"WEBVTT") and (len(text) == 6 or text[6:7] in (b" ", b"\t", b"\n", b"\r")):
        return "text/vtt"
    return None


# --------------------------------------------------------------------------------------- images
def image(src: Path, dst: Path, mime: str) -> dict:
    from PIL import Image, ImageFile

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # au-delà : DecompressionBombError
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    expected = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[mime]
    try:
        with Image.open(src) as probe:
            if probe.format != expected:
                raise Rejected("media_type_mismatch", "Format d'image différent du type annoncé")
            w, h = probe.size
            if w < 1 or h < 1 or w > MAX_SIDE or h > MAX_SIDE:
                raise Rejected("image_dimensions", f"Dimensions hors limites ({w}×{h}, maximum {MAX_SIDE} px)")
            if getattr(probe, "n_frames", 1) != 1:
                raise Rejected("image_animated", "Images animées refusées")
            probe.verify()
        with Image.open(src) as img:
            img.load()  # décodage complet : une image tronquée ou corrompue échoue ici
            mode = img.mode
            if expected == "JPEG":
                target = "L" if mode in ("L", "1") else "RGB"
            elif mode in ("1", "L", "LA", "RGB", "RGBA"):
                target = mode
            else:
                target = "RGBA" if "A" in mode or "transparency" in img.info else "RGB"
            clean = img.convert(target)
            clean.info = {}  # aucune métadonnée ne survit (EXIF, XMP, ICC, textes PNG, GPS)
            options: dict = {}
            if expected == "JPEG":
                options = {"quality": 90, "optimize": True}
            elif expected == "WEBP":
                options = {"quality": 90, "method": 4}
            else:
                options = {"optimize": True}
            clean.save(dst, format=expected, **options)
            out = {"width": clean.width, "height": clean.height}
    except Rejected:
        raise
    except Image.DecompressionBombError:
        raise Rejected("image_dimensions", "Image trop grande (bombe de décompression)") from None
    except Exception:
        raise Rejected("image_invalid", "Image illisible ou corrompue") from None
    with Image.open(dst) as check:  # contrôle de sortie : aucune métadonnée résiduelle
        if check.getexif() or any(k in check.info for k in ("exif", "xmp", "icc_profile", "XML:com.adobe.xmp")):
            raise Rejected("image_invalid", "Métadonnées résiduelles après réencodage")
    return out


# --------------------------------------------------------------------------------------- PDF
RAW_PDF_TOKENS = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/EmbeddedFiles", b"/XFA", b"/RichMedia")
FORBIDDEN_PDF_KEYS = {
    "/JS",
    "/JavaScript",
    "/AA",
    "/OpenAction",
    "/Launch",
    "/EmbeddedFile",
    "/EmbeddedFiles",
    "/XFA",
    "/RichMedia",
    "/RichMediaContent",
}
FORBIDDEN_PDF_ACTIONS = {
    "/JavaScript",
    "/Launch",
    "/SubmitForm",
    "/ImportData",
    "/GoToE",
    "/Rendition",
    "/Sound",
    "/Movie",
}


def pdf(src: Path, dst: Path) -> dict:
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    raw = src.read_bytes()
    # Polyglotte : rien d'autre que des blancs après le dernier %%EOF (archive ou HTML accolé).
    eof = raw.rfind(b"%%EOF")
    if eof < 0 or raw[eof + 5 :].strip(b" \t\r\n\x00"):
        raise Rejected("pdf_invalid", "Données hors structure PDF")
    # Défense en profondeur : jetons actifs visibles en clair, y compris dans des objets non référencés
    # (révisions incrémentales antérieures) que le parcours structurel ne visite pas.
    for token in RAW_PDF_TOKENS:
        if re.search(re.escape(token) + rb"(?![A-Za-z0-9])", raw):
            raise Rejected("pdf_active_content", f"Contenu actif interdit ({token[1:].decode()})")
    try:
        reader = PdfReader(src, strict=True)
        if reader.is_encrypted or "/Encrypt" in reader.trailer:
            raise Rejected("pdf_encrypted", "PDF chiffré refusé")
        pages = len(reader.pages)
        if pages < 1 or pages > MAX_PDF_PAGES:
            raise Rejected("pdf_invalid", f"Nombre de pages hors limites ({pages})")
        seen: set[tuple[int, int]] = set()
        stack: list = [reader.trailer]
        visited = 0
        while stack:
            node = stack.pop()
            if isinstance(node, IndirectObject):
                ref = (node.idnum, node.generation)
                if ref in seen:
                    continue
                seen.add(ref)
                node = node.get_object()
            visited += 1
            if visited > MAX_PDF_OBJECTS:
                raise Rejected("pdf_invalid", "Structure PDF trop volumineuse")
            if isinstance(node, DictionaryObject):
                for key, value in node.items():
                    if key in FORBIDDEN_PDF_KEYS:
                        raise Rejected("pdf_active_content", f"Contenu actif interdit ({key[1:]})")
                    if key == "/S" and value in FORBIDDEN_PDF_ACTIONS:
                        raise Rejected("pdf_active_content", f"Action interdite ({str(value)[1:]})")
                    if key == "/Subtype" and value in ("/FileAttachment", "/RichMedia", "/Movie", "/Sound", "/Screen"):
                        raise Rejected("pdf_active_content", f"Annotation interdite ({str(value)[1:]})")
                    stack.append(value)
            elif isinstance(node, ArrayObject):
                stack.extend(node)
    except Rejected:
        raise
    except Exception:
        raise Rejected("pdf_invalid", "PDF illisible ou non conforme") from None
    shutil.copyfile(src, dst)  # aucun réécriture : le fichier validé est conservé tel quel
    return {"pages": pages}


# --------------------------------------------------------------------------------------- MP4
TOP_LEVEL = {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pdin"}
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"dinf", b"udta", b"mvex"}
BRANDS = {b"isom", b"iso2", b"iso3", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1", b"M4V "}
HANDLERS = {b"vide", b"soun"}
CODECS = {b"avc1", b"avc3", b"hvc1", b"hev1", b"av01", b"vp09", b"mp4a", b"Opus"}


def _boxes(f, start: int, end: int, depth: int, counter: list[int]):
    """Itère (type, début de charge utile, fin) sur [start, end) avec contrôle strict des tailles."""
    pos = start
    while pos < end:
        counter[0] += 1
        if counter[0] > MAX_BOXES or depth > MAX_DEPTH:
            raise Rejected("mp4_invalid", "Structure MP4 trop complexe")
        if end - pos < 8:
            raise Rejected("mp4_invalid", "Boîte MP4 tronquée")
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:
            if end - pos < 16:
                raise Rejected("mp4_invalid", "Boîte MP4 tronquée")
            (size,) = struct.unpack(">Q", f.read(8))
            header = 16
        elif size == 0:
            if depth != 0:
                raise Rejected("mp4_invalid", "Taille de boîte nulle hors du niveau racine")
            size = end - pos
        if size < header or pos + size > end:
            raise Rejected("mp4_invalid", "Taille de boîte MP4 incohérente")
        yield kind, pos + header, pos + size
        pos += size


def mp4(src: Path, dst: Path) -> dict:
    total = src.stat().st_size
    counter = [0]
    seen: dict[bytes, int] = {}
    info: dict[str, Any] = {"duration_seconds": 0.0, "tracks": [], "codecs": []}
    with src.open("rb") as f:
        first = True
        for kind, body, end in _boxes(f, 0, total, 0, counter):
            if first:
                if kind != b"ftyp":
                    raise Rejected("media_type_mismatch", "Le conteneur MP4 doit commencer par ftyp")
                f.seek(body)
                major = f.read(4)
                f.read(4)
                compatible = {f.read(4) for _ in range((end - body - 8) // 4)}
                if major not in BRANDS and not (compatible & BRANDS):
                    raise Rejected("mp4_invalid", "Marque MP4 non autorisée")
                first = False
            if kind not in TOP_LEVEL:
                raise Rejected("mp4_invalid", f"Boîte de premier niveau non autorisée ({kind.decode('latin-1')!r})")
            seen[kind] = seen.get(kind, 0) + 1
            if kind == b"moov":
                _moov(f, body, end, counter, info)
        if seen.get(b"moov") != 1 or not seen.get(b"mdat") or seen.get(b"ftyp") != 1:
            raise Rejected("mp4_invalid", "MP4 incomplet (ftyp, moov et mdat obligatoires, moov unique)")
    if b"vide" not in {t.encode() for t in info["tracks"]}:
        raise Rejected("mp4_invalid", "Aucune piste vidéo")
    shutil.copyfile(src, dst)
    return info


def _moov(f, start: int, end: int, counter: list[int], info: dict) -> None:
    mvhd = 0

    def walk(s: int, e: int, depth: int, track: dict | None) -> None:
        nonlocal mvhd
        for kind, body, stop in _boxes(f, s, e, depth, counter):
            if kind == b"mvhd":
                mvhd += 1
                f.seek(body)
                version = f.read(1)[0]
                f.read(3)
                if version == 1:
                    f.read(16)
                    timescale, duration = struct.unpack(">IQ", f.read(12))
                else:
                    f.read(8)
                    timescale, duration = struct.unpack(">II", f.read(8))
                if timescale == 0:
                    raise Rejected("mp4_invalid", "Échelle de temps nulle")
                seconds = duration / timescale
                if seconds > MAX_VIDEO_SECONDS:
                    raise Rejected("mp4_too_long", f"Durée supérieure à {MAX_VIDEO_SECONDS // 3600} h")
                info["duration_seconds"] = round(seconds, 3)
            elif kind == b"trak":
                current: dict = {"handler": None, "codecs": []}
                walk(body, stop, depth + 1, current)
                if current["handler"] not in HANDLERS:
                    raise Rejected("mp4_invalid", "Piste non autorisée (seules vidéo et audio)")
                if not current["codecs"]:
                    raise Rejected("mp4_invalid", "Piste sans description de codec")
                info["tracks"].append(current["handler"].decode())
                info["codecs"].extend(c.decode("latin-1") for c in current["codecs"])
            elif kind == b"hdlr" and track is not None:
                f.seek(body + 8)
                track["handler"] = f.read(4)
            elif kind == b"stsd" and track is not None:
                f.seek(body + 4)
                (count,) = struct.unpack(">I", f.read(4))
                if count < 1 or count > 16:
                    raise Rejected("mp4_invalid", "Description de codec incohérente")
                for entry, _, _ in _boxes(f, body + 8, stop, depth + 1, counter):
                    if entry not in CODECS:
                        raise Rejected("mp4_codec", f"Codec non autorisé ({entry.decode('latin-1')!r})")
                    track["codecs"].append(entry)
            elif kind in CONTAINERS:
                walk(body, stop, depth + 1, track)

    walk(start, end, 1, None)
    if mvhd != 1:
        raise Rejected("mp4_invalid", "En-tête de film (mvhd) absent ou multiple")


# --------------------------------------------------------------------------------------- WebVTT
TIMING = re.compile(
    r"^(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}[ \t]+-->[ \t]+(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}(?:[ \t]+[^\n<>]*)?$"
)
ALLOWED_TAG = re.compile(
    r"</?(?:b|i|u|ruby|rt)>|</c>|<c(?:\.[A-Za-z0-9_-]{1,32}){0,4}>"
    r"|</v>|<v(?:\.[A-Za-z0-9_-]{1,32}){0,4}[ \t][^<>&\n]{1,64}>"
    r"|</lang>|<lang [A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8}){0,3}>|<(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}>"
)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_cue(line: str) -> str:
    out, pos = [], 0
    for match in re.finditer(r"<[^<>]*>?", line):
        out.append(line[pos : match.start()].replace("<", "&lt;").replace(">", "&gt;"))
        tag = match.group(0)
        out.append(tag if ALLOWED_TAG.fullmatch(tag) else tag.replace("<", "&lt;").replace(">", "&gt;"))
        pos = match.end()
    out.append(line[pos:].replace("<", "&lt;").replace(">", "&gt;"))
    return "".join(out)


def vtt(src: Path, dst: Path) -> dict:
    raw = src.read_bytes()
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        raise Rejected("vtt_invalid", "Sous-titres non encodés en UTF-8") from None
    if CONTROL.search(text):
        raise Rejected("vtt_invalid", "Caractères de contrôle interdits")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not re.fullmatch(r"WEBVTT(?:[ \t][^\n]*)?", lines[0]):
        raise Rejected("vtt_invalid", "En-tête WEBVTT absent")
    out = ["WEBVTT"]
    cues, i, n = 0, 1, len(lines)
    while i < n and lines[i].strip():  # en-tête facultatif : ignoré (non réécrit)
        i += 1
    while i < n:
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break
        block = []
        while i < n and lines[i].strip():
            block.append(lines[i])
            i += 1
        head = block[0]
        if head.startswith("STYLE"):
            raise Rejected("vtt_invalid", "Blocs STYLE (CSS) refusés")
        if head.startswith("NOTE") or head.startswith("REGION"):
            continue  # commentaires et régions : retirés
        timing_index = 0 if "-->" in head else 1
        if timing_index >= len(block) or not TIMING.fullmatch(block[timing_index].strip()):
            raise Rejected("vtt_invalid", "Minutage de sous-titre invalide")
        cue = []
        if timing_index == 1:
            cue.append(re.sub(r"[<>&]", "", head)[:120])
        cue.append(block[timing_index].strip())
        cue.extend(_sanitize_cue(line) for line in block[timing_index + 1 :])
        out.append("")
        out.extend(cue)
        cues += 1
    if cues == 0:
        raise Rejected("vtt_invalid", "Aucun sous-titre")
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"cues": cues}


# --------------------------------------------------------------------------------------- entrée
def run(mime: str, src: Path, dst: Path) -> dict:
    with src.open("rb") as f:
        head = f.read(64)
    detected = sniff(head)
    if detected is None:
        raise Rejected("media_type_forbidden", "Format non autorisé (SVG, HTML, archives, exécutables refusés)")
    if detected != mime:
        raise Rejected("media_type_mismatch", "Le contenu réel ne correspond pas au type annoncé")
    if mime.startswith("image/"):
        details = image(src, dst, mime)
    elif mime == "application/pdf":
        details = pdf(src, dst)
    elif mime == "video/mp4":
        details = mp4(src, dst)
    elif mime == "text/vtt":
        details = vtt(src, dst)
    else:
        raise Rejected("media_type_forbidden", "Format non autorisé")
    return {"ok": True, "mime": mime, **details}


def main(argv: list[str]) -> int:
    mime, src, dst = argv[1], Path(argv[2]), Path(argv[3])
    try:
        result = run(mime, src, dst)
    except Rejected as exc:
        result = {"ok": False, "code": exc.code, "detail": exc.detail}
    except MemoryError:
        result = {"ok": False, "code": "media_resources", "detail": "Ressources dépassées pendant l'analyse"}
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
