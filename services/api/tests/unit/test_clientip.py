"""Tests unitaires de l'identification du client derrière des mandataires de confiance."""

from __future__ import annotations

import asyncio

from app.core.clientip import TrustedProxies


def run(coro):
    return asyncio.run(coro)


def test_forwarded_for_ignored_without_trusted_proxies():
    tp = TrustedProxies([])
    assert run(tp.client_address("10.0.0.5", "203.0.113.7")) == "10.0.0.5"


def test_forwarded_for_ignored_from_untrusted_peer():
    tp = TrustedProxies(["10.0.0.0/8"])
    assert run(tp.client_address("192.0.2.1", "203.0.113.7")) == "192.0.2.1"


def test_rightmost_untrusted_hop_is_the_client():
    tp = TrustedProxies(["10.0.0.0/8"])
    # Le client a falsifié la première valeur ; le mandataire a ajouté la vraie adresse.
    assert run(tp.client_address("10.0.0.5", "1.2.3.4, 203.0.113.7, 10.0.0.9")) == "203.0.113.7"


def test_invalid_or_oversized_header_is_ignored():
    tp = TrustedProxies(["10.0.0.0/8"])
    assert run(tp.client_address("10.0.0.5", "pas-une-adresse")) == "10.0.0.5"
    assert run(tp.client_address("10.0.0.5", ", ".join(["203.0.113.7"] * 30))) == "10.0.0.5"


def test_ipv6_and_literal_hostname():
    tp = TrustedProxies(["::1", "testclient"])
    assert run(tp.client_address("::1", "2001:db8::1")) == "2001:db8::1"
    assert run(tp.client_address("testclient", "203.0.113.9")) == "203.0.113.9"


def test_hostname_resolution_and_absent_host():
    tp = TrustedProxies(["localhost", "hote-inexistant.invalid"])
    assert run(tp.is_trusted("127.0.0.1")) is True
    assert run(tp.is_trusted("192.0.2.44")) is False
