"""Imitation MINIMALE de clamd, réservée à la mise au point locale sans Docker (comme moto pour S3).

Jamais utilisée en CI ni sous Windows : les tests y visent un ClamAV RÉEL (NL_TEST_CLAMD_HOST).
Protocole : zPING, zVERSION, zINSTREAM. Détecte uniquement le fichier de test EICAR.
Mode « hang » : accepte la connexion et ne répond jamais (délai d'analyse dépassé).
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from datetime import UTC, datetime


class FakeClamd:
    def __init__(self, mode: str = "normal", signature_age_hours: float = 1.0) -> None:
        self.mode = mode
        self.signature_age_hours = signature_age_hours
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self) -> None:
        self._stop = True
        self.sock.close()

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(30)
            try:
                cmd = b""
                while not cmd.endswith(b"\0"):
                    part = conn.recv(1)
                    if not part:
                        return
                    cmd += part
                if self.mode == "hang":
                    time.sleep(30)
                    return
                if cmd == b"zPING\0":
                    conn.sendall(b"PONG\0")
                elif cmd == b"zVERSION\0":
                    ts = time.time() - self.signature_age_hours * 3600
                    date = datetime.fromtimestamp(ts, UTC).strftime("%a %b %d %H:%M:%S %Y")
                    conn.sendall(f"ClamAV 1.5.5/27830/{date}\0".encode())
                elif cmd == b"zINSTREAM\0":
                    data = b""
                    while True:
                        (n,) = struct.unpack(">I", self._exact(conn, 4))
                        if n == 0:
                            break
                        data += self._exact(conn, n)
                    if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
                        conn.sendall(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
                    else:
                        conn.sendall(b"stream: OK\0")
            except OSError:
                return

    @staticmethod
    def _exact(conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            part = conn.recv(n - len(buf))
            if not part:
                raise OSError("fermé")
            buf += part
        return buf
