"""Identification du client derrière des mandataires de confiance (reverse proxy, serveur Web).

L'en-tête `X-Forwarded-For` n'est lu que si le pair TCP direct est un mandataire déclaré dans
`TRUSTED_PROXIES` ; sinon il est ignoré (il serait falsifiable par n'importe quel client).
On retient alors l'adresse la plus à droite qui n'est pas elle-même un mandataire de confiance.

Entrées acceptées dans `TRUSTED_PROXIES` :
- une adresse ou un réseau CIDR (`10.0.0.0/8`, `::1`) ;
- un nom d'hôte (`web` dans Docker Compose), résolu de façon asynchrone et mis en cache 60 s,
  pour suivre un conteneur recréé avec une nouvelle adresse.

Compatible avec plusieurs instances : aucune donnée locale n'est nécessaire, seules la
configuration et l'en-tête de la requête comptent.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time

log = logging.getLogger("nurselearn.clientip")

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
_RESOLVE_TTL = 60.0
_MAX_FORWARDED = 20  # au-delà, l'en-tête est jugé anormal et ignoré


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None


class TrustedProxies:
    def __init__(self, entries: list[str]) -> None:
        self.networks: list[IPNetwork] = []
        self.hostnames: list[str] = []
        for raw in entries:
            entry = raw.strip()
            if not entry:
                continue
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self.hostnames.append(entry)
        self._resolved: dict[str, tuple[float, set[str]]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.networks or self.hostnames)

    async def _addresses_of(self, host: str) -> set[str]:
        cached = self._resolved.get(host)
        now = time.monotonic()
        if cached and now - cached[0] < _RESOLVE_TTL:
            return cached[1]
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
            addresses = {str(info[4][0]) for info in infos}
        except OSError:
            addresses = set()  # hôte absent (ex. conteneur arrêté) : rien n'est approuvé
        self._resolved[host] = (now, addresses)
        return addresses

    async def is_trusted(self, peer: str) -> bool:
        if peer in self.hostnames:  # nom littéral (ex. « testclient » dans les tests)
            return True
        ip = _parse_ip(peer)
        if ip is not None and any(ip in net for net in self.networks):
            return True
        for host in self.hostnames:
            if peer in await self._addresses_of(host):
                return True
        return False

    async def client_address(self, peer: str | None, forwarded_for: str | None) -> str:
        """Adresse du client à retenir pour la limitation de débit."""
        peer = peer or "inconnu"
        if not forwarded_for or not self.enabled or not await self.is_trusted(peer):
            return peer
        hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
        if not hops or len(hops) > _MAX_FORWARDED:
            return peer
        for hop in reversed(hops):
            if _parse_ip(hop) is None:
                return peer  # valeur invalide : on ne s'y fie pas
            if not await self.is_trusted(hop):
                return hop
        return hops[0]
