"""Client minimal du démon clamd (protocole TCP documenté : PING, VERSION, INSTREAM).

Aucune dépendance externe. Toute réponse inattendue est une ERREUR, jamais un verdict sain :
seule la réponse exacte « stream: OK » vaut « aucun virus trouvé ».
"""

from __future__ import annotations

import re
import socket
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

CHUNK = 64 * 1024
_VERSION = re.compile(r"^ClamAV (?P<engine>[0-9][0-9A-Za-z.\-]*)/(?P<sig>[0-9]+)/(?P<date>.+)$")
_FOUND = re.compile(r"^stream: (?P<name>[A-Za-z0-9._:\-/]{1,200}) FOUND$")


class ClamdUnavailable(RuntimeError):
    """clamd injoignable ou connexion interrompue."""


class ClamdTimeout(RuntimeError):
    """Analyse non terminée dans le délai imparti."""


class ClamdError(RuntimeError):
    """Réponse d'erreur ou inattendue (taille dépassée, format inconnu…)."""


@dataclass(frozen=True)
class EngineInfo:
    engine_version: str
    signature_version: str
    signatures_published_at: datetime | None  # None = date illisible : jamais de verdict sain


@dataclass(frozen=True)
class ScanResult:
    infected: bool
    threat_name: str | None


def parse_version(line: str) -> EngineInfo:
    m = _VERSION.match(line.strip())
    if not m:
        raise ClamdError("réponse VERSION inattendue (signatures absentes ?)")
    published: datetime | None
    try:
        # Format de clamd : « Sat Nov 22 07:25:03 2026 », heure du démon (UTC dans l'image officielle).
        published = datetime.strptime(" ".join(m["date"].split()), "%a %b %d %H:%M:%S %Y").replace(tzinfo=UTC)
    except ValueError:
        published = None
    return EngineInfo(m["engine"], m["sig"], published)


class ClamdClient:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def _connect(self, timeout: float) -> socket.socket:
        try:
            return socket.create_connection((self.host, self.port), timeout=min(timeout, 10))
        except OSError as exc:
            raise ClamdUnavailable(type(exc).__name__) from None

    @staticmethod
    def _read(sock: socket.socket) -> str:
        buf = b""
        while not buf.endswith(b"\0"):
            try:
                part = sock.recv(4096)
            except TimeoutError:
                raise ClamdTimeout("délai dépassé") from None
            except OSError as exc:
                raise ClamdUnavailable(type(exc).__name__) from None
            if not part:
                raise ClamdUnavailable("connexion fermée par clamd")
            buf += part
            if len(buf) > 8192:
                raise ClamdError("réponse trop longue")
        return buf[:-1].decode("utf-8", "replace").strip()

    def _command(self, cmd: str, timeout: float = 10) -> str:
        with self._connect(timeout) as sock:
            sock.settimeout(timeout)
            try:
                sock.sendall(f"z{cmd}\0".encode())
            except OSError as exc:
                raise ClamdUnavailable(type(exc).__name__) from None
            return self._read(sock)

    def ping(self) -> bool:
        return self._command("PING") == "PONG"

    def version(self) -> EngineInfo:
        return parse_version(self._command("VERSION"))

    def scan_file(self, path: Path) -> ScanResult:
        with self._connect(self.timeout) as sock:
            sock.settimeout(self.timeout)
            try:
                sock.sendall(b"zINSTREAM\0")
                with path.open("rb") as f:
                    while chunk := f.read(CHUNK):
                        sock.sendall(struct.pack(">I", len(chunk)) + chunk)
                sock.sendall(struct.pack(">I", 0))
            except TimeoutError:
                raise ClamdTimeout("délai dépassé à l'envoi") from None
            except OSError as exc:
                raise ClamdUnavailable(type(exc).__name__) from None
            reply = self._read(sock)
        if reply == "stream: OK":
            return ScanResult(False, None)
        found = _FOUND.match(reply)
        if found:
            return ScanResult(True, found["name"])
        raise ClamdError("réponse d'analyse inattendue")
