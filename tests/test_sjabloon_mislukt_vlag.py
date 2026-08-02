"""
Tests voor get_document_bytes(): een kapot/leeg-renderend Word-sjabloon moet
zichtbaar gemeld worden via 'sjabloon_mislukt', niet stil terugvallen op de
ingebouwde PDF zonder dat iemand het merkt. Regressietest voor een Belangrijk-
audit-fix: vóór de fix gaf 'sjabloon' in zowel "geen sjabloon geconfigureerd"
(normaal) als "sjabloon geconfigureerd maar render mislukt" (onverwacht) gewoon
False terug — een bedrijf kon zo een onbranded PDF naar een klant sturen zonder
enig signaal.

Draait tegen de ECHTE get_document_bytes() (AST-extractie); de duurdere
afhankelijkheden (sjabloon_ophalen, _sjb_render_cached, get_pdf_bytes,
get_factuur_bytes, _pdf_cache_key) worden als simpele stubs meegegeven.
"""
from _schildertool_extract import extract

NAMES = ("get_document_bytes",)

PROJECT = {"id": 1}
PDF_FALLBACK = {"bytes": b"%PDF-fallback", "b64": "ZmFrZQ==", "ext": "pdf", "mime": "application/pdf"}


def _maak_ns(sjabloon_ophalen, render_cached):
    return extract(*NAMES, extra_globals={
        "sjabloon_ophalen": sjabloon_ophalen,
        "_sjb_render_cached": render_cached,
        "_pdf_cache_key": lambda project: "key",
        "get_pdf_bytes": lambda project: dict(PDF_FALLBACK),
        "get_factuur_bytes": lambda project: dict(PDF_FALLBACK),
        "_SJB_MIME": {"pdf": "application/pdf"},
    })


def test_geen_sjabloon_geconfigureerd_is_geen_mislukking():
    ns = _maak_ns(sjabloon_ophalen=lambda soort: None,
                  render_cached=lambda *a, **k: (_ for _ in ()).throw(AssertionError("mag niet aangeroepen worden")))

    d = ns["get_document_bytes"](PROJECT, "offerte")

    assert d["sjabloon"] is False
    assert d["sjabloon_mislukt"] is False
    assert d["bytes"] == PDF_FALLBACK["bytes"]


def test_sjabloon_render_geslaagd():
    ok_result = {"bytes": b"docx-bytes", "b64": "ZG9jeA==", "ext": "docx", "mime": "application/vnd.ms-word"}
    ns = _maak_ns(sjabloon_ophalen=lambda soort: {"updated_at": "2026-01-01"},
                  render_cached=lambda *a, **k: dict(ok_result))

    d = ns["get_document_bytes"](PROJECT, "offerte")

    assert d["sjabloon"] is True
    assert d["sjabloon_mislukt"] is False
    assert d["bytes"] == ok_result["bytes"]


def test_sjabloon_geconfigureerd_maar_render_gooit_exceptie():
    def _boom(*a, **k):
        raise RuntimeError("kapot .docx-sjabloon")

    ns = _maak_ns(sjabloon_ophalen=lambda soort: {"updated_at": "2026-01-01"},
                  render_cached=_boom)

    d = ns["get_document_bytes"](PROJECT, "factuur")

    assert d["sjabloon"] is False
    assert d["sjabloon_mislukt"] is True, (
        "BUG NIET GEFIXT: een geconfigureerd maar kapot-renderend sjabloon moet "
        "gemeld worden, niet onzichtbaar terugvallen op de ingebouwde PDF"
    )
    assert d["bytes"] == PDF_FALLBACK["bytes"]


def test_sjabloon_geconfigureerd_maar_render_geeft_leeg_resultaat():
    ns = _maak_ns(sjabloon_ophalen=lambda soort: {"updated_at": "2026-01-01"},
                  render_cached=lambda *a, **k: None)

    d = ns["get_document_bytes"](PROJECT, "offerte")

    assert d["sjabloon"] is False
    assert d["sjabloon_mislukt"] is True
    assert d["bytes"] == PDF_FALLBACK["bytes"]
