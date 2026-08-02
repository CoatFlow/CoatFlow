"""
Tests voor _pdf_cache_key(): voor een NIET-bevroren (Concept) project rekent
bereken_onderdeel() met LIVE productprijzen/uurtarieven. Zonder producten/personeel
in de cache-key bleef de key ongewijzigd na zo'n prijswijziging, dus kreeg de
@st.cache_data-gecachete PDF-generator de OUDE (verouderde) inhoud terug — een
klant kon zo een PDF met een ander totaal ontvangen dan wat het scherm toonde.
Regressietest voor een Belangrijk-audit-fix.

Draait tegen de ECHTE _pdf_cache_key() (AST-extractie) — geen Streamlit-runtime
nodig.
"""
import hashlib
import json

from _schildertool_extract import extract, FakeSt

NAMES = ("_pdf_cache_key",)

PROJECT = {"id": 1, "naam": "Test", "onderdelen": []}
INSTELLINGEN = {"bedrijfsnaam": "Test BV"}
PRODUCTEN_V1 = [{"id": 1, "naam": "Muurverf", "prijs": 50.0}]
PRODUCTEN_V2 = [{"id": 1, "naam": "Muurverf", "prijs": 100.0}]
PERSONEEL_V1 = [{"id": 1, "naam": "Piet", "uurtarief": 40.0}]
PERSONEEL_V2 = [{"id": 1, "naam": "Piet", "uurtarief": 60.0}]


def _maak_ns(instellingen, producten, personeel):
    st = FakeSt(instellingen=instellingen, producten=producten, personeel=personeel)
    return extract(*NAMES, extra_globals={"st": st, "hashlib": hashlib, "json": json})


def test_prijswijziging_verandert_de_cache_key():
    ns = _maak_ns(INSTELLINGEN, PRODUCTEN_V1, PERSONEEL_V1)
    key_voor = ns["_pdf_cache_key"](PROJECT)

    ns["st"].session_state["producten"] = PRODUCTEN_V2

    key_na = ns["_pdf_cache_key"](PROJECT)

    assert key_na != key_voor, (
        "BUG NIET GEFIXT: een productprijswijziging verandert de PDF-cache-key niet — "
        "de gegenereerde PDF blijft dan het oude (verouderde) bedrag tonen"
    )


def test_uurtariefwijziging_verandert_de_cache_key():
    ns = _maak_ns(INSTELLINGEN, PRODUCTEN_V1, PERSONEEL_V1)
    key_voor = ns["_pdf_cache_key"](PROJECT)

    ns["st"].session_state["personeel"] = PERSONEEL_V2

    key_na = ns["_pdf_cache_key"](PROJECT)

    assert key_na != key_voor


def test_identieke_inhoud_geeft_identieke_key():
    """Regressiecheck: de fix mag de cache niet 'kapot' maken door willekeurige/
    niet-deterministische keys te genereren voor exact dezelfde inhoud."""
    ns1 = _maak_ns(INSTELLINGEN, PRODUCTEN_V1, PERSONEEL_V1)
    ns2 = _maak_ns(dict(INSTELLINGEN), [dict(p) for p in PRODUCTEN_V1], [dict(p) for p in PERSONEEL_V1])

    assert ns1["_pdf_cache_key"](PROJECT) == ns2["_pdf_cache_key"](dict(PROJECT))


def test_instellingenwijziging_blijft_ook_gedetecteerd():
    """Bestaand gedrag (SP-007) mag niet regresseren door deze fix."""
    ns = _maak_ns(INSTELLINGEN, PRODUCTEN_V1, PERSONEEL_V1)
    key_voor = ns["_pdf_cache_key"](PROJECT)

    ns["st"].session_state["instellingen"] = {"bedrijfsnaam": "Andere Naam BV"}

    key_na = ns["_pdf_cache_key"](PROJECT)
    assert key_na != key_voor
