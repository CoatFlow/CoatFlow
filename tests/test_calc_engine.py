"""
Tests voor de rekenkern (bereken_onderdeel): materiaalkosten, arbeidskosten,
toeslagen (additief, nooit samengesteld), marge/BTW-volgorde en afronding.
Draait tegen de ECHTE functie uit SchilderTool1.py (AST-extractie, zie
_schildertool_extract.py) — geen Streamlit-runtime nodig.

Dit bestand is mede het resultaat van de audit-bevinding "nul geautomatiseerde
tests rond de reken- en tenant-isolatielaag" — de rekenkern bepaalt bedragen die
naar echte klanten gaan en verdient een regressienet.
"""
from _schildertool_extract import extract, FakeSt

NAMES = (
    "is_meter_product", "verbruik_eenheid_van", "PRODUCTIE_NORMEN",
    "_FALLBACK_UUR_PER_M2", "_FALLBACK_UUR_PER_M1", "_HOUTWERK_TERUGVAL_UUR_PER_M2",
    "_f", "_houtwerk_uren", "auto_arbeidsuren", "HOUTWERK_LAGEN", "HOUTWERK_NORMEN",
    "houtwerk_effectief_m2", "_CALC_DUAL_WZ", "METER_CATEGORIEEN", "METER_WERKZAAMHEDEN",
    "bereken_onderdeel",
)

INSTELLINGEN = {
    "toeslag_hoogte_pct": 10, "toeslag_spoed_pct": 20, "toeslag_buiten_pct": 10,
    "toeslag_weekend_pct": 50, "toeslag_avond_pct": 25, "toeslag_winter_pct": 10,
}
PERSONEEL = [{"id": 1, "naam": "Piet", "uurtarief": 40.0, "actief": True}]


def _maak_bereken_onderdeel(instellingen=None, producten=None, personeel=None, projecten=None):
    """Bouw een verse bereken_onderdeel() met eigen (nep-)Streamlit-sessiestaat."""
    st = FakeSt(
        instellingen=instellingen if instellingen is not None else INSTELLINGEN,
        producten=producten or [], personeel=personeel or [], projecten=projecten or [],
    )
    ns = extract(*NAMES, extra_globals={"st": st})
    return ns["bereken_onderdeel"]


def _basis_onderdeel(**overrides):
    """20 uur arbeid, geen materiaal, geen toeslagen — makkelijk met de hand na te
    rekenen. arbeid_uren_override omzeilt bewust de arbeidsuren-schatting: die heeft
    zijn eigen expliciete tests hieronder."""
    ond = {
        "m2": 0, "lagen": 1, "werkzaamheden": [],
        "arbeid_uren_override": 10.0,   # 10u x EUR 40 = EUR 400 subtotaal
        "toeslag_hoogte": False, "toeslag_spoed": False, "toeslag_buiten": False,
        "toeslag_weekend": False, "toeslag_avond": False, "toeslag_winter": False,
    }
    ond.update(overrides)
    return ond


# ── Arbeidskosten ─────────────────────────────────────────────────────────
def test_arbeidskosten_is_uren_maal_gemiddeld_uurtarief():
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(), marge_pct=0, btw_pct=0)
    assert r["arbeid"] == 400.0
    assert r["subtotaal"] == 400.0


def test_geen_personeel_geeft_geen_arbeidskosten_geen_terugval_uurloon():
    """Bewust: geen standaard-uurloon-terugval als er geen (actief) personeel is."""
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=[])
    r = bereken_onderdeel(_basis_onderdeel(), marge_pct=0, btw_pct=0)
    assert r["arbeid"] == 0.0


# ── Materiaalkosten ───────────────────────────────────────────────────────
def test_materiaal_is_m2_x_lagen_x_verbruik_x_stukprijs():
    """stukprijs = verpakkingsprijs / inhoud (SP-AUDIT: niet de volle
    verpakkingsprijs per eenheid, anders veelvoud te hoog)."""
    producten = [{"naam": "Muurverf", "prijs": 50.0, "inhoud": 10.0, "verbruik": 0.12,
                 "werkzaamheden": ["Muren schilderen"], "actief": True}]
    bereken_onderdeel = _maak_bereken_onderdeel(producten=producten, personeel=[])
    ond = _basis_onderdeel(m2=20, lagen=2, werkzaamheden=["Muren schilderen"],
                           arbeid_uren_override=0)
    r = bereken_onderdeel(ond, marge_pct=0, btw_pct=0)
    # stukprijs = 50/10 = 5 EUR/liter; materiaal = 20 * 2 * 0.12 * 5 = 24
    assert r["materiaal"] == 24.0


def test_inactief_product_telt_niet_mee():
    producten = [{"naam": "Oude verf", "prijs": 999.0, "inhoud": 1.0, "verbruik": 1.0,
                 "werkzaamheden": ["Muren schilderen"], "actief": False}]
    bereken_onderdeel = _maak_bereken_onderdeel(producten=producten, personeel=[])
    ond = _basis_onderdeel(m2=20, lagen=1, werkzaamheden=["Muren schilderen"],
                           arbeid_uren_override=0)
    r = bereken_onderdeel(ond, marge_pct=0, btw_pct=0)
    assert r["materiaal"] == 0.0


def test_project_gescoped_product_telt_niet_mee_in_ander_project():
    producten = [{"naam": "Klantspecifieke verf", "prijs": 50.0, "inhoud": 10.0,
                 "verbruik": 0.12, "werkzaamheden": ["Muren schilderen"],
                 "actief": True, "project_id": 42}]
    bereken_onderdeel = _maak_bereken_onderdeel(producten=producten, personeel=[])
    ond = _basis_onderdeel(m2=20, lagen=1, werkzaamheden=["Muren schilderen"],
                           arbeid_uren_override=0)
    r_ander_project = bereken_onderdeel(ond, marge_pct=0, btw_pct=0, project_id=7)
    r_eigen_project = bereken_onderdeel(ond, marge_pct=0, btw_pct=0, project_id=42)
    assert r_ander_project["materiaal"] == 0.0
    assert r_eigen_project["materiaal"] > 0.0


# ── Toeslagen ─────────────────────────────────────────────────────────────
def test_toeslag_hoogte_percentage():
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(toeslag_hoogte=True), marge_pct=0, btw_pct=0)
    assert r["toeslagen"] == 40.0   # 400 * 10%


def test_toeslag_spoed_percentage():
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(toeslag_spoed=True), marge_pct=0, btw_pct=0)
    assert r["toeslagen"] == 80.0   # 400 * 20%


def test_toeslag_buiten_percentage():
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(toeslag_buiten=True), marge_pct=0, btw_pct=0)
    assert r["toeslagen"] == 40.0   # 400 * 10%


def test_toeslag_weekend_percentage():
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(toeslag_weekend=True), marge_pct=0, btw_pct=0)
    assert r["toeslagen"] == 200.0   # 400 * 50%


def test_toeslag_avond_percentage():
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(toeslag_avond=True), marge_pct=0, btw_pct=0)
    assert r["toeslagen"] == 100.0   # 400 * 25%


def test_toeslag_winter_percentage():
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(toeslag_winter=True), marge_pct=0, btw_pct=0)
    assert r["toeslagen"] == 40.0   # 400 * 10%


def test_meerdere_toeslagen_zijn_additief_niet_samengesteld():
    """Additief: som van losse percentages op het BASISbedrag, één keer opgeteld.
    Samengesteld (fout) zou elke toeslag na elkaar op het lopende bedrag toepassen
    -- bij 3+ actieve toeslagen geven die twee modellen aantoonbaar verschillende
    uitkomsten."""
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(
        _basis_onderdeel(toeslag_hoogte=True, toeslag_spoed=True, toeslag_buiten=True),
        marge_pct=0, btw_pct=0)
    additief_verwacht = round(400 * 0.10 + 400 * 0.20 + 400 * 0.10, 2)   # 160.0
    samengesteld_fout = round(400 * 1.10 * 1.20 * 1.10 - 400, 2)          # 208.0
    assert r["toeslagen"] == additief_verwacht
    assert r["toeslagen"] != samengesteld_fout


def test_steiger_en_reis_toeslag_niet_van_toepassing():
    """Regressietest voor een kritieke audit-fix: Steiger-/Reiskostentoeslag zijn niet
    van toepassing op dit bedrijf en uit Instellingen -> Toeslagen gehaald. Vóór de
    fix bleef bereken_onderdeel deze twee vlaggen nog toepassen op elk onderdeel waar
    ze (van vóór de UI-verwijdering) nog op True stonden -- onzichtbaar voor de
    gebruiker. Een onderdeel met die legacy-vlaggen moet nu identiek rekenen aan
    hetzelfde onderdeel zonder."""
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    met_legacy_vlaggen = bereken_onderdeel(
        _basis_onderdeel(toeslag_steiger=True, toeslag_reis=True), marge_pct=0, btw_pct=0)
    zonder = bereken_onderdeel(_basis_onderdeel(), marge_pct=0, btw_pct=0)
    assert met_legacy_vlaggen["toeslagen"] == 0.0
    assert met_legacy_vlaggen == zonder


# ── Marge & BTW ───────────────────────────────────────────────────────────
def test_marge_en_btw_volgorde():
    """BTW hoort berekend te worden over (subtotaal + toeslagen + marge) -- de volle
    verkoopprijs, zoals bij een Nederlandse factuur hoort -- niet over een kaler
    grondbedrag."""
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(toeslag_hoogte=True), marge_pct=25, btw_pct=21)
    subtotaal_met_toeslag = 400.0 + 40.0                # 440
    marge_bedrag = subtotaal_met_toeslag * 0.25         # 110
    excl_btw = subtotaal_met_toeslag + marge_bedrag     # 550
    btw_bedrag = excl_btw * 0.21                        # 115.5
    assert r["marge_bedrag"] == round(marge_bedrag, 2)
    assert r["excl_btw"] == round(excl_btw, 2)
    assert r["btw_bedrag"] == round(btw_bedrag, 2)


def test_incl_btw_is_som_van_afgeronde_delen():
    """SP-AUDIT: incl_btw = round(excl_btw,2) + round(btw_bedrag,2), niet los
    afgerond vanuit onafgeronde tussenwaarden -- anders kan de offerte/factuur 1
    cent afwijken tussen de getoonde regels en het totaal."""
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(), marge_pct=13, btw_pct=21)
    assert r["incl_btw"] == round(r["excl_btw"] + r["btw_bedrag"], 2)


def test_marge_is_opslag_op_kostprijs():
    """Vastleggen wat de engine feitelijk doet (opslagmodel, geen brutomarge-op-
    verkoopprijs): bij marge 25% op EUR 400 kostprijs is excl_btw 500, dus de
    winst is 100/500 = 20% van de verkoopprijs, niet 25%. Regressietest zodat een
    toekomstige wijziging van dit rekenmodel bewust gebeurt, niet per ongeluk."""
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    r = bereken_onderdeel(_basis_onderdeel(), marge_pct=25, btw_pct=0)
    assert r["excl_btw"] == 500.0
    assert r["marge_bedrag"] == 100.0


# ── Edge cases ────────────────────────────────────────────────────────────
def test_geen_werkzaamheden_geeft_geen_materiaal_maar_wel_terugval_arbeid():
    bereken_onderdeel = _maak_bereken_onderdeel(personeel=PERSONEEL)
    ond = _basis_onderdeel()
    ond.pop("arbeid_uren_override")
    r = bereken_onderdeel(ond, marge_pct=0, btw_pct=0)
    assert r["materiaal"] == 0.0
    # geen crash, geen negatieve/None-waarden
    assert r["arbeid"] >= 0.0
    assert r["excl_btw"] >= 0.0
