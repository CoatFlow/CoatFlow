"""
Tests voor de prijs-snapshot: een bevroren offerte/factuur moet overal (scherm, PDF
EN Word-sjabloon) dezelfde, bevroren regelbedragen tonen — ook nadat een
productprijs later wijzigt. Regressietest voor een kritieke audit-fix: het
Word-sjabloon-pad (_sjb_context) rekende eerder rechtstreeks via bereken_onderdeel()
met LIVE prijzen i.p.v. via de snapshot-aware bereken_onderdelen_lijst(), waardoor
een regelbedrag het eigen printtotaal kon tegenspreken.

Draait tegen de ECHTE functies uit SchilderTool1.py (AST-extractie) — geen
Streamlit-runtime nodig.
"""
import hashlib
import json
from datetime import date

from _schildertool_extract import extract, FakeSt

CALC_NAMES = (
    "is_meter_product", "verbruik_eenheid_van", "PRODUCTIE_NORMEN",
    "_FALLBACK_UUR_PER_M2", "_FALLBACK_UUR_PER_M1", "_HOUTWERK_TERUGVAL_UUR_PER_M2",
    "_f", "_houtwerk_uren", "auto_arbeidsuren", "HOUTWERK_LAGEN", "HOUTWERK_NORMEN",
    "houtwerk_effectief_m2", "_CALC_DUAL_WZ", "METER_CATEGORIEEN", "METER_WERKZAAMHEDEN",
    "bereken_onderdeel",
)
SNAPSHOT_NAMES = (
    "materieel_regels", "materieel_totaal", "_bereken_project_totaal_live",
    "FROZEN_STATUSSEN", "_calc_inputs_hash", "_snapshot_actief", "maak_prijs_snapshot",
    "verzeker_prijs_snapshot", "bereken_project_totaal", "bereken_onderdelen_lijst",
)

PERSONEEL = [{"id": 1, "naam": "Piet", "uurtarief": 40.0, "actief": True}]


def _maak_ns(instellingen, producten, personeel=PERSONEEL):
    st = FakeSt(instellingen=instellingen, producten=producten, personeel=personeel,
               projecten=[])
    return extract(*CALC_NAMES, *SNAPSHOT_NAMES,
                   extra_globals={"st": st, "hashlib": hashlib, "json": json, "date": date})


def _project(**overrides):
    p = {
        "id": 1, "status": "Offerte verzonden",   # FROZEN_STATUSSEN-lid
        "marge": 25, "btw": 21,
        "onderdelen": [
            {"m2": 20, "lagen": 2, "werkzaamheden": ["Muren schilderen"], "meters": 0},
        ],
        "materieel": [],
    }
    p.update(overrides)
    return p


INSTELLINGEN = {
    "toeslag_hoogte_pct": 10, "toeslag_spoed_pct": 20, "toeslag_buiten_pct": 10,
    "toeslag_weekend_pct": 50, "toeslag_avond_pct": 25, "toeslag_winter_pct": 10,
    "standaard_marge": 25, "standaard_btw": 21,
}
PRODUCT_V1 = [{"naam": "Muurverf", "prijs": 50.0, "inhoud": 10.0, "verbruik": 0.12,
              "werkzaamheden": ["Muren schilderen"], "actief": True}]
PRODUCT_V2_DUURDER = [{"naam": "Muurverf", "prijs": 100.0, "inhoud": 10.0, "verbruik": 0.12,
                       "werkzaamheden": ["Muren schilderen"], "actief": True}]


def test_bereken_onderdelen_lijst_gebruikt_snapshot_als_bevroren():
    """De kern van de fix: bereken_onderdelen_lijst() (nu gebruikt door zowel de
    native PDF als het Word-sjabloon) moet bij een bevroren project de VASTGELEGDE
    waarden teruggeven, niet opnieuw live doorrekenen."""
    ns = _maak_ns(INSTELLINGEN, PRODUCT_V1)
    project = _project()

    # Bevries de offerte op de huidige (lagere) prijs.
    ns["maak_prijs_snapshot"](project)
    bevroren_regel = ns["bereken_onderdelen_lijst"](project, project["marge"], project["btw"])[0]
    bevroren_totaal = ns["bereken_project_totaal"](project)

    # Simuleer: de schilder verhoogt de productprijs NA het versturen van de offerte.
    ns["st"].session_state["producten"] = PRODUCT_V2_DUURDER

    na_prijswijziging_regel = ns["bereken_onderdelen_lijst"](project, project["marge"], project["btw"])[0]
    na_prijswijziging_totaal = ns["bereken_project_totaal"](project)

    assert na_prijswijziging_regel == bevroren_regel, (
        "BUG: het regelbedrag verandert mee met een latere productprijs-wijziging, "
        "ook al is de offerte bevroren"
    )
    assert na_prijswijziging_totaal == bevroren_totaal


def test_regel_en_totaal_blijven_consistent_ook_na_prijswijziging():
    """Dit is exact het gerapporteerde bug-scenario: het regelbedrag (zoals het
    Word-sjabloon dat toont) mag nooit hoger worden dan het printtotaal."""
    ns = _maak_ns(INSTELLINGEN, PRODUCT_V1)
    project = _project()
    ns["maak_prijs_snapshot"](project)

    ns["st"].session_state["producten"] = [
        {"naam": "Muurverf", "prijs": 5000.0, "inhoud": 10.0, "verbruik": 0.12,
         "werkzaamheden": ["Muren schilderen"], "actief": True}
    ]

    regel = ns["bereken_onderdelen_lijst"](project, project["marge"], project["btw"])[0]
    totaal = ns["bereken_project_totaal"](project)

    assert regel["excl_btw"] <= totaal["excl_btw"], (
        f"BUG NIET GEFIXT: regelbedrag (€{regel['excl_btw']}) is hoger dan het "
        f"printtotaal (€{totaal['excl_btw']}) van diezelfde bevroren offerte"
    )


def test_oude_rechtstreekse_bereken_onderdeel_aanroep_zou_wel_zijn_meegewijzigd():
    """Documenteert WAAROM de fix nodig was: de oude aanpak (rechtstreeks
    bereken_onderdeel() aanroepen, zonder de snapshot-check) rekent wél live door.
    Dit bewijst dat bereken_onderdelen_lijst() daadwerkelijk ander gedrag geeft dan
    de vroegere, foute implementatie — niet toevallig gelijk uitkomt."""
    ns = _maak_ns(INSTELLINGEN, PRODUCT_V1)
    project = _project()
    ns["maak_prijs_snapshot"](project)

    ns["st"].session_state["producten"] = PRODUCT_V2_DUURDER
    ond = project["onderdelen"][0]
    live_direct = ns["bereken_onderdeel"](ond, project["marge"], project["btw"],
                                          project_id=project.get("id"))
    via_snapshot_helper = ns["bereken_onderdelen_lijst"](project, project["marge"], project["btw"])[0]

    assert live_direct["materiaal"] != via_snapshot_helper["materiaal"], (
        "als deze twee gelijk zijn is de test-opzet zelf niet onderscheidend genoeg"
    )


def test_niet_bevroren_project_rekent_gewoon_live():
    """Regressiecheck: een project dat NIET in een bevroren status staat (bv.
    'Concept') moet gewoon live doorrekenen — de fix mag dat niet blokkeren."""
    ns = _maak_ns(INSTELLINGEN, PRODUCT_V1)
    project = _project(status="Concept")   # geen FROZEN_STATUSSEN-lid, geen snapshot

    voor = ns["bereken_onderdelen_lijst"](project, project["marge"], project["btw"])[0]
    ns["st"].session_state["producten"] = PRODUCT_V2_DUURDER
    na = ns["bereken_onderdelen_lijst"](project, project["marge"], project["btw"])[0]

    assert voor["materiaal"] != na["materiaal"], (
        "een niet-bevroren (Concept) project moet gewoon met de actuele prijs rekenen"
    )
