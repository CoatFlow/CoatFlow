"""
Test voor _haal_html() in product_import.py: de response-body moet BEGRENSD
gelezen worden (stream=True + iter_content-afkapping), niet eerst volledig
opgehaald en dan pas afgekapt (resp.text[:max_bytes]). Regressietest voor een
Klein-audit-fix (geheugenrisico): een kwaadaardige/kapotte pagina die een enorme
response terugstuurt kon voorheen het volledige ding in het geheugen trekken
vóórdat de afkapping toesloeg.

Draait met een nep-'requests'-module (geen echt netwerkverkeer).
"""
import sys
import types

import product_import as pi


def _install_fake_requests(monkeypatch, chunks, headers=None):
    class FakeResp:
        def __init__(self):
            self.status_code = 200
            self.headers = headers or {}
            self.closed = False

        def iter_content(self, chunk_size=65536):
            for c in chunks:
                yield c

        def close(self):
            self.closed = True

    class exceptions:
        class Timeout(Exception):
            pass

        class ConnectionError(Exception):
            pass

    calls = {"n": 0}

    def fake_get(url, headers=None, timeout=None, allow_redirects=None, stream=None):
        calls["n"] += 1
        assert stream is True, "moet stream=True gebruiken, anders wordt de hele body alsnog opgehaald"
        return FakeResp()

    def fake_get_encoding_from_headers(hdrs):
        return (hdrs or {}).get("_charset")

    fake_utils = types.SimpleNamespace(get_encoding_from_headers=fake_get_encoding_from_headers)
    fake_requests = type("FakeRequestsModule", (), {
        "get": staticmethod(fake_get), "exceptions": exceptions, "utils": fake_utils,
    })
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    return calls


def test_grote_response_wordt_afgekapt_op_max_bytes(monkeypatch):
    """Kernscenario: een pagina die (veel) meer dan max_bytes terugstuurt, mag nooit
    meer dan max_bytes in tekst opleveren."""
    max_bytes = 1000
    # 20 chunks van 200 bytes = 4000 bytes, ruim boven de cap.
    chunks = [b"a" * 200 for _ in range(20)]
    _install_fake_requests(monkeypatch, chunks)

    tekst, fout = pi._haal_html("https://leverancier.nl/product", max_bytes=max_bytes)

    assert fout is None
    assert len(tekst) <= max_bytes, (
        f"BUG NIET GEFIXT: {len(tekst)} bytes tekst teruggegeven, max_bytes was {max_bytes}"
    )


def test_iter_content_stopt_zodra_de_cap_bereikt_is(monkeypatch):
    """De fix moet ook daadwerkelijk STOPPEN met lezen zodra de cap bereikt is,
    niet alleen achteraf afkappen — anders is de fix zinloos tegen een oneindige
    stream. We geven een generator die na de cap een uitzondering gooit als er
    nog meer aangevraagd wordt."""
    max_bytes = 500

    class FakeResp:
        def __init__(self):
            self.status_code = 200
            self.headers = {}
            self.gelezen = 0

        def iter_content(self, chunk_size=65536):
            for _ in range(1000):   # zou 1000*100=100.000 bytes zijn zonder de cap
                self.gelezen += 100
                if self.gelezen > max_bytes * 3:
                    raise AssertionError("bleef lezen ver voorbij de cap — stream niet afgebroken")
                yield b"x" * 100

        def close(self):
            pass

    class exceptions:
        class Timeout(Exception):
            pass

        class ConnectionError(Exception):
            pass

    def fake_get(url, headers=None, timeout=None, allow_redirects=None, stream=None):
        return FakeResp()

    fake_utils = types.SimpleNamespace(get_encoding_from_headers=lambda h: None)
    fake_requests = type("FakeRequestsModule", (), {
        "get": staticmethod(fake_get), "exceptions": exceptions, "utils": fake_utils,
    })
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    tekst, fout = pi._haal_html("https://leverancier.nl/product", max_bytes=max_bytes)

    assert fout is None
    assert len(tekst) <= max_bytes


def test_kleine_response_blijft_gewoon_werken(monkeypatch):
    """Regressiecheck: een normale, kleine pagina moet nog gewoon de volledige
    tekst teruggeven (de fix mag legitieme, kleine responses niet breken)."""
    _install_fake_requests(monkeypatch, [b"<html><body>Product X - EUR 12,50</body></html>"])

    tekst, fout = pi._haal_html("https://leverancier.nl/product")

    assert fout is None
    assert "Product X" in tekst
    assert "12,50" in tekst


def test_header_charset_wordt_gebruikt_indien_aanwezig(monkeypatch):
    """De header-charset-detectie (get_encoding_from_headers) moet nog werken na
    de omzetting naar handmatig decoderen."""
    ruw = "Kwaliteitsverf".encode("iso-8859-1")
    _install_fake_requests(monkeypatch, [ruw], headers={"_charset": "iso-8859-1"})

    tekst, fout = pi._haal_html("https://leverancier.nl/product")

    assert fout is None
    assert "Kwaliteitsverf" in tekst
