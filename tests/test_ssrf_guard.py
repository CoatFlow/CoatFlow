"""
Tests voor de SSRF-afscherming in product_import.py: elke hostnaam moet vóór het
verbinden echt worden opgelost en getoetst tegen privé/lokale/gereserveerde
IP-ranges, en een redirect naar zo'n adres moet geblokkeerd worden vóórdat de
server het daadwerkelijk aanvraagt. Regressietest voor een kritieke audit-fix.

De publieke-domein-tests (test_publiek_ip_is_veilig, test_normale_winkelpagina_is_veilig)
hebben een echte DNS-resolutie nodig en worden overgeslagen zonder internetverbinding.
"""
import socket

import pytest

import product_import as pi


@pytest.mark.parametrize("url,omschrijving", [
    ("http://169.254.169.254/latest/meta-data/", "cloud-metadata IP"),
    ("http://127.0.0.1:8501/", "loopback"),
    ("http://10.0.0.5/", "RFC1918 10.x"),
    ("http://192.168.1.1/", "RFC1918 192.168.x"),
    ("http://172.16.0.1/", "RFC1918 172.16.x"),
    ("http://0.0.0.0/", "unspecified"),
    ("http://[::1]/", "IPv6 loopback"),
    ("http://localhost/", "geen punt in hostnaam"),
    ("ftp://8.8.8.8/", "verkeerd schema"),
    ("niet-een-url", "geen schema"),
    ("http://has spaces.nl/", "spatie in hostnaam"),
])
def test_interne_of_ongeldige_adressen_geweigerd(url, omschrijving):
    assert pi._url_is_safe(url) is False, f"had geweigerd moeten worden: {omschrijving} ({url})"


def _heeft_internet():
    try:
        socket.getaddrinfo("www.gamma.nl", None)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _heeft_internet(), reason="geen internetverbinding voor DNS-resolutie")
def test_publiek_ip_is_veilig():
    assert pi._url_is_safe("http://8.8.8.8/") is True


@pytest.mark.skipif(not _heeft_internet(), reason="geen internetverbinding voor DNS-resolutie")
def test_normale_winkelpagina_is_veilig():
    assert pi._url_is_safe("https://www.gamma.nl/product/voorbeeld") is True


def test_redirect_naar_intern_adres_wordt_geblokkeerd_voor_de_tweede_hop(monkeypatch):
    """End-to-end door _haal_html met een nep-'requests'-module: bewijst dat de
    validatie ook echt in de call-flow zit (niet alleen in de standalone helper)."""

    class FakeResp:
        def __init__(self, status_code, headers=None, text=""):
            self.status_code = status_code
            self.headers = headers or {}
            self.text = text
            self.apparent_encoding = "utf-8"
            self.encoding = "utf-8"

    class exceptions:
        class Timeout(Exception):
            pass

        class ConnectionError(Exception):
            pass

    calls = []

    def fake_get(url, headers=None, timeout=None, allow_redirects=None):
        calls.append(url)
        if url == "https://leverancier.nl/product":
            return FakeResp(302, headers={"Location": "http://169.254.169.254/geheim"})
        raise AssertionError(f"onverwachte URL aangevraagd: {url}")

    fake_requests = type("FakeRequestsModule", (), {"get": staticmethod(fake_get),
                                                     "exceptions": exceptions})
    monkeypatch.setitem(__import__("sys").modules, "requests", fake_requests)

    tekst, fout = pi._haal_html("https://leverancier.nl/product")

    assert tekst is None
    assert "169.254.169.254" not in " ".join(calls)
    assert calls == ["https://leverancier.nl/product"], (
        f"er is meer aangevraagd dan alleen de eerste (veilige) hop: {calls}"
    )
