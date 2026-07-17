"""
SchilderPro — Calculatie & Offerte Tool voor Schilders
Professional Streamlit app voor ZZP'ers en kleine teams in NL/BE
"""

import streamlit as st
from streamlit_option_menu import option_menu
from fpdf import FPDF
from datetime import datetime, date, timedelta
import random
import re
import json
import hashlib
import base64
import streamlit.components.v1 as _components
import tempfile
import html as _html
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSTISCHE SCHAKELAAR — LITE_MODE
# ─────────────────────────────────────────────────────────────────────────────
# Test of de niet-essentiële `components.html`-JS-iframes de online traagheid
# veroorzaken. Staat 'ie op True → die JS-verrijkingen (dashboard-nav-koppeling,
# popover-styling, long-press-verwijderen, offerte+factuur-dubbel-download, kaart-
# styling van de toevoeg-formulieren) worden OVERGESLAGEN. De kern-CRUD blijft werken
# via de gewone knoppen; alleen wat JS-gemak/politoer valt weg.
#
# VOLLEDIG OMKEERBAAR: zet LITE_MODE = False → de app draait weer exact zoals voorheen
# (al je oude knoppen/gedrag terug). Er verandert niets aan data, berekeningen of opslag.
LITE_MODE = False

def _html_component(*args, **kwargs):
    """components.html, maar in LITE_MODE overgeslagen (diagnostische test).
    Gebruikt voor NIET-essentiële JS-verrijking. LITE_MODE=False → identiek aan
    het oude `_components.html(...)`-gedrag."""
    if LITE_MODE:
        return None
    return _components.html(*args, **kwargs)

# Productimport (URL → automatische invulling). Modulair, defensief geladen:
# ontbreekt het module-bestand op een deploy, dan blijft de app gewoon werken
# en toont de import-sectie een nette melding.
try:
    from product_import import product_uit_bron
    _PRODUCT_IMPORT_OK = True
except Exception:
    _PRODUCT_IMPORT_OK = False

# Database-laag (Supabase PostgreSQL — Fase 1 SaaS-fundering). Defensief geladen:
# ontbreekt db.py of is Supabase niet geconfigureerd, dan valt de app automatisch
# terug op lokale JSON-opslag (geen crash, geen witte pagina).
try:
    import db as _db
    _DB_OK = True
except Exception:
    _DB_OK = False

# Deploy-diagnostiek: log éénmalig of Supabase (dus login) actief is of dat de app op
# JSON terugvalt. Zichtbaar in Streamlit Cloud → Manage app → Logs; maakt "login werkt
# niet op de cloud" meteen herleidbaar naar ontbrekende/foutieve secrets.
if _DB_OK:
    try:
        _db.log_startup_diagnostics()
    except Exception:
        pass

# Canonieke standaard-instellingen uit de datalaag (db.py heeft alleen stdlib-imports en
# is dus altijd laadbaar): één bron van waarheid die de app (init_state-merge) deelt met
# de registratie (auth.sign_up), zodat een nieuw bedrijf altijd een volledig profiel krijgt.
# Lege terugval alleen in het pathologische geval dat db.py onverwacht niet laadbaar is.
STANDAARD_INSTELLINGEN = _db.STANDAARD_INSTELLINGEN if _DB_OK else {}

# Authenticatie & tenant-isolatie (Fase 2). Defensief geladen. Alleen actief als
# Supabase is geconfigureerd; in lokale JSON-modus draait de app zonder login.
try:
    import auth as _auth
    _AUTH_OK = True
except Exception:
    _AUTH_OK = False

# =====================================================
# PERSISTENTIE
# =====================================================

DATA_PATH = Path(__file__).parent / "data" / "appdata.json"

PERSISTENT_KEYS = [
    "klanten", "projecten", "personeel", "producten",
    "taken", "instellingen", "volgende_project_id", "volgende_klant_id",
    "agenda_taken",
]

def _use_db():
    """True als de Supabase-laag geladen én geconfigureerd is."""
    try:
        return _DB_OK and _db.is_enabled()
    except Exception:
        return False

def _load_data_json():
    """Terugval: laad persistente data uit het lokale JSON-bestand."""
    if not DATA_PATH.exists():
        return
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        for key in PERSISTENT_KEYS:
            if key in data:
                st.session_state[key] = data[key]
    except Exception:
        pass  # Corrupt bestand → init_state() vult demodata in

def _save_data_json():
    """Terugval: sla persistente session_state atomisch op naar JSON."""
    try:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {key: st.session_state[key] for key in PERSISTENT_KEYS
                if key in st.session_state}
        tmp = DATA_PATH.parent / "appdata_tmp.json"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(DATA_PATH)  # atomisch op NTFS
    except Exception:
        pass  # Schrijffout crasht de app niet

def load_data():
    """Laad persistente data naar session_state.
    Primair: Supabase (bron van waarheid). Terugval: lokale JSON, zodat de app
    ook zonder DB-configuratie of bij een verbindingsfout blijft draaien."""
    if _use_db():
        try:
            # Fase 2: UITSLUITEND de company van de INGELOGDE gebruiker (door de auth-gate
            # gezet). GEEN terugval meer op de default company — dat zou test-/demodata van
            # een ander bedrijf lekken en de gebruikersisolatie doorbreken.
            cid = st.session_state.get("company_id")
            if cid:
                st.session_state["_company_id"] = cid
                data = _db.load_company_data(cid)
                for key in PERSISTENT_KEYS:
                    if key in data:
                        st.session_state[key] = data[key]
                st.session_state.pop("_db_fout", None)
                return
            # DB geconfigureerd maar nog geen company → migratie nog niet gedraaid
            st.session_state["_db_fout"] = (
                "Database is geconfigureerd, maar er is nog geen bedrijf gevonden. "
                "Is de migratie (migrate_json_to_supabase.py) al uitgevoerd? "
                "De app draait nu tijdelijk op de lokale back-up."
            )
        except Exception as e:
            st.session_state["_db_fout"] = (
                f"Kon niet met de database verbinden — de app draait nu op de lokale "
                f"back-up. Details: {e}"
            )
    _load_data_json()

def save_data():
    """Sla persistente data op. Primair Supabase; bij geen DB → lokale JSON.
    Schrijft nooit naar beide tegelijk (geen hybride opslag)."""
    _cid = st.session_state.get("company_id") or st.session_state.get("_company_id")
    if _use_db() and _cid:
        try:
            _db.save_company_data(
                _cid,
                {key: st.session_state.get(key) for key in PERSISTENT_KEYS},
            )
            st.session_state.pop("_db_fout", None)
            return
        except Exception as e:
            # Geen stille JSON-write (zou hybride opslag worden); fout netjes tonen.
            st.session_state["_db_fout"] = (
                f"Opslaan in de database is mislukt. Je laatste wijziging is mogelijk "
                f"niet bewaard. Details: {e}"
            )
            return
    _save_data_json()

# =====================================================
# PRODUCT- & CALCULATIE-HELPERS (gedeeld door migratie en UI)
# =====================================================

# Werkzaamheden met maximaal 2 lagen (punt 4)
LAGEN_GELIMITEERD   = {"Gronden", "Afplakken", "Kitwerk"}
# Categorieën die per strekkende meter rekenen i.p.v. per m² (punt 2)
METER_CATEGORIEEN   = {"Kit", "Afplakken"}
# Werkzaamheden die uitsluitend meterwerk betreffen (terugval-detectie)
METER_WERKZAAMHEDEN = {"Afplakken", "Kitwerk"}

def is_meter_product(product):
    """True als dit product per strekkende meter rekent (kit/afplakwerk) i.p.v. per m².
    Bepaald op verbruik-eenheid → categorie → werkzaamheden, zodat ook bestaande
    producten (bv. Afplaktape in categorie 'Gereedschap') correct meter-gebaseerd zijn."""
    if not isinstance(product, dict):
        return False
    if product.get("verbruik_eenheid") == "meter":
        return True
    if product.get("categorie") in METER_CATEGORIEEN:
        return True
    _wz = product.get("werkzaamheden") or []
    return bool(_wz) and all(w in METER_WERKZAAMHEDEN for w in _wz)

def verbruik_eenheid_van(product):
    """'meter' voor kit/afplakwerk, anders 'm²'."""
    return "meter" if is_meter_product(product) else "m²"

def onderdeel_is_meterwerk(ond):
    """True als álle werkzaamheden van dit onderdeel meterwerk zijn (kit/afplak),
    zodat de offerte dit onderdeel in strekkende meters i.p.v. m² toont (punt 12)."""
    _wz = (ond or {}).get("werkzaamheden") or []
    return bool(_wz) and all(w in METER_WERKZAAMHEDEN for w in _wz)

def max_lagen_voor(werkzaamheden):
    """Max. aantal lagen: 2 als álle gekozen werkzaamheden gelimiteerd zijn
    (Gronden/Afplakken/Kitwerk), anders 5 (bestaande logica behouden)."""
    _wz = [w for w in (werkzaamheden or [])]
    return 2 if (_wz and all(w in LAGEN_GELIMITEERD for w in _wz)) else 5

# ── Realistische productienormen (Nederlandse schildersbranche) — CENTRAAL beheerd ──
# Per werkzaamheid: hoeveel eenheden een schilder gemiddeld per MANUUR haalt, en of het
# werk per verf-/grondlaag herhaald wordt. Gangbare branchegemiddelden; pas hier aan om
# álle calculaties tegelijk te tunen (geen verspreide hardcoded getallen).
#   eenheid  = "m2" (oppervlaktewerk) of "meter" (kit-/afplakwerk, per strekkende meter)
#   per_uur  = productie per manuur in die eenheid
#   per_laag = True → tijd telt per laag (schilder-/grondwerk); False → eenmalige bewerking
# Logica-check (opdracht): muren schilderen (10) sneller dan behang verwijderen (5);
# afplakken (40 m/u) kost weinig tijd; houtwerk (5) duurt langer dan muren (10); kitwerk
# (meter) heeft een andere productie dan schilderwerk.
PRODUCTIE_NORMEN = {
    "Muren schilderen":    {"eenheid": "m2",    "per_uur": 10.0, "per_laag": True},
    "Plafond schilderen":  {"eenheid": "m2",    "per_uur": 8.0,  "per_laag": True},
    "Houtwerk schilderen": {"eenheid": "m2",    "per_uur": 5.0,  "per_laag": True},
    "Gronden":             {"eenheid": "m2",    "per_uur": 12.0, "per_laag": True,  "per_uur_meter": 15.0},
    "Schuren":             {"eenheid": "m2",    "per_uur": 10.0, "per_laag": False, "per_uur_meter": 15.0},
    "Behang verwijderen":  {"eenheid": "m2",    "per_uur": 5.0,  "per_laag": False},
    "Behangen":            {"eenheid": "m2",    "per_uur": 6.0,  "per_laag": False},
    "Afplakken":           {"eenheid": "meter", "per_uur": 40.0, "per_laag": False},
    "Kitwerk":             {"eenheid": "meter", "per_uur": 20.0, "per_laag": False},
}
# Terugval als een werkzaamheid geen norm heeft (nooit 0 uren): oud vast tempo.
_FALLBACK_M2_PER_UUR = 8.0
# Productiviteit meterwerk-terugval (analoog aan het oude tempo, BUG-04).
METER_PER_UUR = 15.0

def _f(x):
    """Veilige float-parse (None/'' → 0.0)."""
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0

def auto_arbeidsuren(werkzaamheden, m2, lagen, meters=0, houtwerk_m2=0):
    """Realistische arbeidsuren o.b.v. de centrale PRODUCTIE_NORMEN — dé gedeelde bron voor
    de calculatie-engine én de invoerformulieren, zodat beide identiek rekenen. Elke gekozen
    werkzaamheid draagt bij op zijn eigen productietempo: oppervlaktewerk op m² (schilder-/
    grondwerk per laag), meterwerk (kit/afplak) op strekkende meters. Houtwerk gebruikt het
    effectieve schilderoppervlak (`houtwerk_m2`, uit HOUTWERK_NORMEN) i.p.v. de generieke m².
    Zónder werkzaamheden-lijst → terugval op het oude vaste tempo (backwards-compatible)."""
    _m2 = _f(m2); _lg = _f(lagen) or 1.0; _mt = _f(meters); _hw = _f(houtwerk_m2)
    _wz = werkzaamheden or []
    if not _wz:
        uren = (_m2 / _FALLBACK_M2_PER_UUR) * _lg
        if METER_PER_UUR > 0:
            uren += _mt / METER_PER_UUR
        return uren
    uren = 0.0
    for w in _wz:
        norm = PRODUCTIE_NORMEN.get(w)
        if w == "Houtwerk schilderen":
            hoeveelheid, per_uur, per_laag = _hw, (norm or {}).get("per_uur", _FALLBACK_M2_PER_UUR), True
        elif norm and norm.get("eenheid") == "meter":
            hoeveelheid, per_uur, per_laag = _mt, norm["per_uur"], norm.get("per_laag", False)
        elif norm:
            hoeveelheid, per_uur, per_laag = _m2, norm["per_uur"], norm.get("per_laag", False)
            # Dual-unit (Schuren/Gronden): naast m² telt óók de strekkende meter (m1) mee,
            # op zijn eigen tempo. Laat m1 op 0 → geen effect (opt-in per onderdeel).
            _pm = norm.get("per_uur_meter")
            if _pm and _pm > 0 and _mt > 0:
                uren += (_mt / _pm) * (_lg if per_laag else 1.0)
        else:
            hoeveelheid, per_uur, per_laag = _m2, _FALLBACK_M2_PER_UUR, True
        if per_uur > 0:
            uren += (hoeveelheid / per_uur) * (_lg if per_laag else 1.0)
    return uren

def _markeer_uren_touched(flag_key):
    """on_change-callback voor het Arbeidsuren-veld: markeert dat de gebruiker de
    uren handmatig heeft aangepast, zodat het veld niet langer automatisch met
    m²/lagen meeberekent (punt 2 — override)."""
    st.session_state[flag_key] = True

def bereken_verpakkingen(benodigd, inhoud):
    """Aantal verpakkingen = benodigd materiaal ÷ inhoud (naar boven afgerond).
    VOORBEREID voor toekomstige, nauwkeurigere calculaties — nog NIET verwerkt in de
    live prijsberekening, omdat dat bestaande offertes/facturen zou wijzigen (punt 1)."""
    import math
    try:
        inhoud = float(inhoud)
        if inhoud <= 0:
            return 0
        return max(0, math.ceil(float(benodigd) / inhoud))
    except Exception:
        return 0

def _default_inhoud(product):
    """Migratie-default voor (inhoud, inhoud_eenheid) op basis van categorie/eenheid."""
    _eh = product.get("eenheid", "")
    if is_meter_product(product):
        # inhoud uitgedrukt in meters per verpakking
        if product.get("categorie") == "Kit" or _eh == "tube":
            return (12.0, "meter")
        return (50.0, "meter")
    _map = {"liter": (10.0, "liter"), "vel": (50.0, "vel"), "kg": (5.0, "kg"),
            "stuk": (1.0, "stuk"), "m²": (1.0, "m²"), "rol": (50.0, "meter"),
            "tube": (12.0, "meter")}
    return _map.get(_eh, (1.0, _eh or "stuk"))

def migreer_product(product):
    """Vul ontbrekende nieuwe velden aan op een bestaand product (data-compat, punt 6).
    Muteert in-place en is idempotent."""
    if not isinstance(product, dict):
        return product
    # BUG-02: kernvelden borgen zodat een onvolledig/geïmporteerd/ouder product de
    # calculatie-engine (die product["werkzaamheden"]/["verbruik"]/["prijs"] leest)
    # niet laat crashen. Neutrale defaults → geen invloed op bestaande calculaties.
    product.setdefault("naam", "Product")
    product.setdefault("prijs", 0.0)
    product.setdefault("verbruik", 0.0)
    product.setdefault("eenheid", "stuk")
    product.setdefault("categorie", "Overig")
    product.setdefault("actief", True)   # beheerstatus Actief/Inactief (geen invloed op calculatie)
    if not isinstance(product.get("werkzaamheden"), list):
        product["werkzaamheden"] = []
    if "verbruik_eenheid" not in product:
        product["verbruik_eenheid"] = verbruik_eenheid_van(product)
    if "inhoud" not in product or "inhoud_eenheid" not in product:
        _inh, _inh_eh = _default_inhoud(product)
        product.setdefault("inhoud", _inh)
        product.setdefault("inhoud_eenheid", _inh_eh)
    return product

# ── BUG-02: idempotente backfill van ontbrekende velden op alle recordtypen, zodat
#    oudere/geïmporteerde/handmatig bewerkte/onvolledige data niet crasht bij render
#    of berekening. Alle defaults zijn neutraal → geen invloed op bestaande logica. ──

def migreer_onderdeel(ond):
    """Borg de velden die de engine/weergave hard uitlezen (m2, naam, …)."""
    if not isinstance(ond, dict):
        return ond
    ond.setdefault("naam", "Onderdeel")
    ond.setdefault("m2", 0)
    ond.setdefault("lagen", 1)
    ond.setdefault("meters", 0)
    if not isinstance(ond.get("werkzaamheden"), list):
        ond["werkzaamheden"] = []
    for _tk in ("toeslag_hoogte", "toeslag_spoed", "toeslag_buiten", "toeslag_steiger",
                "toeslag_weekend", "toeslag_avond", "toeslag_winter", "toeslag_reis"):
        ond.setdefault(_tk, False)
    ond.setdefault("arbeid_uren_override", None)
    return ond

def migreer_project(project):
    """Borg de hard-uitgelezen projectvelden + normaliseer de onderdelen.
    marge/btw worden bewust NIET geforceerd (overal al via .get benaderd)."""
    if not isinstance(project, dict):
        return project
    project.setdefault("naam", "Naamloos project")
    project.setdefault("adres", "")
    project.setdefault("status", "Concept")
    project.setdefault("aangemaakt", "")
    project.setdefault("notities", "")
    project.setdefault("klant_id", None)
    if not isinstance(project.get("medewerkers"), list):
        project["medewerkers"] = []
    if not isinstance(project.get("onderdelen"), list):
        project["onderdelen"] = []
    for _o in project["onderdelen"]:
        migreer_onderdeel(_o)
    return project

def migreer_klant(klant):
    """Borg de hard-uitgelezen klantvelden (naam wordt overal als sleutel/label gebruikt)."""
    if not isinstance(klant, dict):
        return klant
    klant.setdefault("naam", "Onbekende klant")
    for _f in ("bedrijf", "adres", "postcode", "stad", "telefoon", "email",
               "btw_nummer", "notities"):
        klant.setdefault(_f, "")
    klant.setdefault("actief", True)
    return klant

def migreer_personeel(mw):
    """Borg de hard-uitgelezen personeelsvelden (uurtarief wordt in de engine gesommeerd)."""
    if not isinstance(mw, dict):
        return mw
    mw.setdefault("naam", "Medewerker")
    mw.setdefault("uurtarief", 0.0)
    mw.setdefault("functie", "")
    mw.setdefault("telefoon", "")
    mw.setdefault("actief", True)
    mw.setdefault("status", "Actief" if mw.get("actief", True) else "Inactief")
    if not isinstance(mw.get("project_ids"), list):
        mw["project_ids"] = []
    mw.setdefault("algemeen", False)   # ZZP/algemeen: automatisch aan álle projecten
    return mw

# Basis-instellingen die buiten de Instellingen-pagina hard worden uitgelezen
# (engine, calculatie, project, PDF). Spiegelt de fabrieksdefaults uit init_state().
_INST_BASIS_DEFAULTS = {
    "bedrijfsnaam": "SchilderPro BV", "btw_nummer": "NL999888777B01",
    "adres": "Verfstraat 1, 5000 AA Tilburg", "telefoon": "013-1234567",
    "email": "info@schilderpro.nl", "iban": "NL12 ABCD 0123 4567 89",
    "standaard_marge": 25, "standaard_btw": 21,
    "toeslag_hoogte_pct": 10, "toeslag_spoed_pct": 20,
    "toeslag_buiten_pct": 10, "toeslag_steiger_pct": 15,
    "offerte_geldigheid": 30, "betalingstermijn": 14,
    "offerte_tekst": "Bedankt voor uw interesse. Wij bieden u hierbij onze offerte aan.",
    "voorwaarden": "Betaling binnen 14 dagen na factuurdatum. Op al onze werkzaamheden zijn onze algemene voorwaarden van toepassing.",
    # Factuur-specifiek (gebruikt door maak_factuur_pdf); zo verschijnt de voettekst
    # standaard, ook zonder eerst de Instellingen-pagina te openen.
    "factuur_prefix": "FACT", "factuurtermijn": 30,
    "factuur_tekst": "Bedankt voor uw opdracht. Wij verzoeken u vriendelijk het openstaande bedrag te voldoen.",
    "factuur_voettekst": "Op al onze werkzaamheden zijn onze algemene voorwaarden van toepassing. Bij vragen over deze factuur kunt u contact met ons opnemen.",
}

def migreer_instellingen(inst):
    """Borg de basis-instellingen die elders hard worden uitgelezen, zodat een
    partiële/oude instellingen-import geen KeyError veroorzaakt."""
    if not isinstance(inst, dict):
        return inst
    for _k, _v in _INST_BASIS_DEFAULTS.items():
        inst.setdefault(_k, _v)
    return inst

def _backfill_ids(records):
    """BUG-02: ken records zonder geldig int-id alsnog een uniek id toe, zodat
    id-afhankelijke weergave/PDF niet crasht. Idempotent."""
    if not isinstance(records, list):
        return
    _used = {r["id"] for r in records if isinstance(r, dict) and isinstance(r.get("id"), int)}
    _nxt = (max(_used) + 1) if _used else 1
    for r in records:
        if isinstance(r, dict) and not isinstance(r.get("id"), int):
            while _nxt in _used:
                _nxt += 1
            r["id"] = _nxt
            _used.add(_nxt)
            _nxt += 1

# ── BUG-07: permanente, opgeslagen offertenummers. Eenmaal toegekend wordt een
#    nummer nooit meer opnieuw berekend; het staat als project["offerte_nummer"]. ──

def _offerte_nr_seq(s):
    """Haal het volgnummer (achterste cijferreeks) uit een offertenummer-string."""
    if not s:
        return None
    _digits = ""
    for _ch in reversed(str(s)):
        if _ch.isdigit():
            _digits = _ch + _digits
        elif _digits:
            break
    return int(_digits) if _digits else None

def _init_offertenummers():
    """Ken elk project éénmalig een permanent, uniek offertenummer toe en bewaar het.
    Bestaande projecten behouden hun huidige (formule-)nummer (start-1+id); alleen
    bij een botsing of een onherleidbaar id wordt het eerstvolgende vrije nummer
    gebruikt. Idempotent en botsingsvrij — projecten mét nummer blijven ongemoeid."""
    inst  = st.session_state.instellingen
    pfx   = inst.get("offerte_prefix", "OFF") or "OFF"
    try:
        start = int(inst.get("offerte_startnummer", 1) or 1)
    except (TypeError, ValueError):
        start = 1
    projecten = st.session_state.projecten
    _used = set()
    for _p in projecten:
        _s = _offerte_nr_seq(_p.get("offerte_nummer")) if isinstance(_p, dict) else None
        if _s is not None:
            _used.add(_s)
    for _p in projecten:
        if not isinstance(_p, dict) or _p.get("offerte_nummer"):
            continue
        try:
            _n = start - 1 + int(_p.get("id"))
        except (TypeError, ValueError):
            _n = None
        if _n is None or _n < start or _n in _used:
            _n = (max(_used) + 1) if _used else start
            while _n in _used:
                _n += 1
        _used.add(_n)
        _p["offerte_nummer"] = f"{pfx}-{_n:04d}"

def verzeker_factuur_nummer(project):
    """Ken een project éénmalig een permanent, uniek factuurnummer toe — lazy: pas
    wanneer de gebruiker daadwerkelijk een factuur genereert (een factuur ontstaat
    niet automatisch voor elk project). Het nummer + de factuurdatum worden op het
    project opgeslagen en wijzigen daarna NOOIT meer (niet opnieuw berekend, niet
    afhankelijk van instellingen/sortering). Botsingsvrij t.o.v. bestaande
    factuurnummers. De aanroeper is verantwoordelijk voor save_data()."""
    if not isinstance(project, dict):
        return None
    if project.get("factuur_nummer"):
        return project["factuur_nummer"]
    inst = st.session_state.instellingen
    pfx  = inst.get("factuur_prefix", "FACT") or "FACT"
    try:
        _seq = int(inst.get("volgende_factuur_nummer", inst.get("factuur_startnummer", 1) or 1) or 1)
    except (TypeError, ValueError):
        _seq = 1
    if _seq < 1:
        _seq = 1
    _used = set()
    for _p in st.session_state.projecten:
        if isinstance(_p, dict):
            _s = _offerte_nr_seq(_p.get("factuur_nummer"))
            if _s is not None:
                _used.add(_s)
    while _seq in _used:
        _seq += 1
    project["factuur_nummer"] = f"{pfx}-{_seq:04d}"
    project.setdefault("factuur_datum", datetime.now().strftime("%Y-%m-%dT%H:%M"))
    inst["volgende_factuur_nummer"] = _seq + 1
    return project["factuur_nummer"]

def _wis_pdf_downloadknoppen(pid=None):
    """Klap de Offerte/Factuur-downloadknoppen in Projecten → Acties weer in
    (verberg ze). Aangeroepen bij paginawissel, 'Terug naar overzicht' en het
    openen van een project; de knoppen komen pas terug na 'PDF genereren'."""
    if pid is None:
        _keys = [k for k in list(st.session_state.keys())
                 if str(k).startswith("_off_bytes_") or str(k).startswith("_fact_bytes_")]
    else:
        _keys = [f"_off_bytes_{pid}", f"_fact_bytes_{pid}"]
    for _k in _keys:
        st.session_state.pop(_k, None)

# =====================================================
# PAGE CONFIG
# =====================================================

# Browsertab-favicon = de blauwe "C" van het CoatFlow-logo (zelfde SVG als login/sidebar),
# als SVG-data-URI. Streamlit geeft een niet-verwerkbare string ongewijzigd door aan de
# favicon-href; moderne browsers renderen SVG-favicons.
_CF_FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48' fill='none'>"
    "<path d='M35 14.5 A15.5 15.5 0 1 0 35 33.5' stroke='#2563EB' stroke-width='8.5' "
    "stroke-linecap='round'/></svg>"
)
_CF_FAVICON = "data:image/svg+xml;base64," + base64.b64encode(_CF_FAVICON_SVG.encode()).decode()
st.set_page_config(
    page_title="CoatFlow",
    page_icon=_CF_FAVICON,
    layout="wide",
    initial_sidebar_state="expanded"
)

# =====================================================
# SESSION STATE INITIALISATIE
# =====================================================

# STANDAARD_INSTELLINGEN wordt bovenaan uit de datalaag (db.py) geïmporteerd — één bron
# van waarheid, gedeeld met de registratie (auth.sign_up). De init_state()-merge eronder
# zet die defaults ÓNDER de geladen waarden, zodat elk bedrijf altijd alle sleutels heeft
# (een nieuw bedrijf start in de DB met instellingen={}) en opgeslagen waarden leidend blijven.

def init_state():
    """Initialiseer alle session state variabelen"""
    # BUG-10: data één keer per sessie van schijf laden. init_state() draait bij
    # elke rerun, maar session_state blíjft tussen reruns bestaan en is na het laden
    # de bron van waarheid (elke mutatie schrijft via save_data() weg). Opnieuw
    # inlezen bij elke rerun was overbodige disk-I/O. De idempotente defaults/migraties
    # hieronder blijven wel elke run draaien (geen schijf, puur in-memory borging).
    if not st.session_state.get("_data_geladen"):
        load_data()
        st.session_state["_data_geladen"] = True

    # 🔴-AUDIT-fix: borg een COMPLEET instellingen-dict. load_data() kan voor een nieuw
    # bedrijf instellingen={} (uit auth.sign_up) of een gedeeltelijk dict opleveren;
    # zonder de defaults eronder zou directe toegang als inst["standaard_marge"] crashen.
    # Door elke rerun te mergen blijft het dict compleet, óók na een Reset/import; door
    # de geladen waarden bóven de defaults te plaatsen blijven opgeslagen waarden leidend.
    st.session_state["instellingen"] = {
        **STANDAARD_INSTELLINGEN,
        **(st.session_state.get("instellingen") or {}),
    }

    # Demo-/voorbeelddata wordt ALLEEN in lokale JSON-modus (single-user dev) geseed.
    # In Supabase-modus (SaaS, ingelogd) nooit → een nieuw/leeg bedrijf krijgt lege
    # lijsten, geen test-/demodata. Zie ook de setdefault-vangnet vlak vóór _backfill_ids.
    if "producten" not in st.session_state and not _use_db():
        st.session_state.producten = [
            {"id": 1, "naam": "Sigma Muurverf Wit", "prijs": 28.50, "verbruik": 0.12, "eenheid": "liter", "categorie": "Verf", "werkzaamheden": ["Muren schilderen"]},
            {"id": 2, "naam": "Sigma Houtverf Buiten", "prijs": 34.00, "verbruik": 0.10, "eenheid": "liter", "categorie": "Verf", "werkzaamheden": ["Houtwerk schilderen"]},
            {"id": 3, "naam": "Primer Universeel", "prijs": 22.00, "verbruik": 0.08, "eenheid": "liter", "categorie": "Primer", "werkzaamheden": ["Gronden", "Muren schilderen"]},
            {"id": 4, "naam": "Afplaktape 50mm", "prijs": 4.50, "verbruik": 0.20, "eenheid": "rol", "categorie": "Gereedschap", "werkzaamheden": ["Afplakken"]},
            {"id": 5, "naam": "Siliconenkit", "prijs": 8.00, "verbruik": 0.05, "eenheid": "tube", "categorie": "Kit", "werkzaamheden": ["Kitwerk"]},
            {"id": 6, "naam": "Schuurpapier (vel)", "prijs": 1.20, "verbruik": 0.50, "eenheid": "vel", "categorie": "Gereedschap", "werkzaamheden": ["Schuren", "Houtwerk schilderen"]},
        ]

    if "personeel" not in st.session_state and not _use_db():
        st.session_state.personeel = [
            {"id": 1, "naam": "Jan de Vries", "uurtarief": 52.00, "functie": "Uitvoerder", "telefoon": "06-12345678", "actief": True, "project_ids": []},
            {"id": 2, "naam": "Piet Bakker", "uurtarief": 42.00, "functie": "Schilder", "telefoon": "06-87654321", "actief": True, "project_ids": []},
        ]

    if "klanten" not in st.session_state and not _use_db():
        st.session_state.klanten = [
            {"id": 1, "naam": "Familie Jansen", "bedrijf": "", "adres": "Kerkstraat 12", "postcode": "5211 AB", "stad": "Den Bosch", "telefoon": "073-1234567", "email": "jansen@email.nl", "btw_nummer": "", "notities": "Vaste klant"},
            {"id": 2, "naam": "VvE Parkflat", "bedrijf": "VvE Parkflat BV", "adres": "Parkweg 44", "postcode": "5615 GH", "stad": "Eindhoven", "telefoon": "040-9876543", "email": "info@parkflat.nl", "btw_nummer": "NL001234567B01", "notities": "Jaarlijks terugkerend"},
        ]

    if "projecten" not in st.session_state and not _use_db():
        st.session_state.projecten = [
            {
                "id": 1, "naam": "Woonkamer renovatie Jansen", "klant_id": 1,
                "adres": "Kerkstraat 12, Den Bosch",
                "status": "Offerte verzonden",
                "aangemaakt": "2025-01-10",
                "onderdelen": [
                    {"naam": "Woonkamer muren", "m2": 60, "lagen": 2, "werkzaamheden": ["Muren schilderen", "Gronden"], "toeslag_hoogte": False, "toeslag_spoed": False, "toeslag_buiten": False},
                    {"naam": "Plafond woonkamer", "m2": 30, "lagen": 1, "werkzaamheden": ["Muren schilderen"], "toeslag_hoogte": True, "toeslag_spoed": False, "toeslag_buiten": False},
                ],
                "medewerkers": [1, 2],
                "notities": "Klant wil witte muren",
                "btw": 21,
                "marge": 20,
            },
            {
                "id": 2, "naam": "Buitenschilderwerk Parkflat", "klant_id": 2,
                "adres": "Parkweg 44, Eindhoven",
                "status": "In uitvoering",
                "aangemaakt": "2025-01-05",
                "onderdelen": [
                    {"naam": "Gevels blok A", "m2": 200, "lagen": 2, "werkzaamheden": ["Houtwerk schilderen", "Schuren"], "toeslag_hoogte": True, "toeslag_spoed": False, "toeslag_buiten": True},
                ],
                "medewerkers": [1],
                "notities": "Steiger aanwezig",
                "btw": 21,
                "marge": 25,
            },
        ]

    if "taken" not in st.session_state and not _use_db():
        st.session_state.taken = [
            {"id": 1, "taak": "Offerte sturen naar Jansen", "klaar": False, "datum": "2025-01-15"},
            {"id": 2, "taak": "Verf bestellen Parkflat", "klaar": True, "datum": "2025-01-12"},
            {"id": 3, "taak": "Steiger regelen blok B", "klaar": False, "datum": "2025-01-20"},
        ]

    if "instellingen" not in st.session_state:
        # Terugval (wordt in de praktijk al door de merge hierboven afgedekt).
        st.session_state.instellingen = dict(STANDAARD_INSTELLINGEN)

    if "volgende_project_id" not in st.session_state:
        st.session_state.volgende_project_id = 3

    if "volgende_klant_id" not in st.session_state:
        st.session_state.volgende_klant_id = 3

    # Vangnet (álle modi): zorg dat de kernlijsten bestaan. In Supabase-modus met een
    # leeg/nieuw bedrijf zijn ze hierboven bewust NIET met demodata gevuld → hier leeg,
    # zodat de UI nooit op een ontbrekende key crasht (en er géén testdata verschijnt).
    for _k in ("producten", "personeel", "klanten", "projecten", "taken"):
        st.session_state.setdefault(_k, [])

    # ── BUG-02: records zonder geldig id alsnog een uniek id geven (vóór de
    #    teller-integriteit), zodat id-afhankelijke weergave/PDF niet crasht. ──
    _backfill_ids(st.session_state.projecten)
    _backfill_ids(st.session_state.klanten)
    _backfill_ids(st.session_state.personeel)
    _backfill_ids(st.session_state.producten)

    # ── ID-teller integriteit (SP-002): teller moet altijd boven het hoogste
    #    bestaande id liggen, anders ontstaan dubbele ids na back-up import,
    #    fabrieksreset of een handmatig bewerkt databestand. ──
    _max_pid = max((p.get("id", 0) for p in st.session_state.projecten
                    if isinstance(p.get("id"), int)), default=0)
    if st.session_state.volgende_project_id <= _max_pid:
        st.session_state.volgende_project_id = _max_pid + 1
    _max_kid = max((k.get("id", 0) for k in st.session_state.klanten
                    if isinstance(k.get("id"), int)), default=0)
    if st.session_state.volgende_klant_id <= _max_kid:
        st.session_state.volgende_klant_id = _max_kid + 1

    # ── Datamigratie producten: nieuw veld "Inhoud" + verbruik-eenheid (per m²/meter).
    #    Idempotent; bestaande producten blijven werken (punt 6). ──
    for _prod in st.session_state.producten:
        migreer_product(_prod)

    # ── BUG-02: instellingen + alle records compleet maken (ontbrekende velden →
    #    veilige defaults) zodat oudere/geïmporteerde/onvolledige data niet crasht
    #    bij render of berekening. Idempotent (setdefault); geen calculatiewijziging. ──
    migreer_instellingen(st.session_state.instellingen)
    for _kl in st.session_state.klanten:
        migreer_klant(_kl)
    for _mw in st.session_state.personeel:
        migreer_personeel(_mw)
    for _pj in st.session_state.projecten:
        migreer_project(_pj)

    # ── BUG-07: ken bestaande projecten éénmalig een permanent offertenummer toe. ──
    _init_offertenummers()

    if "klanten_pagina" not in st.session_state:
        st.session_state.klanten_pagina = 1

    if "kl_edit_id" not in st.session_state:
        st.session_state.kl_edit_id = None

    if "kl_del_id" not in st.session_state:
        st.session_state.kl_del_id = None

    if "kl_view_id" not in st.session_state:
        st.session_state.kl_view_id = None

    if "klant_zoek" not in st.session_state:
        st.session_state.klant_zoek = ""

    if "geselecteerd_project" not in st.session_state:
        st.session_state.geselecteerd_project = None
    if "pj_edit_in_form" not in st.session_state:
        st.session_state.pj_edit_in_form = None   # id van project dat in de '+ Nieuw project'-form wordt bewerkt

    if not DATA_PATH.exists():
        save_data()

# ── Route-guard (Fase 2) ──────────────────────────────────────────────────────
# Vóór init_state(): als Supabase actief is moet de gebruiker eerst inloggen.
# require_auth() toont de toegangspoort + st.stop() zolang niet ingelogd, zodat er
# géén core-UI of data lekt. In lokale JSON-modus is er geen login (single-user dev).
if _AUTH_OK and _auth.is_active():
    _auth.require_auth()

init_state()

# Sessie bewaren (cookie) zodat een page-refresh ingelogd blijft (Fase 2).
if _AUTH_OK and _auth.is_active():
    _auth.persist_session()

# ── Snelle Calculatie: widgetwaarden vasthouden over paginawissels heen ──
# Streamlit ruimt de state van een widget op zodra die in een script-run niet
# gerenderd wordt (bv. wanneer je op een andere pagina zit). Daardoor kwamen na
# een Reset de standaardwaarden (50 m² / 2 lagen) terug bij terugkeer naar de
# Calculaties-pagina. Door deze keys élke run opnieuw aan zichzelf toe te kennen
# blijven ze in session_state staan, zodat de (geresette) waarden behouden
# blijven tot de gebruiker zelf iets wijzigt. Puur state-behoud — geen invloed
# op de berekeningen.
# BUG-09: de Snelle Calculatie start met dezelfde standaard-BTW als een nieuw project
# (Instellingen → standaard_btw), zodat identieke invoer ook identiek BTW-resultaat geeft.
# De Winstmarge volgde de standaardmarge al; hiermee zijn ook de BTW-defaults gelijk.
st.session_state.setdefault("calc_btw", st.session_state.instellingen.get("standaard_btw", 21))
# Beginwaarden van de Calculatie-widgets via setdefault i.p.v. een value=-param op de
# widgets zelf. De persistence-lus hieronder + de Reset zetten deze keys ook via de
# Session State API; een gelijktijdige value=-param gaf de Streamlit-waarschuwing
# "created with a default value but also had its value set via the Session State API".
# Zonder value= op de widgets (ze lezen puur uit session_state) verdwijnt die melding.
HOUTWERK_LAGEN = 2   # houtwerk standaard 2 lagen (grond + aflak); vroeg gedefinieerd voor calc-init
st.session_state.setdefault("calc_m2", 0)
st.session_state.setdefault("calc_lagen", 1)
st.session_state.setdefault("calc_meters", 0)
st.session_state.setdefault("calc_marge", st.session_state.instellingen.get("standaard_marge", 25))
st.session_state.setdefault("calc_houttype", "Kozijnen")       # type houtwerk (bij Houtwerk schilderen)
st.session_state.setdefault("calc_houttype_waarde", 0.0)       # typespecifieke hoeveelheid (m/aantal/m²)
st.session_state.setdefault("calc_houtwerk_lagen", HOUTWERK_LAGEN)   # aantal lagen bij houtwerk

for _ck in ("calc_m2", "calc_lagen", "calc_meters", "calc_wz", "calc_houttype", "calc_houttype_waarde",
            "calc_houtwerk_lagen", "calc_marge", "calc_btw", "ct_hoogte", "ct_spoed", "ct_buiten",
            "ct_weekend", "ct_avond", "ct_winter", "calc_uren", "calc_uren_touched"):
    if _ck in st.session_state:
        st.session_state[_ck] = st.session_state[_ck]

# =====================================================
# HELPER FUNCTIES
# =====================================================

WERKZAAMHEDEN_OPTIES = [
    "Muren schilderen", "Plafond schilderen", "Houtwerk schilderen",
    "Gronden", "Afplakken", "Kitwerk", "Schuren", "Behang verwijderen", "Behangen"
]

# ── Houtwerk-calculatie (Nederlandse schildersnormen) — CENTRAAL beheerd ──
# Bij "Houtwerk schilderen" vervangt het gekozen type + zijn eigen maatveld de generieke
# m²/lagen. De engine rekent de ingevoerde hoeveelheid om naar effectief schilderoppervlak
# (m²) via `m2_per_eenheid`, en berekent daar verf/primer/arbeid/kosten op.
#   input          = welk invoerveld tonen ("meter" / "aantal" / "m2")
#   label          = veldlabel
#   m2_per_eenheid = schilderoppervlak (m²) per ingevoerde eenheid (branchegemiddelde)
HOUTWERK_NORMEN = {
    "Kozijnen":         {"input": "meter",  "label": "Strekkende meter (m)", "m2_per_eenheid": 0.5},
    "Deuren":           {"input": "aantal", "label": "Aantal zijdes",        "m2_per_eenheid": 1.8},
    "Trappen":          {"input": "aantal", "label": "Aantal treden",        "m2_per_eenheid": 0.5},
    "Plinten":          {"input": "meter",  "label": "Strekkende meter (m)", "m2_per_eenheid": 0.1},
    "Gevelbetimmering": {"input": "m2",     "label": "Oppervlakte (m²)",     "m2_per_eenheid": 1.0},
}
# Houtwerk wordt standaard in 2 lagen geschilderd (grondlaag + aflaklaag) — HOUTWERK_LAGEN
# staat hierboven al gedefinieerd (vóór de calc-init die het gebruikt).

def houtwerk_effectief_m2(houttype, waarde):
    """Effectief schilderoppervlak (m²) voor houtwerk: ingevoerde hoeveelheid × de norm
    (m² per eenheid) uit HOUTWERK_NORMEN. Onbekend type of lege waarde → 0."""
    norm = HOUTWERK_NORMEN.get(houttype)
    if not norm:
        return 0.0
    return max(0.0, _f(waarde)) * float(norm["m2_per_eenheid"])


def render_houttype(select_key, waarde_key):
    """Type houtwerk + het bijbehorende maatveld — gedeeld door de Calculatiepagina én
    Projecten → Onderdeel toevoegen, zodat beide identiek werken. Elk type heeft zijn eigen
    invoereenheid (strekkende meter / aantal zijdes / aantal treden / m²); de engine rekent
    dit om naar schilderoppervlak. Retourneert (houttype, waarde). Beide keys hebben elders
    een setdefault (géén value= op de widgets → geen Session-State-waarschuwing)."""
    houttype = st.selectbox("Type houtwerk", list(HOUTWERK_NORMEN.keys()), key=select_key)
    norm = HOUTWERK_NORMEN.get(houttype, {})
    waarde = st.number_input(norm.get("label", "Hoeveelheid"), min_value=0.0, step=1.0,
                             key=waarde_key,
                             help=f"± {norm.get('m2_per_eenheid', 0):g} m² schilderoppervlak per eenheid.")
    return houttype, waarde

# Dynamische calculatie-invoer (Calculaties + Onderdeel toevoegen): welke dimensievelden een
# werkzaamheid nodig heeft. Meterwerk → Lengte (m); schilder-/grondwerk → m² + Aantal lagen;
# overig oppervlaktewerk (Schuren/Behang verwijderen/Behangen) → alleen m². HOUTWERK is apart:
# het vervangt m²/lagen door Type houtwerk + een typespecifiek maatveld.
_CALC_LENGTE_WZ = {"Afplakken", "Kitwerk"}
_CALC_LAGEN_WZ  = {"Muren schilderen", "Plafond schilderen", "Gronden"}
# Dual-unit: tonen zowel m² als m1 (strekkende meter). De gebruiker vult in wat past;
# m1 telt mee in de arbeidsuren (materiaal blijft op m²).
_CALC_DUAL_WZ   = {"Schuren", "Gronden"}


def dimensie_flags(werkzaamheden):
    """Welke dynamische afmetingvelden horen bij een selectie werkzaamheden. Dé gedeelde bron
    voor Calculaties én Onderdeel toevoegen. Meterwerk (Kitwerk/Afplakken) → Lengte; schilder-/
    grondwerk → m² + Aantal lagen; overig oppervlaktewerk → alleen m². **Houtwerk schilderen
    vervangt de generieke m²/lagen volledig** door Type houtwerk + een typespecifiek maatveld
    (dan géén generieke m²/lagen). Retourneert
    (show_kit, show_afplak, show_meters, show_m2, show_lagen, show_houtwerk)."""
    _wz = werkzaamheden or []
    show_kit      = "Kitwerk" in _wz
    show_afplak   = "Afplakken" in _wz
    show_meters   = show_kit or show_afplak or any(w in _CALC_DUAL_WZ for w in _wz)
    show_houtwerk = "Houtwerk schilderen" in _wz
    if show_houtwerk:
        show_m2, show_lagen = False, False      # houtwerk vervangt de generieke afmetingen
    else:
        show_m2    = any(w not in _CALC_LENGTE_WZ for w in _wz)   # oppervlaktewerk → m²
        show_lagen = any(w in _CALC_LAGEN_WZ for w in _wz)        # schilder-/grondwerk → lagen
    return show_kit, show_afplak, show_meters, show_m2, show_lagen, show_houtwerk


def lengte_label(show_kit, show_afplak):
    """Contextlabel voor het gedeelde lengteveld (één waarde, engine/state ongewijzigd):
    alleen kit / alleen afplak / beide."""
    if show_kit and not show_afplak:
        return "Lengte kit (m)"
    if show_afplak and not show_kit:
        return "Lengte afplakken (m)"
    if not show_kit and not show_afplak:
        return "Lengte / strekkende meter (m1)"   # Schuren/Gronden dual-unit
    return "Lengte kit/afplak (m)"

STATUS_KLEUREN = {
    "Concept":           ("#475569", "#F1F5F9"),
    "Offerte verzonden": ("#5B21B6", "#EDE9FE"),
    "Geaccepteerd":      ("#166534", "#DCFCE7"),
    "In uitvoering":     ("#92400E", "#FEF3C7"),
    "Afgerond":          ("#166534", "#F0FDF4"),
    "Geannuleerd":       ("#991B1B", "#FEE2E2"),
}

def get_klant_naam(klant_id):
    for k in st.session_state.klanten:
        if k["id"] == klant_id:
            return k["naam"]
    return "Onbekende klant"

def get_klant(klant_id):
    for k in st.session_state.klanten:
        if k["id"] == klant_id:
            return k
    return None

def _inst_getal(d, key, default, cast=float):
    """Robuuste parse van een numerieke instelling uit dict `d`: valt terug op `default`
    bij een ontbrekende, lege (None/"") of ongeldige waarde, maar behoudt een geldige 0
    (zodat bv. 'decimalen = 0' of een 0-toeslag blijft werken). Defensief tegen een
    handmatig/corrupt geïmporteerd settings-bestand; normale invoer blijft ongewijzigd."""
    v = d.get(key, default)
    if v is None or v == "":
        return default
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def bereken_onderdeel(onderdeel, marge_pct, btw_pct, project_id=None):
    """Bereken kosten voor één onderdeel"""
    inst = st.session_state.instellingen
    m2 = onderdeel["m2"]
    lagen = onderdeel.get("lagen", 1)
    werkzaamheden = onderdeel.get("werkzaamheden", [])
    # Strekkende lengte voor kit-/afplakwerk (punt 2). Bestaande onderdelen zonder
    # dit veld → 0, zodat er nergens nog met m² voor kit/tape wordt gerekend.
    meters = float(onderdeel.get("meters", 0) or 0)
    # Houtwerk: het gekozen type + typespecifieke hoeveelheid vervangen de generieke m²/lagen.
    # De hoeveelheid wordt via HOUTWERK_NORMEN omgerekend naar effectief schilderoppervlak en
    # met standaard 2 lagen (grond + aflak) berekend — verf/primer/arbeid schalen hierop mee.
    # Bestaand houtwerk zónder type → ongewijzigd (m²/lagen zoals opgeslagen; geen regressie).
    _houttype = onderdeel.get("houttype")
    _is_houtwerk = "Houtwerk schilderen" in werkzaamheden and _houttype in HOUTWERK_NORMEN
    if _is_houtwerk:
        m2 = houtwerk_effectief_m2(_houttype, onderdeel.get("houttype_waarde", 0))
        # Aantal lagen instelbaar per houtwerk-onderdeel; bestaand houtwerk zónder dit veld
        # → HOUTWERK_LAGEN (2), dus geen prijswijziging op oude calculaties.
        lagen = int(onderdeel.get("houtwerk_lagen") or HOUTWERK_LAGEN)

    # Materiaalkosten
    materiaal = 0.0
    for product in st.session_state.producten:
        # Inactieve producten tellen niet mee in de calculatie (beheerstatus).
        if not product.get("actief", True):
            continue
        # Projectkoppeling: binnen een project tellen alleen GLOBALE producten (zonder
        # project_id) én producten van dít project mee. Zonder project_id (Snelle
        # Calculatie of oudere aanroep) blijft de volledige productenpool gelden — zo
        # blijven bestaande, projectloze producten overal werken (geen regressie).
        if project_id is not None:
            _pp = product.get("project_id")
            if _pp is not None and _pp != project_id:
                continue
        for wz in product["werkzaamheden"]:
            if wz in werkzaamheden:
                # `prijs` is de VERPAKKINGSprijs; ÷ inhoud → prijs per eenheid (liter/meter/vel…),
                # zodat `verbruik (per m²/m) × stukprijs` de juiste €/m² geeft (voorheen werd door
                # het overslaan van inhoud met de hele verpakkingsprijs per eenheid gerekend → veel
                # te hoog). Inhoud ontbreekt/0 → 1 (geen deling; oude producten ongewijzigd).
                _inh = float(product.get("inhoud", 1) or 1)
                _stukprijs = float(product["prijs"]) / (_inh if _inh > 0 else 1)
                if is_meter_product(product):
                    # Kit/afplakwerk: lengte × verbruik per meter × stukprijs (GEEN m², GEEN lagen)
                    materiaal += meters * float(product["verbruik"]) * _stukprijs
                else:
                    # Oppervlaktewerk: m² × lagen × verbruik per m² × stukprijs. Bij dual-unit
                    # (Schuren/Gronden) telt de strekkende meter (m1) óók mee als oppervlak, zodat
                    # materiaal niet 0 is wanneer je alleen m1 invult.
                    _opp = m2 + (meters if wz in _CALC_DUAL_WZ else 0)
                    materiaal += _opp * lagen * float(product["verbruik"]) * _stukprijs
                break

    # Arbeidskosten — automatische uren via de centrale, per-werkzaamheid-realistische helper
    # (PRODUCTIE_NORMEN). Houtwerk gebruikt het effectieve schilderoppervlak. Override (punt 2):
    # staat er een handmatige ureninschatting op het onderdeel, dan is die leidend.
    # Ontbreekt het veld of is het leeg/None → automatische berekening (backwards-compatible).
    _auto_uren = auto_arbeidsuren(werkzaamheden, m2, lagen, meters, houtwerk_m2=m2)
    _uren_ovr = onderdeel.get("arbeid_uren_override", None)
    if _uren_ovr in (None, ""):
        uren = _auto_uren
    else:
        try:
            uren = float(_uren_ovr)
        except (TypeError, ValueError):
            uren = _auto_uren
    arbeid = 0.0
    # Gebruik project-gekoppeld personeel indien beschikbaar, anders alle actieve medewerkers.
    # BUG-03: de koppeling kan in twee richtingen vastliggen — forward via het project zelf
    # (project["medewerkers"]) of reverse via het personeel (personeel["project_ids"]).
    # Beide tellen mee, zodat de op een project gekozen medewerkers daadwerkelijk bepalend
    # zijn voor de arbeidsberekening. Een medewerker wordt nooit dubbel geteld (de lijst
    # personeel wordt één keer doorlopen). `or []` vangt ontbrekende/None project_ids op.
    if project_id is not None:
        _proj = next((p for p in st.session_state.projecten
                      if p.get("id") == project_id), None)
        _proj_mw_ids = set((_proj or {}).get("medewerkers") or [])
        _proj_mw = [mw for mw in st.session_state.personeel
                    if mw.get("actief") and (
                        mw.get("id") in _proj_mw_ids
                        or project_id in (mw.get("project_ids") or [])
                        or mw.get("algemeen"))]   # "algemeen" = telt op elk project mee (ZZP)
        actieve_mw = _proj_mw if _proj_mw else [mw for mw in st.session_state.personeel if mw.get("actief")]
    else:
        actieve_mw = [mw for mw in st.session_state.personeel if mw.get("actief")]
    if actieve_mw:
        gem_tarief = sum(mw["uurtarief"] for mw in actieve_mw) / len(actieve_mw)
        arbeid = uren * gem_tarief
    else:
        # Geen (actief/algemeen) personeel in het systeem → géén arbeidskosten. Bewust
        # geen standaard-uurloon-terugval meer: arbeid telt alleen als er personeel is.
        arbeid = 0.0

    subtotaal = materiaal + arbeid

    # Toeslagen — alle percentages uit Instellingen → Toeslagen (SP-012)
    toeslagen = 0.0
    for _ok, _pk in (
        ("toeslag_hoogte",  "toeslag_hoogte_pct"),
        ("toeslag_spoed",   "toeslag_spoed_pct"),
        ("toeslag_buiten",  "toeslag_buiten_pct"),
        ("toeslag_steiger", "toeslag_steiger_pct"),
        ("toeslag_weekend", "toeslag_weekend_pct"),
        ("toeslag_avond",   "toeslag_avond_pct"),
        ("toeslag_winter",  "toeslag_winter_pct"),
        ("toeslag_reis",    "toeslag_reis_pct"),
    ):
        if onderdeel.get(_ok):
            toeslagen += subtotaal * (float(inst.get(_pk, 0) or 0) / 100)

    subtotaal_met_toeslagen = subtotaal + toeslagen

    # Marge
    marge_bedrag = subtotaal_met_toeslagen * (marge_pct / 100)
    excl_btw = subtotaal_met_toeslagen + marge_bedrag

    # BTW
    btw_bedrag = excl_btw * (btw_pct / 100)
    incl_btw = excl_btw + btw_bedrag

    return {
        "materiaal": round(materiaal, 2),
        "arbeid": round(arbeid, 2),
        "uren": round(uren, 1),
        "toeslagen": round(toeslagen, 2),
        "subtotaal": round(subtotaal_met_toeslagen, 2),
        "marge_bedrag": round(marge_bedrag, 2),
        "excl_btw": round(excl_btw, 2),
        "btw_bedrag": round(btw_bedrag, 2),
        # SP-AUDIT: totaal incl. BTW = afgeronde excl. + afgeronde BTW, zodat de
        # getoonde regels op offerte/factuur (Subtotaal excl. + BTW) exact optellen
        # tot het te-betalen-bedrag. Voorheen werd incl_btw los afgerond vanuit de
        # onafgeronde tussenwaarden, wat een afwijking van 1 cent kon geven. Door
        # per onderdeel consistent te zijn, klopt ook het projecttotaal (Σ excl + Σ btw).
        "incl_btw": round(round(excl_btw, 2) + round(btw_bedrag, 2), 2),
    }

def _bereken_project_totaal_live(project):
    """Bereken totaal voor een project — altijd live uit actuele prijzen/tarieven."""
    marge = project.get("marge", st.session_state.instellingen["standaard_marge"])
    btw = project.get("btw", st.session_state.instellingen["standaard_btw"])

    totaal_materiaal = 0
    totaal_arbeid = 0
    totaal_toeslagen = 0
    totaal_excl = 0
    totaal_btw = 0
    totaal_incl = 0

    for onderdeel in project.get("onderdelen", []):
        c = bereken_onderdeel(onderdeel, marge, btw, project_id=project.get("id"))
        totaal_materiaal += c["materiaal"]
        totaal_arbeid += c["arbeid"]
        totaal_toeslagen += c["toeslagen"]
        totaal_excl += c["excl_btw"]
        totaal_btw += c["btw_bedrag"]
        totaal_incl += c["incl_btw"]

    return {
        "materiaal": round(totaal_materiaal, 2),
        "arbeid": round(totaal_arbeid, 2),
        "toeslagen": round(totaal_toeslagen, 2),
        "excl_btw": round(totaal_excl, 2),
        "btw_bedrag": round(totaal_btw, 2),
        "incl_btw": round(totaal_incl, 2),
    }


def render_kosten_breakdown(result, marge_pct, btw_pct):
    """Gedeelde kosten-breakdown-kaart: Materiaal / Arbeid / Toeslagen / Marge / BTW,
    elk met bedrag én percentage van het totaal (incl. BTW). Eén consistente versie voor
    zowel de Calculaties-pagina als de Project-details — dezelfde berekening, logica en
    styling (`.calc-breakdown-card` / `.calc-bd-*`, nu globaal in _APP_CSS).

    `result` mag een onderdeel-resultaat (`bereken_onderdeel`) of een projecttotaal
    (`bereken_project_totaal`) zijn. `marge_bedrag` zit in het onderdeel-resultaat; voor
    projecttotalen (en bevroren snapshots) wordt het afgeleid uit excl_btw minus de
    directe kosten, zodat de getoonde bedragen exact bij de projectcalculatie aansluiten."""
    totaal_ref = result["incl_btw"] if result.get("incl_btw", 0) > 0 else 1
    marge_bedrag = result.get("marge_bedrag")
    if marge_bedrag is None:
        marge_bedrag = result["excl_btw"] - result["materiaal"] - result["arbeid"] - result["toeslagen"]

    items = [
        ("droplet",      "#EFF6FF", "#2563EB", "Materiaal",             result["materiaal"]),
        ("person-badge", "#F0FDF4", "#059669", "Arbeid",                result["arbeid"]),
        ("plus-circle",  "#FFF7ED", "#D97706", "Toeslagen",             result["toeslagen"]),
        ("graph-up",     "#FFFBEB", "#D97706", f"Marge ({marge_pct}%)", marge_bedrag),
        ("bank",         "#F5F3FF", "#7C3AED", f"BTW ({btw_pct}%)",     result["btw_bedrag"]),
    ]

    rijen_bd = ""
    for i, (icon, icon_bg, icon_clr, label, bedrag) in enumerate(items):
        pct = (bedrag / totaal_ref * 100) if totaal_ref > 0 else 0
        bar_w = min(int(pct), 100)
        even_cls = "even" if i % 2 == 1 else ""
        rijen_bd += f"""
        <div class="calc-bd-row {even_cls}">
          <div style="flex:5;display:flex;align-items:center;">
            <div class="calc-bd-icon" style="background:{icon_bg};"><i class="bi bi-{icon}" style="font-size:14px;color:{icon_clr};"></i></div>
            <span class="calc-bd-label">{label}</span>
          </div>
          <div style="flex:2;" class="calc-bd-amount">{format_eur(bedrag)}</div>
          <div style="flex:2;text-align:right;">
            <div class="calc-bd-pct">{pct:.1f}%</div>
            <div class="calc-bd-bar-wrap"><div class="calc-bd-bar" style="width:{bar_w}%;"></div></div>
          </div>
        </div>"""

    st.markdown(
        '<div class="calc-breakdown-card">'
        '<div class="calc-bd-header">'
        '<div class="calc-bd-th" style="flex:5;">Post</div>'
        '<div class="calc-bd-th" style="flex:2;text-align:right;">Bedrag</div>'
        '<div class="calc-bd-th" style="flex:2;text-align:right;">% van totaal</div>'
        '</div>'
        + rijen_bd +
        '</div>',
        unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════
# SP-008: PRIJS-SNAPSHOTS — bevroren bedragen voor uitgebrachte
# offertes. Prijzen/tarieven wijzigen heeft geen effect meer op
# reeds verzonden offertes; de offerte-inhoud zelf wijzigen wel.
# ═════════════════════════════════════════════════════════════

FROZEN_STATUSSEN = {"Offerte verzonden", "Geaccepteerd", "In uitvoering", "Afgerond"}

def _calc_inputs_hash(project):
    """Hash van de offerte-INHOUD (onderdelen, marge, btw) — bewust zónder
    productprijzen of uurtarieven. Wijzigt de gebruiker de offerte zelf,
    dan wijkt de hash af en is de snapshot niet langer leidend."""
    return hashlib.md5(json.dumps({
        "onderdelen": project.get("onderdelen", []),
        "marge": project.get("marge", st.session_state.instellingen["standaard_marge"]),
        "btw":   project.get("btw",   st.session_state.instellingen["standaard_btw"]),
    }, sort_keys=True, default=str).encode()).hexdigest()

def _snapshot_actief(project):
    """True als de bevroren snapshot van dit project leidend is.

    BUG-08: een eenmaal bevroren offerte is een juridische momentopname. Zodra er
    een snapshot bestaat én de status bevroren is, is die snapshot leidend —
    ongeacht latere wijzigingen aan productprijzen, uurtarieven, instellingen,
    toeslag-percentages, marge of BTW. De inputs-hash wordt bewust NIET meer
    vergeleken (die bevatte marge/btw, waardoor zo'n wijziging het bedrag kon
    veranderen). De onderdelen-telling blijft als enige consistentiecheck: alleen
    wanneer de offerte-inhoud structureel wijzigt (een onderdeel erbij/eraf) legt
    verzeker_prijs_snapshot de snapshot opnieuw vast, zodat weergave en totaal
    blijven kloppen. Het hash-formaat is ongewijzigd, dus bestaande snapshots
    blijven geldig (geen migratie nodig)."""
    snap = project.get("prijs_snapshot")
    return (bool(snap)
            and project.get("status") in FROZEN_STATUSSEN
            and len(snap.get("onderdelen", [])) == len(project.get("onderdelen", [])))

def maak_prijs_snapshot(project):
    """Leg de actuele (live) berekening vast op het project."""
    marge = project.get("marge", st.session_state.instellingen["standaard_marge"])
    btw   = project.get("btw",   st.session_state.instellingen["standaard_btw"])
    project["prijs_snapshot"] = {
        "datum":       str(date.today()),
        "inputs_hash": _calc_inputs_hash(project),
        "onderdelen":  [bereken_onderdeel(o, marge, btw, project_id=project.get("id"))
                        for o in project.get("onderdelen", [])],
        "totaal":      _bereken_project_totaal_live(project),
    }

def verzeker_prijs_snapshot(project):
    """Maak of ververs de snapshot als het project bevroren hoort te zijn maar
    de snapshot ontbreekt of niet meer bij de inhoud past.
    Returnt True als het project is gewijzigd (caller moet dan save_data() doen)."""
    if project.get("status") not in FROZEN_STATUSSEN:
        return False
    if _snapshot_actief(project):
        return False
    maak_prijs_snapshot(project)
    return True

def bereken_project_totaal(project):
    """Totaal voor een project — uit de prijs-snapshot indien bevroren, anders live."""
    if _snapshot_actief(project):
        return dict(project["prijs_snapshot"]["totaal"])
    return _bereken_project_totaal_live(project)

def bereken_onderdelen_lijst(project, marge_pct, btw_pct):
    """Per-onderdeel berekeningen — uit de prijs-snapshot indien bevroren, anders live."""
    if _snapshot_actief(project):
        return [dict(c) for c in project["prijs_snapshot"]["onderdelen"]]
    return [bereken_onderdeel(o, marge_pct, btw_pct, project_id=project.get("id"))
            for o in project.get("onderdelen", [])]

def prune_personeel_projectkoppelingen():
    """Verwijder verwijzingen naar niet-meer-bestaande projecten uit
    personeel project_ids (SP-005). Aanroepen na elke projectverwijdering,
    vóór save_data()."""
    _bestaand = {p["id"] for p in st.session_state.projecten}
    for _mw in st.session_state.personeel:
        if _mw.get("project_ids"):
            _mw["project_ids"] = [pid for pid in _mw["project_ids"] if pid in _bestaand]

def status_badge(status):
    color, bg = STATUS_KLEUREN.get(status, ("#475569", "#F1F5F9"))
    return (f'<span style="display:inline-flex;align-items:center;gap:5px;background:{bg};'
            f'color:{color};padding:3px 10px;border-radius:99px;font-size:11px;font-weight:600;white-space:nowrap;">'
            f'<span style="width:5px;height:5px;border-radius:99px;background:{color};flex-shrink:0;"></span>'
            f'{status}</span>')

def format_eur(bedrag):
    # SP-012: aantal decimalen volgt Instellingen → Voorkeuren
    _dec = _inst_getal(st.session_state.instellingen, "decimalen", 2, int)
    return f"€ {bedrag:,.{_dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")

def format_datum(d):
    """Datum volgens Instellingen → Voorkeuren datumweergave (SP-012)."""
    try:
        if not isinstance(d, date):
            d = date.fromisoformat(str(d)[:10])
    except Exception:
        return str(d)
    if str(st.session_state.instellingen.get("datumweergave", "DD-MM-JJJJ")).startswith("MM"):
        return d.strftime("%m/%d/%Y")
    return d.strftime("%d-%m-%Y")

def h(value):
    """Escape gebruikersdata voor veilig gebruik in unsafe_allow_html HTML."""
    return _html.escape(str(value))

# ═══════════════════════════════════════════════════════════════════════════
# CENTRALE INVOERVALIDATIE (beta-blockers) — ÉÉN bron van waarheid, hergebruikt
# op Projecten, Calculaties, Klanten en Personeel. Elke validator geeft een
# (ok, fout)-tuple terug (bij ok is fout ""), zodat foutmeldingen, grenzen en
# regels overal identiek zijn en er geen gekopieerde validatiecode ontstaat.
# Voegt UITSLUITEND validatie toe; raakt geen bestaande bereken- of opslaglogica.
# ═══════════════════════════════════════════════════════════════════════════

# Realistische grenzen (min, max) — ruim genoeg voor échte projecten, streng
# tegen onzin (negatief, nul waar onlogisch, absurd hoog).
VAL_LIMIETEN = {
    "m2":        (0,         100_000),     # m² oppervlakte
    "meters":    (0,         100_000),     # strekkende meters (kit/afplak)
    "lagen":     (1,         20),
    "uren":      (0,         100_000),
    "prijs":     (0,         1_000_000),
    "uurtarief": (1,         10_000),
    "aantal":    (1,         1_000_000),
}

_VAL_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
# NL (1234 AB) óf BE/4-cijferig — de app bedient zowel Nederlandse als Belgische adressen.
_VAL_PC_RE    = re.compile(r"^([1-9]\d{3}\s?[A-Za-z]{2}|\d{4})$")
_VAL_TEL_RE   = re.compile(r"^[0-9+\-\s().]+$")
_VAL_VOWELS   = set("aeiouyàáäâãåèéêëìíîïòóôõöùúûü")

def _val_gibberish(tekst):
    """Heuristiek tegen willekeurige tekst (bv. 'dddddd'): één steeds herhaald
    teken, of vanaf 4 letters helemaal geen klinker → vrijwel zeker onzin."""
    letters = [c for c in tekst.lower() if c.isalpha()]
    if not letters:
        return True
    if len(set(letters)) == 1:
        return True
    if len(letters) >= 4 and not any(c in _VAL_VOWELS for c in letters):
        return True
    return False

def valideer_tekst(waarde, veld, min_len=2, verplicht=True, anti_gibberish=True):
    """Tekstvalidatie voor naam-/plaatsachtige velden."""
    t = (waarde or "").strip()
    if not t:
        return (False, f"{veld} is verplicht.") if verplicht else (True, "")
    if len(t) < min_len:
        return False, f"{veld} moet minimaal {min_len} tekens bevatten."
    if anti_gibberish and _val_gibberish(t):
        return False, f"Voer een geldige {veld.lower()} in (geen willekeurige tekens)."
    return True, ""

def valideer_email(waarde, verplicht=False):
    t = (waarde or "").strip()
    if not t:
        return (False, "E-mailadres is verplicht.") if verplicht else (True, "")
    if not _VAL_EMAIL_RE.match(t):
        return False, "Voer een geldig e-mailadres in (bijv. naam@bedrijf.nl)."
    return True, ""

def valideer_telefoon(waarde, verplicht=False):
    t = (waarde or "").strip()
    if not t:
        return (False, "Telefoonnummer is verplicht.") if verplicht else (True, "")
    if not _VAL_TEL_RE.match(t):
        return False, "Telefoonnummer mag alleen cijfers, spaties en + - ( ) bevatten."
    if not (8 <= len(re.sub(r"\D", "", t)) <= 15):
        return False, "Voer een geldig telefoonnummer in (8 tot 15 cijfers)."
    return True, ""

def valideer_postcode(waarde, verplicht=False):
    t = (waarde or "").strip()
    if not t:
        return (False, "Postcode is verplicht.") if verplicht else (True, "")
    if not _VAL_PC_RE.match(t):
        return False, "Voer een geldige postcode in (bijv. 5211 AB of 2000)."
    return True, ""

def valideer_getal(waarde, soort, veld, toestaan_nul=True):
    """Numerieke validatie tegen de centrale grenzen (VAL_LIMIETEN). Blokkeert
    negatief, absurd hoog en (optioneel) nul; laat geldige waarden ongemoeid."""
    minv, maxv = VAL_LIMIETEN[soort]
    try:
        w = float(waarde)
    except (TypeError, ValueError):
        return False, f"{veld} moet een getal zijn."
    if w < 0:
        return False, f"{veld} mag niet negatief zijn."
    if w == 0:
        return (True, "") if toestaan_nul else (False, f"{veld} moet groter zijn dan 0.")
    if w < minv:
        return False, f"{veld} moet minimaal {minv:g} zijn."
    if w > maxv:
        return False, f"{veld} is onrealistisch hoog (max {maxv:,.0f})."
    return True, ""

def eerste_validatiefout(*resultaten):
    """Geef de eerste foutmelding uit een reeks (ok, fout)-tuples, of "" als
    alles geldig is — houdt de aanroep op de pagina's compact en consistent."""
    for ok, fout in resultaten:
        if not ok:
            return fout
    return ""

def _pdf_cache_key(project):
    """MD5-hash van project + volledige instellingen (SP-007) → wijzigt zodra de
    projectdata óf de instellingen wijzigen, en is identiek voor identieke inhoud."""
    return hashlib.md5(
        json.dumps(
            {"project": project, "instellingen": st.session_state.instellingen},
            sort_keys=True, default=str
        ).encode()
    ).hexdigest()


# PERF: PDF-generatie was de zwaarste kostenpost op de Offertes-pagina (per offerte
# werd bij ELKE render een offerte- én factuur-PDF gemaakt; de cache was per-sessie,
# dus elke nieuwe sessie/host-herstart genereerde alles opnieuw → seconden). Nu een
# GLOBALE cache (@st.cache_data, server-breed, over sessies/reruns heen), gekeyd op de
# inhouds-hash: per projectversie maar één keer genereren. `_project` heeft een leading
# underscore → Streamlit hasht dat argument niet (alleen de hashbare cache_key). De hash
# is inhouds-gebaseerd, dus geen tenant-lek: identieke inhoud = identieke PDF.
@st.cache_data(show_spinner=False, max_entries=400)
def _offerte_pdf_cached(cache_key, _project):
    bestand = maak_offerte_pdf(_project)
    with open(bestand, "rb") as fh:
        raw = fh.read()
    return {"bytes": raw, "b64": base64.b64encode(raw).decode()}


@st.cache_data(show_spinner=False, max_entries=400)
def _factuur_pdf_cached(cache_key, _project):
    bestand = maak_factuur_pdf(_project)
    with open(bestand, "rb") as fh:
        raw = fh.read()
    return {"bytes": raw, "b64": base64.b64encode(raw).decode()}


def get_pdf_bytes(project):
    """Return offerte-PDF bytes + base64. Globaal gecachet op de inhouds-hash."""
    # SP-008: borg de prijs-snapshot vóór de hash → de key weerspiegelt de definitieve
    # projectinhoud. verzeker_prijs_snapshot muteert alleen bij de eerste keer.
    if verzeker_prijs_snapshot(project):
        save_data()
    return _offerte_pdf_cached(_pdf_cache_key(project), project)


def get_factuur_bytes(project):
    """Return factuur-PDF bytes + base64. Zelfde globale cache; prijs-snapshot is al
    door get_pdf_bytes geborgd."""
    return _factuur_pdf_cached(_pdf_cache_key(project), project)


def ui_alert(msg, type="success"):
    specs = {
        "success": ("check-circle-fill", "#166534", "#DCFCE7", "#BBF7D0"),
        "error":   ("x-circle-fill",     "#991B1B", "#FEE2E2", "#FECACA"),
        "info":    ("info-circle-fill",   "#1D4ED8", "#DBEAFE", "#BFDBFE"),
        "warning": ("exclamation-triangle-fill", "#92400E", "#FEF3C7", "#FDE68A"),
    }
    icon, fg, bg, border = specs.get(type, specs["info"])
    st.markdown(
        f'<div style="background:{bg};border:1px solid {border};border-radius:10px;'
        f'padding:11px 16px;display:flex;align-items:center;gap:10px;font-size:13px;'
        f'color:{fg};margin:6px 0;box-shadow:0 1px 3px rgba(0,0,0,0.04);">'
        f'<i class="bi bi-{icon}" style="font-size:15px;flex-shrink:0;"></i>'
        f'<span style="font-weight:500;">{msg}</span></div>',
        unsafe_allow_html=True
    )


def ga_naar_tab(tab_label="Overzicht"):
    """Klik een tab (op tekst) via JS (st.tabs kent geen programmatische selectie).
    Gebruikt door Klanten/Personeel/Projecten om ná 'toevoegen' terug te keren naar het
    overzicht, én door Projecten > Bewerken om naar '+ Nieuw project' te springen.
    BELANGRIJK: een UNIEKE nonce per aanroep, anders ziet Streamlit een identieke
    components.html en HERGEBRUIKT het iframe → het script draait dan alleen de 1e keer.
    De nonce forceert een her-mount zodat de klik élke keer gebeurt. Retry tot 4s."""
    _n = st.session_state.get("_ovz_tab_nonce", 0) + 1
    st.session_state["_ovz_tab_nonce"] = _n
    _components.html("""<script>(function(){
/* nonce __NONCE__ */
var p=window.parent.document;
var doel=__TABJSON__;
var n=0;
function go(){
    n++;
    var tabs=p.querySelectorAll('button[data-baseweb="tab"], [role="tab"]');
    var t=null;
    for(var i=0;i<tabs.length;i++){ if(tabs[i].textContent.trim()===doel){ t=tabs[i]; break; } }
    if(t){
        if(t.getAttribute('aria-selected')==='true') return;   // gelukt → klaar
        t.click();
    }
    if(n<40) setTimeout(go, 100);   // blijf tot 4s proberen tot de tab echt geselecteerd is
}
go();
})();</script>""".replace("__NONCE__", str(_n)).replace("__TABJSON__", json.dumps(tab_label)), height=0)


# Projectkoppeling (Personeel): "Algemeen" (ZZP) staat als eerste optie IN het
# projecten-dropdown i.p.v. een apart vinkje — selecteren = telt op elk project mee.
_ALGEMEEN_OPT = "__algemeen__"


def _koppel_label(v):
    """Labeltekst voor een optie in de projectkoppeling-multiselect."""
    if v == _ALGEMEEN_OPT:
        return "Algemeen — alle projecten (ZZP)"
    return next((p["naam"] for p in st.session_state.projecten if p["id"] == v), str(v))


# =====================================================
# PDF GENERATIE
# =====================================================

# BUG-01: platform-onafhankelijke fontlading. Geen hardcoded Windows-pad meer.
# Volgorde: meegeleverde DM Sans (huisstijl-font, identiek aan de app-UI), dan
# systeem-Arial (Windows → identieke weergave), Liberation Sans (Linux,
# Arial-metrisch), DejaVu Sans (Linux), macOS Arial, en als gegarandeerde fallback
# de met de app meegeleverde DejaVuSans in ./fonts (open SIL OFL-licentie). Alle
# kandidaten zijn Unicode-TTF's (volledige € / accenten / typografie-ondersteuning),
# zodat PDF-generatie werkt op Windows, Linux, Docker en cloud-hosting.
# Let op: _registreer_pdf_fonts() registreert de eerste complete set ALTIJD onder de
# familienaam "Arial" (interne alias) — de PDF-code blijft set_font("Arial", …) gebruiken.
_BUNDLE_FONTS = Path(__file__).parent / "fonts"
_PDF_FONT_SETS = [
    # Huisstijl: DM Sans (zelfde font als de web-UI) — SIL OFL, meegeleverd in ./fonts.
    (str(_BUNDLE_FONTS / "DMSans-Regular.ttf"), str(_BUNDLE_FONTS / "DMSans-Bold.ttf"),
     str(_BUNDLE_FONTS / "DMSans-Italic.ttf"),  str(_BUNDLE_FONTS / "DMSans-BoldItalic.ttf")),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf",
     "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/arialbi.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf",
     "/Library/Fonts/Arial Italic.ttf", "/Library/Fonts/Arial Bold Italic.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
     "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
    (str(_BUNDLE_FONTS / "DejaVuSans.ttf"), str(_BUNDLE_FONTS / "DejaVuSans-Bold.ttf"),
     str(_BUNDLE_FONTS / "DejaVuSans-Oblique.ttf"), str(_BUNDLE_FONTS / "DejaVuSans-BoldOblique.ttf")),
]

def _registreer_pdf_fonts(pdf):
    """Registreer een Unicode-TTF als familie 'Arial' (4 stijlen) op de eerste
    locatie waar een complete set bestaat. Returnt de gevonden setnaam of None."""
    for _set in _PDF_FONT_SETS:
        _r, _b, _i, _bi = _set
        try:
            if all(Path(p).is_file() for p in _set):
                pdf.add_font("Arial", "",   _r)
                pdf.add_font("Arial", "B",  _b)
                pdf.add_font("Arial", "I",  _i)
                pdf.add_font("Arial", "BI", _bi)
                return _r
        except Exception:
            continue
    return None

def maak_offerte_pdf(project):
    """Genereer professionele PDF offerte — CoatFlow stijl"""
    klant   = get_klant(project["klant_id"])
    inst    = st.session_state.instellingen
    marge   = project.get("marge",  inst["standaard_marge"])
    btw_pct = project.get("btw",    inst["standaard_btw"])
    totaal  = bereken_project_totaal(project)          # ongewijzigde berekening

    # ── helpers ──────────────────────────────────────────────
    def fmt(bedrag):
        """€ 1.098,20  (Nederlandse notatie; decimalen via Instellingen — SP-012)"""
        _dec = _inst_getal(inst, "decimalen", 2, int)
        s = f"{float(bedrag):,.{_dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"€ {s}"

    def fmt_datum(d):
        """Nederlandse notatie: '14-06-2026' of '14-06-2026 13:48' (punt 13)."""
        if not d: return ""
        s = str(d)
        try:
            if len(s) > 10 and s[10] in ("T", " "):
                return datetime.fromisoformat(s[:16]).strftime("%d-%m-%Y %H:%M")
            return date.fromisoformat(s[:10]).strftime("%d-%m-%Y")
        except Exception:
            return s

    TOESLAG_NAMEN = {
        "toeslag_hoogte":  "Hoogte toeslag",
        "toeslag_spoed":   "Spoed toeslag",
        "toeslag_buiten":  "Buitenwerk toeslag",
        "toeslag_steiger": "Steiger toeslag",
        "toeslag_weekend": "Weekendtoeslag",
        "toeslag_avond":   "Avondtoeslag",
        "toeslag_winter":  "Wintertoeslag",
        "toeslag_reis":    "Reiskostentoeslag",
    }

    # BUG-07: permanent, opgeslagen offertenummer — nooit opnieuw berekend, niet
    # afhankelijk van instellingen/sortering. _init_offertenummers() heeft dit al
    # toegekend; de fallback dient enkel als vangnet voor onverwacht ontbreken.
    offerte_nr = project.get("offerte_nummer")
    if not offerte_nr:
        try:
            _offerte_volgnr = int(inst.get("offerte_startnummer", 1) or 1) - 1 + int(project.get("id", 1))
        except (TypeError, ValueError):
            _offerte_volgnr = int(inst.get("offerte_startnummer", 1) or 1)
        offerte_nr = f"{inst.get('offerte_prefix', 'OFF') or 'OFF'}-{_offerte_volgnr:04d}"

    # ── kleurpalet (R, G, B) ─────────────────────────────────
    def _hex_rgb(hx, fallback=(37, 99, 235)):
        try:
            hx = str(hx).lstrip("#")
            return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))
        except Exception:
            return fallback
    NAVY   = (8,  26,  54)
    # SP-012: accentkleur volgt Instellingen → Bedrijfsgegevens "Bedrijfskleur"
    BLUE   = _hex_rgb(inst.get("bedrijfskleur", "#2563EB"))
    BLUELT = (239, 246, 255);  GRBG   = (248, 250, 252)
    GRLT   = (241, 245, 249);  BORDER = (226, 232, 240)
    TDARK  = (15,  23,  42);   TMED   = (71,  85, 105)
    TLIGHT = (148, 163, 184);  WHITE  = (255, 255, 255)
    ORANGE = (194,  90,  30)

    # ── PDF object ───────────────────────────────────────────
    pdf = FPDF()
    # BUG-01: platform-onafhankelijke Unicode-fonts (Windows Arial → Linux/macOS →
    # meegeleverde DejaVuSans). Ondersteunt € en alle Unicode-tekens, zonder
    # afhankelijkheid van een lokaal Windows-fontpad.
    _registreer_pdf_fonts(pdf)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    PW, PH = 210, 297          # A4 mm
    ML, MR = 18, 18
    CW     = PW - ML - MR      # inhouds­breedte = 174 mm
    FOOTY  = PH - 18           # footer start-y  = 279 mm

    # ── teken­helpers ─────────────────────────────────────────
    def fr(x, y, w, h, c):
        """Gevulde rechthoek (geen rand)."""
        pdf.set_fill_color(*c); pdf.set_draw_color(*c)
        pdf.rect(x, y, w, h, "F")

    def br(x, y, w, h, fc, bc):
        """Gevulde rechthoek met rand."""
        pdf.set_line_width(0.2)
        pdf.set_fill_color(*fc); pdf.set_draw_color(*bc)
        pdf.rect(x, y, w, h, "FD")

    def hl(y, x1=None, x2=None, c=BORDER):
        """Horizontale lijn."""
        pdf.set_draw_color(*c); pdf.set_line_width(0.2)
        pdf.line(x1 if x1 is not None else ML,
                 y,
                 x2 if x2 is not None else ML + CW,
                 y)

    # ── footer en pagina­wissel ───────────────────────────────
    def draw_footer():
        fr(0, FOOTY - 0.5, PW, PH - FOOTY + 0.5, NAVY)
        hl(FOOTY - 0.5, 0, PW, BLUE)
        parts = [inst["bedrijfsnaam"]]
        for k, pfx in [("telefoon", ""), ("email", ""), ("iban", "IBAN: "),
                       ("kvk", "KVK: "), ("btw_nummer", "BTW: "), ("website", "")]:
            v = inst.get(k, "")
            if v: parts.append(pfx + v)
        pdf.set_font("Arial", "", 7)
        pdf.set_text_color(*TLIGHT)
        pdf.set_xy(ML, FOOTY + 2.5)
        pdf.cell(CW, 5, "   •   ".join(parts), align="C")
        # SP-012: paginanummers volgen Instellingen → Offertes → PDF-opmaak
        if inst.get("pdf_paginanummers_tonen", True):
            pdf.set_xy(ML, FOOTY + 8)
            pdf.cell(CW, 4, f"Pagina {pdf.page_no()}", align="C")

    def new_page():
        draw_footer()
        pdf.add_page()
        fr(0, 0, PW, 9, NAVY); hl(0, 0, PW, BLUE)
        pdf.set_font("Arial", "B", 8); pdf.set_text_color(*WHITE)
        pdf.set_xy(ML, 1.5); pdf.cell(85, 5, inst["bedrijfsnaam"])
        pdf.set_font("Arial", "", 7.5)
        pdf.set_text_color(*(130, 145, 170))
        pdf.set_xy(ML + 85, 1.5)
        pdf.cell(CW - 85, 5,
                 f"Offerte  ·  {offerte_nr}", align="R")
        pdf.set_y(15)

    def chk(h=38):
        """Voeg nieuwe pagina toe als er onvoldoende ruimte is."""
        if pdf.get_y() + h > FOOTY - 5:
            new_page()

    # ════════════════════════════════════════════════════════
    # 1. HEADER
    # ════════════════════════════════════════════════════════
    fr(0, 0, PW, 56, NAVY)
    fr(0, 0, PW,  3, BLUE)

    # SP-012: bedrijfslogo (Instellingen → Bedrijfsgegevens) indien aanwezig én aangezet
    _tx = ML   # tekst-startpositie, verschuift wanneer er een logo staat
    if inst.get("pdf_logo_tonen", True) and inst.get("logo_b64"):
        try:
            import io as _io
            _li = pdf.image(_io.BytesIO(base64.b64decode(inst["logo_b64"])), x=ML, y=7, h=14)
            _lw = getattr(_li, "rendered_width", None) or 16
            _tx = ML + min(float(_lw), 50) + 5
        except Exception:
            _tx = ML   # ongeldig logo → layout als vanouds

    # Bedrijfsnaam + tagline + contact
    pdf.set_font("Arial", "B", 22); pdf.set_text_color(*WHITE)
    pdf.set_xy(_tx, 10); pdf.cell(115, 10, inst["bedrijfsnaam"])

    pdf.set_font("Arial", "", 8)
    pdf.set_text_color(*(160, 175, 200))
    pdf.set_xy(_tx, 22); pdf.cell(115, 5, "Professioneel schildersbedrijf")

    # SP-012: specifiek offerte-e-mailadres indien ingesteld
    ct = "   •   ".join(x for x in [
        inst.get("telefoon", ""),
        inst.get("email_offertes", "") or inst.get("email", ""),
        inst.get("website", "")
    ] if x)
    if ct:
        pdf.set_font("Arial", "", 7.5)
        pdf.set_text_color(*(120, 135, 160))
        pdf.set_xy(_tx, 30); pdf.cell(115, 5, ct)

    # Offertegegevens rechts — datumnotatie volgt Instellingen → Voorkeuren (SP-012)
    _df = "%m/%d/%Y" if str(inst.get("datumweergave", "DD-MM-JJJJ")).startswith("MM") else "%d-%m-%Y"
    datum_nu   = datetime.now().strftime(_df)
    geldig_tot = (datetime.now() + timedelta(
        days=_inst_getal(inst, "offerte_geldigheid", 30, int)
    )).strftime(_df)

    pdf.set_font("Arial", "B", 20); pdf.set_text_color(*WHITE)
    pdf.set_xy(136, 8); pdf.cell(56, 10, "OFFERTE", align="R")
    fr(150, 20, 42, 0.8, BLUE)   # blauwe accent­lijn

    for i, (lbl, val) in enumerate([
        ("Nr.",       offerte_nr),
        ("Datum",     datum_nu),
        ("Geldig tot", geldig_tot),
    ]):
        yr = 24 + i * 7
        pdf.set_font("Arial", "", 7.5)
        pdf.set_text_color(*(120, 135, 160))
        pdf.set_xy(140, yr); pdf.cell(20, 5, lbl + ":", align="R")
        pdf.set_font("Arial", "B", 8); pdf.set_text_color(*WHITE)
        pdf.set_xy(161, yr); pdf.cell(31, 5, val, align="R")

    pdf.set_y(63)

    # ════════════════════════════════════════════════════════
    # 2. VAN / AAN  — professionele blokken
    # ════════════════════════════════════════════════════════
    y0   = pdf.get_y()
    CHW  = (CW - 8) // 2    # ~83 mm per kolom
    AANX = ML + CHW + 8

    def _van_rows():
        rows = [(True, 9.5, inst["bedrijfsnaam"])]
        if inst.get("adres"): rows.append((False, 8, inst["adres"]))
        pp = " ".join(filter(None, [inst.get("postcode",""), inst.get("plaats","")]))
        if pp: rows.append((False, 8, pp))
        if inst.get("telefoon"): rows.append((False, 8, "T   " + str(inst["telefoon"])))
        _em = inst.get("email_offertes","") or inst.get("email","")
        if _em: rows.append((False, 8, "E   " + str(_em)))
        if inst.get("kvk"):        rows.append((False, 8, f"KvK   {inst['kvk']}"))
        if inst.get("btw_nummer"): rows.append((False, 8, f"BTW   {inst['btw_nummer']}"))
        return rows

    def _aan_rows():
        if not klant: return [(True, 9.5, "Onbekende klant")]
        rows = [(True, 9.5, klant["naam"])]
        if klant.get("bedrijf") and klant["bedrijf"] != klant["naam"]:
            rows.append((False, 8, klant["bedrijf"]))
        if klant.get("contactpersoon"):
            rows.append((False, 8, "T.a.v. " + str(klant["contactpersoon"])))
        if klant.get("adres"): rows.append((False, 8, klant["adres"]))
        kpp = " ".join(filter(None, [klant.get("postcode",""), klant.get("stad","")]))
        if kpp: rows.append((False, 8, kpp))
        if klant.get("telefoon"): rows.append((False, 8, "T   " + str(klant["telefoon"])))
        if klant.get("email"):    rows.append((False, 8, "E   " + str(klant["email"])))
        return rows

    van_rows = _van_rows(); aan_rows = _aan_rows()
    ROWH   = 4.8
    box_h  = 9.5 + max(len(van_rows), len(aan_rows)) * ROWH + 2

    for xk, lbl, rows, accent in [(ML, "VAN", van_rows, BLUE), (AANX, "AAN", aan_rows, NAVY)]:
        br(xk, y0, CHW, box_h, WHITE, BORDER)
        fr(xk, y0, CHW, 6.5, GRLT)             # label-strip
        fr(xk, y0, 2.5, box_h, accent)         # accentbalk links
        pdf.set_font("Arial", "B", 7); pdf.set_text_color(*TLIGHT)
        pdf.set_xy(xk + 6, y0 + 1.7); pdf.cell(CHW - 9, 4, lbl)
        yr = y0 + 9.5
        for bold, sz, tekst in rows:
            pdf.set_font("Arial", "B" if bold else "", sz)
            pdf.set_text_color(*(NAVY if bold else TMED))
            pdf.set_xy(xk + 6, yr); pdf.cell(CHW - 9, ROWH, str(tekst)[:48])
            yr += ROWH
    pdf.set_y(y0 + box_h + 7)

    # ════════════════════════════════════════════════════════
    # 3. PROJECT INFO KAART — uitgebreid
    # ════════════════════════════════════════════════════════
    ypk = pdf.get_y()
    _ond_lst = project.get("onderdelen", [])
    _n_ond   = len(_ond_lst)
    _opp = sum(float(o.get("m2", 0) or 0) for o in _ond_lst if not onderdeel_is_meterwerk(o))
    _mtr = sum(float(o.get("meters", 0) or 0) for o in _ond_lst if onderdeel_is_meterwerk(o))
    _opp_str = f"{_opp:g} m²" + (f"  +  {_mtr:g} m" if _mtr else "")
    _projnr  = f"P-{int(project['id']):04d}"

    card_h = 33
    br(ML, ypk, CW, card_h, BLUELT, BORDER)
    fr(ML, ypk, 3.5, card_h, BLUE)

    pdf.set_font("Arial", "B", 11); pdf.set_text_color(*NAVY)
    pdf.set_xy(ML + 8, ypk + 3.2); pdf.cell(CW - 70, 6, str(project["naam"])[:42])
    pdf.set_font("Arial", "B", 8.5); pdf.set_text_color(*BLUE)
    pdf.set_xy(ML + CW - 63, ypk + 3.6); pdf.cell(60, 6, str(project.get("status", "Concept")), align="R")

    hl(ypk + 11.5, ML + 8, ML + CW - 4, BORDER)

    metas = [("Projectnummer", _projnr),
             ("Aangemaakt",    fmt_datum(project.get("aangemaakt", ""))),
             ("Oppervlakte",   _opp_str),
             ("Onderdelen",    str(_n_ond))]
    colw = (CW - 12) / 4
    for i, (lbl, val) in enumerate(metas):
        cx = ML + 8 + i * colw
        pdf.set_font("Arial", "", 7); pdf.set_text_color(*TLIGHT)
        pdf.set_xy(cx, ypk + 14); pdf.cell(colw - 2, 4, lbl.upper())
        pdf.set_font("Arial", "B", 8.5); pdf.set_text_color(*TDARK)
        pdf.set_xy(cx, ypk + 18.3); pdf.cell(colw - 2, 5, str(val)[:24])

    if project.get("adres"):
        pdf.set_font("Arial", "", 7); pdf.set_text_color(*TLIGHT)
        pdf.set_xy(ML + 8, ypk + 25.8); pdf.cell(16, 4, "LOCATIE")
        pdf.set_font("Arial", "", 8.5); pdf.set_text_color(*TMED)
        pdf.set_xy(ML + 25, ypk + 25.5); pdf.cell(CW - 30, 5, str(project.get("adres", ""))[:72])

    pdf.set_y(ypk + card_h + 7)

    # ════════════════════════════════════════════════════════
    # 4. INTRO TEKST
    # ════════════════════════════════════════════════════════
    intro = inst.get("offerte_tekst", "").strip()
    if intro:
        chk(20)
        pdf.set_font("Arial", "I", 8.5); pdf.set_text_color(*TMED)
        pdf.set_x(ML); pdf.multi_cell(CW, 5, intro)
        pdf.ln(5)

    # ════════════════════════════════════════════════════════
    # 5. WERKZAAMHEDEN OVERZICHT
    # ════════════════════════════════════════════════════════
    chk(20)
    y_sh = pdf.get_y()
    fr(ML, y_sh, CW, 10, NAVY)
    pdf.set_font("Arial", "B", 8.5); pdf.set_text_color(*WHITE)
    pdf.set_xy(ML + 4, y_sh + 2)
    pdf.cell(CW - 8, 6, "WERKZAAMHEDEN OVERZICHT")
    pdf.set_y(y_sh + 10 + 4)

    # Interne prijsopbouw (materiaal/arbeid) standaard VERBORGEN — opt-in via
    # Instellingen → PDF-opmaak (punt 3). Nieuwe sleutel zodat bestaande config
    # niet plots interne kosten op de klant-offerte toont.
    _toon_detail = bool(inst.get("pdf_intern_tonen", False))

    _ond_calcs = bereken_onderdelen_lijst(project, marge, btw_pct)
    for idx, (ond, calc) in enumerate(zip(project["onderdelen"], _ond_calcs)):
        wz_list  = ond.get("werkzaamheden", [])
        tsl_list = [TOESLAG_NAMEN[k] for k in TOESLAG_NAMEN if ond.get(k)]

        # Eenheid: kitwerk/afplak in strekkende meter, overig in m² (punt 12)
        if onderdeel_is_meterwerk(ond):
            sub_str = f"{float(ond.get('meters', 0) or 0):g} meter"
        else:
            _lg = ond.get("lagen", 1)
            sub_str = f"{float(ond.get('m2', 0) or 0):g} m²    •    {_lg} {'laag' if _lg == 1 else 'lagen'}"

        body_h = (5 if wz_list else 0) + (5 if tsl_list else 0) + (6 if _toon_detail else 0)
        card_h = 15 + max(body_h, 1) + 3

        chk(card_h + 4)
        yc = pdf.get_y()

        # Kaart + subtiele accentbalk links
        br(ML, yc, CW, card_h, WHITE, BORDER)
        fr(ML, yc, 3, card_h, BLUE)

        # Naam + eenheid (links)
        pdf.set_font("Arial", "B", 10.5); pdf.set_text_color(*NAVY)
        pdf.set_xy(ML + 7, yc + 2.8); pdf.cell(108, 6, f"{idx + 1}.   {str(ond['naam'])[:40]}")
        pdf.set_font("Arial", "", 8); pdf.set_text_color(*TLIGHT)
        pdf.set_xy(ML + 7, yc + 9); pdf.cell(108, 5, sub_str)

        # Totaal onderdeel (rechts, prominent)
        pdf.set_font("Arial", "", 6.5); pdf.set_text_color(*TLIGHT)
        pdf.set_xy(ML + CW - 60, yc + 3); pdf.cell(57, 4, "TOTAAL ONDERDEEL", align="R")
        pdf.set_font("Arial", "B", 12); pdf.set_text_color(*NAVY)
        pdf.set_xy(ML + CW - 60, yc + 7.3); pdf.cell(57, 6, fmt(calc["excl_btw"]), align="R")

        hl(yc + 15, ML + 5, ML + CW - 5, BORDER)
        yd = yc + 16.5

        if wz_list:
            pdf.set_font("Arial", "", 7.5); pdf.set_text_color(*TLIGHT)
            pdf.set_xy(ML + 7, yd); pdf.cell(26, 5, "Werkzaamheden")
            pdf.set_font("Arial", "", 8); pdf.set_text_color(*TMED)
            pdf.set_xy(ML + 35, yd); pdf.cell(CW - 40, 5, ("    ".join(f"• {w}" for w in wz_list))[:96])
            yd += 5

        if tsl_list:
            pdf.set_font("Arial", "", 7.5); pdf.set_text_color(*TLIGHT)
            pdf.set_xy(ML + 7, yd); pdf.cell(26, 5, "Toeslagen")
            pdf.set_font("Arial", "", 8); pdf.set_text_color(*ORANGE)
            pdf.set_xy(ML + 35, yd); pdf.cell(CW - 40, 5, ("    ".join(f"• {t}" for t in tsl_list))[:96])
            yd += 5

        if _toon_detail:
            parts = []
            if inst.get("pdf_materiaalkosten_tonen", True): parts.append(f"Materiaal {fmt(calc['materiaal'])}")
            if inst.get("pdf_arbeidskosten_tonen", True):   parts.append(f"Arbeid {fmt(calc['arbeid'])}")
            pdf.set_font("Arial", "", 7.5); pdf.set_text_color(*TLIGHT)
            pdf.set_xy(ML + 7, yd); pdf.cell(26, 5, "Specificatie")
            pdf.set_font("Arial", "", 8); pdf.set_text_color(*TMED)
            pdf.set_xy(ML + 35, yd); pdf.cell(CW - 40, 5, "        ".join(parts))
            yd += 6

        pdf.set_y(yc + card_h + 3)

    # ════════════════════════════════════════════════════════
    # 6. TOTALEN BLOK — eindbedrag dominant (punt 5)
    # ════════════════════════════════════════════════════════
    chk(56)
    pdf.ln(3)
    hl(pdf.get_y(), c=(200, 210, 225))
    pdf.ln(5)
    y_tot0 = pdf.get_y()

    TX, TLW, TVW = ML + CW - 82, 45, 35   # x=110  label=45  waarde=35

    def tot_rij(lbl, bedrag, sz=9, accent=False):
        yr = pdf.get_y()
        pdf.set_font("Arial", "B" if accent else "", sz)
        pdf.set_text_color(*(TDARK if accent else TMED))
        pdf.set_xy(TX, yr); pdf.cell(TLW, 7, lbl)
        pdf.set_font("Arial", "B", sz); pdf.set_text_color(*TDARK)
        pdf.set_xy(TX + TLW, yr); pdf.cell(TVW, 7, fmt(bedrag), align="R")
        pdf.ln(7)

    # Linker info-kolom: geldigheid / planning (optioneel)
    _info_rows = [("Geldig tot", geldig_tot)]
    if inst.get("uitvoeringsduur"): _info_rows.append(("Uitvoeringsduur", str(inst["uitvoeringsduur"])))
    if inst.get("startdatum"):      _info_rows.append(("Verwachte start", fmt_datum(inst["startdatum"])))
    yi = y_tot0
    for _lbl, _val in _info_rows:
        pdf.set_font("Arial", "", 6.8); pdf.set_text_color(*TLIGHT)
        pdf.set_xy(ML, yi); pdf.cell(50, 4, _lbl.upper())
        pdf.set_font("Arial", "B", 8.5); pdf.set_text_color(*TDARK)
        pdf.set_xy(ML, yi + 3.8); pdf.cell(60, 5, str(_val))
        yi += 10.5

    # Rechter kolom: interne totalen (standaard verborgen) → subtotaal → BTW
    if _toon_detail and inst.get("pdf_materiaalkosten_tonen", True):
        tot_rij("Totaal materiaal", totaal["materiaal"])
    if _toon_detail and inst.get("pdf_arbeidskosten_tonen", True):
        tot_rij("Totaal arbeid", totaal["arbeid"])
    if totaal["toeslagen"] > 0:
        tot_rij("Totaal toeslagen", totaal["toeslagen"])

    if inst.get("pdf_btw_tonen", True):
        tot_rij("Subtotaal excl. BTW", totaal["excl_btw"], sz=9.5, accent=True)
        tot_rij(f"BTW {btw_pct}%",     totaal["btw_bedrag"])
        _eind_lbl, _eind_bedrag = "TOTAAL INCL. BTW", totaal["incl_btw"]
    else:
        tot_rij("Subtotaal excl. BTW", totaal["excl_btw"], sz=9.5, accent=True)
        _eind_lbl, _eind_bedrag = "TOTAAL EXCL. BTW", totaal["excl_btw"]

    pdf.ln(1)
    # GROOT eindbedrag — navy paneel met blauwe accentbalk
    yb = pdf.get_y()
    fr(TX - 4, yb, TLW + TVW + 8, 17, NAVY)
    fr(TX - 4, yb, 2.5, 17, BLUE)
    pdf.set_font("Arial", "", 7); pdf.set_text_color(*(160, 175, 200))
    pdf.set_xy(TX, yb + 3); pdf.cell(TLW + TVW, 4, _eind_lbl)
    pdf.set_font("Arial", "B", 17); pdf.set_text_color(*WHITE)
    pdf.set_xy(TX, yb + 6.8); pdf.cell(TLW + TVW - 2, 9, fmt(_eind_bedrag), align="R")
    pdf.set_y(max(yi, yb + 17) + 8)

    # ════════════════════════════════════════════════════════
    # 6b. INBEGREPEN / NIET INBEGREPEN (punt 9 & 10)
    # ════════════════════════════════════════════════════════
    if inst.get("pdf_inbegrepen_tonen", True):
        _incl = inst.get("inbegrepen_items") or ["Materiaal", "Arbeid", "Afplakken", "Opruimen", "Afvoer klein afval"]
        _excl = inst.get("niet_inbegrepen_items") or ["Steigerhuur", "Grote herstelwerkzaamheden", "Extra stucwerk"]
        _ie_h = 11 + max(len(_incl), len(_excl)) * 4.8 + 2
        chk(_ie_h + 6)
        yie = pdf.get_y()
        GAP2 = 8; IEW = (CW - GAP2) // 2
        for _xk, _titel, _items, _tc, _mk in [
            (ML,            "INBEGREPEN",      _incl, (16, 122, 87),  (16, 185, 129)),
            (ML + IEW + GAP2, "NIET INBEGREPEN", _excl, (153, 27, 27),  (203, 110, 110)),
        ]:
            br(_xk, yie, IEW, _ie_h, WHITE, BORDER)
            pdf.set_font("Arial", "B", 8); pdf.set_text_color(*_tc)
            pdf.set_xy(_xk + 5, yie + 3); pdf.cell(IEW - 8, 5, _titel)
            hl(yie + 9, _xk + 5, _xk + IEW - 5, BORDER)
            _yy = yie + 11
            for _it in _items:
                fr(_xk + 6, _yy + 1.4, 1.8, 1.8, _mk)          # gekleurde marker
                pdf.set_font("Arial", "", 8); pdf.set_text_color(*TMED)
                pdf.set_xy(_xk + 10, _yy); pdf.cell(IEW - 14, 4.6, str(_it)[:42])
                _yy += 4.8
        pdf.set_y(yie + _ie_h + 7)

    # ════════════════════════════════════════════════════════
    # 7. AFSLUITING + AKKOORD OPDRACHTGEVER (compact — punt 6 & 7)
    # ════════════════════════════════════════════════════════
    chk(58)

    bt = inst.get("bedanktekst", "").strip()
    at = inst.get("afsluittekst", "").strip()
    _slot = "  ".join(x for x in [bt, at] if x)
    if _slot:
        pdf.set_font("Arial", "", 8.5); pdf.set_text_color(*TMED)
        pdf.set_x(ML); pdf.multi_cell(CW, 4.6, _slot); pdf.ln(2.5)

    _groet = inst.get("handtekening", "").strip() or "Met vriendelijke groet,"
    pdf.set_font("Arial", "", 8.5); pdf.set_text_color(*TMED)
    pdf.set_x(ML); pdf.cell(CW, 5, _groet); pdf.ln(5)
    cp = inst.get("contactpersoon", "").strip()
    if cp:
        pdf.set_font("Arial", "B", 9); pdf.set_text_color(*TDARK)
        pdf.set_x(ML); pdf.cell(CW, 5, cp); pdf.ln(4.5)
    pdf.set_font("Arial", "", 8.5); pdf.set_text_color(*TMED)
    pdf.set_x(ML); pdf.cell(CW, 5, inst["bedrijfsnaam"]); pdf.ln(8)

    # Akkoord opdrachtgever — gestructureerd, voorbereid op digitale ondertekening (punt 7)
    if inst.get("pdf_handtekening_tonen", True):
        ys = pdf.get_y()
        SIGW, SIGH = 112, 30
        br(ML, ys, SIGW, SIGH, GRBG, BORDER)
        fr(ML, ys, SIGW, 7, NAVY)
        pdf.set_font("Arial", "B", 7.5); pdf.set_text_color(*WHITE)
        pdf.set_xy(ML + 4, ys + 1.6); pdf.cell(SIGW - 8, 4, "AKKOORD OPDRACHTGEVER")
        pdf.set_font("Arial", "", 7.5); pdf.set_text_color(*TLIGHT)
        pdf.set_xy(ML + 4, ys + 10);  pdf.cell(20, 4, "Naam")
        hl(ys + 15, ML + 4, ML + 62, (180, 190, 205))
        pdf.set_xy(ML + 68, ys + 10); pdf.cell(20, 4, "Datum")
        hl(ys + 15, ML + 68, ML + SIGW - 5, (180, 190, 205))
        pdf.set_xy(ML + 4, ys + 19);  pdf.cell(30, 4, "Handtekening")
        hl(ys + 27, ML + 4, ML + SIGW - 5, (180, 190, 205))
        pdf.set_y(ys + SIGH + 7)

    # ════════════════════════════════════════════════════════
    # 8. ALGEMENE VOORWAARDEN — compacte samenvatting (punt 8)
    # ════════════════════════════════════════════════════════
    chk(28)
    pdf.set_font("Arial", "B", 8.5); pdf.set_text_color(*NAVY)
    pdf.set_x(ML); pdf.cell(CW, 5, "Algemene voorwaarden"); pdf.ln(5)
    hl(pdf.get_y()); pdf.ln(3.5)

    vw_lines = []
    if inst.get("betalingstermijn"):
        vw_lines.append(f"Betaling binnen {inst['betalingstermijn']} dagen na factuurdatum.")
    if inst.get("offerte_geldigheid"):
        vw_lines.append(f"Deze offerte is {inst['offerte_geldigheid']} dagen geldig.")
    try:
        _aanb = float(inst.get("aanbetaling_pct", 0) or 0)
    except (TypeError, ValueError):
        _aanb = 0
    if _aanb > 0:
        vw_lines.append(f"Aanbetaling {_aanb:g}% ({fmt(totaal['incl_btw'] * _aanb / 100)}) bij opdracht.")
    if inst.get("iban"):
        vw_lines.append(f"Betaling op IBAN {inst['iban']}.")
    vw_lines.append("Op al onze werkzaamheden zijn onze algemene voorwaarden van toepassing.")

    for line in vw_lines:
        _y = pdf.get_y()
        fr(ML + 1, _y + 1.7, 1.6, 1.6, BLUE)
        pdf.set_font("Arial", "", 8); pdf.set_text_color(*TMED)
        pdf.set_xy(ML + 5, _y); pdf.cell(CW - 5, 4.8, line)
        pdf.ln(4.8)

    # Volledige voorwaarden alleen indien expliciet aangezet; anders als bijlage aangeboden
    vw = inst.get("voorwaarden", "").strip()
    if vw and inst.get("pdf_voorwaarden_volledig_tonen", False):
        pdf.ln(2)
        pdf.set_font("Arial", "", 7); pdf.set_text_color(*TLIGHT)
        pdf.set_x(ML); pdf.multi_cell(CW, 4, vw)
    elif vw:
        pdf.ln(1.5)
        pdf.set_font("Arial", "I", 7); pdf.set_text_color(*TLIGHT)
        pdf.set_x(ML); pdf.cell(CW, 4, "De volledige algemene voorwaarden worden op verzoek als bijlage verstrekt.")
        pdf.ln(4)
    pdf.ln(2)

    # ════════════════════════════════════════════════════════
    # FOOTER
    # ════════════════════════════════════════════════════════
    draw_footer()

    bestand = str(tempfile.gettempdir()) + f"/offerte_{project['id']:04d}.pdf"
    pdf.output(bestand)
    return bestand

def maak_factuur_pdf(project):
    """Genereer een professionele, premium FACTUUR-PDF (CoatFlow-huisstijl).
    Hergebruikt EXACT dezelfde berekening (bereken_project_totaal /
    bereken_onderdelen_lijst — snapshot-bewust), dezelfde bedrijfs-/klantgegevens
    en dezelfde factuurnummer-logica. Uitsluitend de layout/presentatie is herzien."""
    klant   = get_klant(project["klant_id"])
    inst    = st.session_state.instellingen
    marge   = project.get("marge", inst["standaard_marge"])
    btw_pct = project.get("btw",   inst["standaard_btw"])
    totaal  = bereken_project_totaal(project)          # ongewijzigde berekening (snapshot-bewust)
    factuur_nr = project.get("factuur_nummer") or verzeker_factuur_nummer(project)

    # ── opmaak-helpers ──
    def fmt(bedrag):
        _dec = _inst_getal(inst, "decimalen", 2, int)
        s = f"{float(bedrag):,.{_dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"€ {s}"

    def _hex_rgb(hx, fb=(37, 99, 235)):
        try:
            hx = str(hx).lstrip("#")
            return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))
        except Exception:
            return fb

    TOESLAG_NAMEN = {
        "toeslag_hoogte": "Hoogte", "toeslag_spoed": "Spoed", "toeslag_buiten": "Buitenwerk",
        "toeslag_steiger": "Steiger", "toeslag_weekend": "Weekend", "toeslag_avond": "Avond",
        "toeslag_winter": "Winter", "toeslag_reis": "Reiskosten",
    }

    NAVY = (8, 26, 54);  BLUE = _hex_rgb(inst.get("bedrijfskleur", "#2563EB"))
    INK  = (17, 24, 39);  MUT  = (100, 116, 139); SOFT = (148, 163, 184)
    LINE = (228, 232, 240); CARD = (247, 249, 252); WHITE = (255, 255, 255)
    GREEN = (5, 122, 85);  RED = (190, 30, 45)

    # ── datums ──
    _df = "%m/%d/%Y" if str(inst.get("datumweergave", "DD-MM-JJJJ")).startswith("MM") else "%d-%m-%Y"
    try:
        _termijn = int(inst.get("factuurtermijn", 30) or 30)
    except (TypeError, ValueError):
        _termijn = 30
    try:
        _fdt = datetime.fromisoformat(str(project.get("factuur_datum"))[:16]) if project.get("factuur_datum") else datetime.now()
    except Exception:
        _fdt = datetime.now()
    _verval = _fdt + timedelta(days=_termijn)
    factuur_datum = _fdt.strftime(_df)
    verval_datum  = _verval.strftime(_df)

    # ── factuurstatus (read-only; afgeleide default — voorbereid op uitbreiding) ──
    _status = project.get("factuur_status")
    if not _status:
        _status = "Vervallen" if _verval.date() < date.today() else "Verzonden"
    _STAT = {
        "Concept":   (MUT,   (241, 245, 249)),
        "Verzonden": (BLUE,  (235, 242, 255)),
        "Betaald":   (GREEN, (223, 247, 236)),
        "Vervallen": (RED,   (254, 232, 234)),
    }
    _stat_fg, _stat_bg = _STAT.get(_status, _STAT["Verzonden"])

    # ── PDF + tekenhelpers ──
    pdf = FPDF(); _registreer_pdf_fonts(pdf)
    pdf.set_auto_page_break(auto=False); pdf.add_page()
    PW, PH = 210, 297; ML, MR = 16, 16; CW = PW - ML - MR; FOOTY = PH - 16

    def fr(x, y, w, h, c):
        pdf.set_fill_color(*c); pdf.set_draw_color(*c); pdf.rect(x, y, w, h, "F")

    def rr(x, y, w, h, fc, bc=None, r=2.4):
        pdf.set_line_width(0.25); pdf.set_fill_color(*fc)
        style = "F"
        if bc:
            pdf.set_draw_color(*bc); style = "FD"
        try:
            pdf.rect(x, y, w, h, style, round_corners=True, corner_radius=r)
        except Exception:
            pdf.rect(x, y, w, h, style)

    def hl(y, x1=None, x2=None, c=LINE, w=0.2):
        pdf.set_draw_color(*c); pdf.set_line_width(w)
        pdf.line(x1 if x1 is not None else ML, y, x2 if x2 is not None else ML + CW, y)

    def draw_footer():
        fr(0, FOOTY + 2, PW, PH - FOOTY - 2, NAVY); fr(0, FOOTY + 2, PW, 1.5, BLUE)
        parts = [inst["bedrijfsnaam"]]
        for k, p in [("telefoon", ""), ("email", ""), ("iban", "IBAN "),
                     ("kvk", "KvK "), ("btw_nummer", "BTW "), ("website", "")]:
            v = inst.get(k, "")
            if v: parts.append(p + v)
        pdf.set_font("Arial", "", 7); pdf.set_text_color(*SOFT)
        pdf.set_xy(ML, FOOTY + 4.5); pdf.cell(CW, 4, "   •   ".join(parts), align="C")
        if inst.get("pdf_paginanummers_tonen", True):
            pdf.set_xy(ML, FOOTY + 9)
            pdf.cell(CW, 3.5, f"Factuur {factuur_nr}   ·   Pagina {pdf.page_no()}", align="C")

    def kop_klein():
        fr(0, 0, PW, 8, NAVY); fr(0, 0, PW, 1.5, BLUE)
        pdf.set_font("Arial", "B", 8); pdf.set_text_color(*WHITE)
        pdf.set_xy(ML, 1.4); pdf.cell(90, 5, inst["bedrijfsnaam"])
        pdf.set_font("Arial", "", 7.5); pdf.set_text_color(*(150, 165, 190))
        pdf.set_xy(ML + 90, 1.4); pdf.cell(CW - 90, 5, f"Factuur {factuur_nr}   ·   {_status}", align="R")
        pdf.set_y(14)

    def new_page():
        draw_footer(); pdf.add_page(); kop_klein()

    def chk(h):
        if pdf.get_y() + h > FOOTY - 2:
            new_page()

    def _initialen(naam):
        d = [w for w in str(naam).split() if w]
        if not d: return "SP"
        return (d[0][0] + (d[1][0] if len(d) > 1 else "")).upper()

    # ════════════════════════════════════════════════════════
    # 1. COMPACTE HEADER — bedrijfsidentiteit + FACTUUR + status
    # ════════════════════════════════════════════════════════
    HEADH = 34
    fr(0, 0, PW, HEADH, NAVY); fr(0, 0, PW, 2.5, BLUE)

    _tx = ML; _logo = False
    if inst.get("pdf_logo_tonen", True) and inst.get("logo_b64"):
        try:
            import io as _io
            _li = pdf.image(_io.BytesIO(base64.b64decode(inst["logo_b64"])), x=ML, y=7, h=14)
            _lw = getattr(_li, "rendered_width", None) or 16
            _tx = ML + min(float(_lw), 46) + 5; _logo = True
        except Exception:
            _tx = ML; _logo = False
    if not _logo:
        # Merkmonogram als er geen logo is → sterkere identiteit
        rr(ML, 8, 13, 13, BLUE, r=3)
        pdf.set_font("Arial", "B", 13); pdf.set_text_color(*WHITE)
        pdf.set_xy(ML, 11.0); pdf.cell(13, 6, _initialen(inst["bedrijfsnaam"]), align="C")
        _tx = ML + 18

    pdf.set_font("Arial", "B", 17); pdf.set_text_color(*WHITE)
    pdf.set_xy(_tx, 8.5); pdf.cell(110, 8, str(inst["bedrijfsnaam"])[:32])
    pdf.set_font("Arial", "", 7.5); pdf.set_text_color(*(150, 165, 190))
    pdf.set_xy(_tx, 17.5); pdf.cell(110, 4, "Professioneel schildersbedrijf")
    ct = "   •   ".join(x for x in [
        inst.get("telefoon", ""),
        inst.get("email_facturen", "") or inst.get("email", ""),
        inst.get("website", ""),
    ] if x)
    if ct:
        pdf.set_font("Arial", "", 7.5); pdf.set_text_color(*(130, 148, 176))
        pdf.set_xy(_tx, 22.3); pdf.cell(110, 4, ct)

    pdf.set_font("Arial", "B", 20); pdf.set_text_color(*WHITE)
    pdf.set_xy(PW - MR - 60, 8.5); pdf.cell(60, 9, "FACTUUR", align="R")
    pdf.set_font("Arial", "B", 7.5)
    _sw = pdf.get_string_width(_status.upper()) + 9
    rr(PW - MR - _sw, 20, _sw, 6, _stat_bg, r=3)
    pdf.set_text_color(*_stat_fg); pdf.set_xy(PW - MR - _sw, 21.1); pdf.cell(_sw, 4, _status.upper(), align="C")

    # ════════════════════════════════════════════════════════
    # 2. HERO — TE BETALEN dominant + direct zichtbaar
    # ════════════════════════════════════════════════════════
    hy = HEADH + 6; HEROH = 22
    rr(ML, hy, CW, HEROH, CARD, LINE, r=3)
    fr(ML, hy, 2.5, HEROH, BLUE)
    pdf.set_font("Arial", "B", 7.5); pdf.set_text_color(*MUT)
    pdf.set_xy(ML + 8, hy + 4); pdf.cell(80, 4, "TE BETALEN")
    pdf.set_font("Arial", "B", 26); pdf.set_text_color(*NAVY)
    pdf.set_xy(ML + 8, hy + 8); pdf.cell(100, 11, fmt(totaal["incl_btw"]))
    _hx = ML + CW - 64
    for i, (lbl, val) in enumerate([("Factuurdatum", factuur_datum),
                                    ("Vervaldatum", verval_datum),
                                    ("Betaaltermijn", f"{_termijn} dagen")]):
        ry = hy + 4.6 + i * 4.7
        pdf.set_font("Arial", "", 7); pdf.set_text_color(*SOFT)
        pdf.set_xy(_hx, ry); pdf.cell(34, 4, lbl)
        pdf.set_font("Arial", "B", 8); pdf.set_text_color(*INK)
        pdf.set_xy(_hx + 30, ry); pdf.cell(34, 4, val, align="R")

    # ════════════════════════════════════════════════════════
    # 3. FACTUUR AAN + PROJECT — twee compacte kolommen
    # ════════════════════════════════════════════════════════
    by = hy + HEROH + 6

    def _klant_rows():
        if not klant: return ["Onbekende klant"]
        r = [klant["naam"]]
        if klant.get("bedrijf") and klant["bedrijf"] != klant["naam"]: r.append(klant["bedrijf"])
        if klant.get("contactpersoon"): r.append("T.a.v. " + str(klant["contactpersoon"]))
        if klant.get("adres"): r.append(klant["adres"])
        _pp = " ".join(filter(None, [klant.get("postcode", ""), klant.get("stad", "")]))
        if _pp: r.append(_pp)
        if klant.get("email"): r.append(str(klant["email"]))
        return r

    _ond_lst = project.get("onderdelen", [])
    _opp = sum(float(o.get("m2", 0) or 0) for o in _ond_lst if not onderdeel_is_meterwerk(o))
    _mtr = sum(float(o.get("meters", 0) or 0) for o in _ond_lst if onderdeel_is_meterwerk(o))
    _oppstr = f"{_opp:g} m²" + (f" + {_mtr:g} m" if _mtr else "")
    _proj_rows = [str(project["naam"]), f"Projectnr.  P-{int(project['id']):04d}"]
    if project.get("offerte_nummer"): _proj_rows.append(f"Offerte  {project['offerte_nummer']}")
    if project.get("adres"): _proj_rows.append(str(project["adres"]))
    _proj_rows.append(f"{_oppstr}  ·  {len(_ond_lst)} onderdelen")

    kr = _klant_rows()
    BOXH = 9 + max(len(kr), len(_proj_rows)) * 4.6 + 2
    GAP = 8; BW = (CW - GAP) / 2
    for bx, titel, rows, accent in [(ML, "FACTUUR AAN", kr, BLUE), (ML + BW + GAP, "PROJECT", _proj_rows, NAVY)]:
        rr(bx, by, BW, BOXH, WHITE, LINE, r=3)
        fr(bx, by, 2.2, BOXH, accent)
        pdf.set_font("Arial", "B", 6.8); pdf.set_text_color(*SOFT)
        pdf.set_xy(bx + 6, by + 3); pdf.cell(BW - 9, 4, titel)
        yy = by + 8.5
        for j, row in enumerate(rows):
            pdf.set_font("Arial", "B" if j == 0 else "", 9 if j == 0 else 8)
            pdf.set_text_color(*(NAVY if j == 0 else MUT))
            pdf.set_xy(bx + 6, yy); pdf.cell(BW - 9, 4.4, str(row)[:44]); yy += 4.6
    pdf.set_y(by + BOXH + 6)

    # Optionele intro (factuurtekst) — compact, één blok
    _intro = (inst.get("factuur_tekst", "") or "").strip()
    if _intro:
        chk(12)
        pdf.set_font("Arial", "I", 8.5); pdf.set_text_color(*MUT)
        pdf.set_x(ML); pdf.multi_cell(CW, 4.6, _intro); pdf.ln(3)

    # ════════════════════════════════════════════════════════
    # 4. FACTUURREGELS — compacte, scanbare tabel
    # ════════════════════════════════════════════════════════
    chk(20)
    ty = pdf.get_y()
    fr(ML, ty, CW, 8, NAVY)
    pdf.set_font("Arial", "B", 7); pdf.set_text_color(*WHITE)
    pdf.set_xy(ML + 4, ty + 2.4); pdf.cell(100, 3.5, "OMSCHRIJVING")
    pdf.set_xy(ML + CW - 70, ty + 2.4); pdf.cell(34, 3.5, "OMVANG", align="R")
    pdf.set_xy(ML + CW - 34, ty + 2.4); pdf.cell(30, 3.5, "BEDRAG EXCL.", align="R")
    pdf.set_y(ty + 8)

    _ond_calcs = bereken_onderdelen_lijst(project, marge, btw_pct)
    if not _ond_lst:
        pdf.set_font("Arial", "I", 8.5); pdf.set_text_color(*SOFT)
        pdf.set_x(ML + 4); pdf.cell(CW, 7, "Geen onderdelen op dit project."); pdf.ln(7)
    _zebra = False
    for idx, (ond, calc) in enumerate(zip(_ond_lst, _ond_calcs)):
        wz_list = ond.get("werkzaamheden", [])
        tsl_list = [TOESLAG_NAMEN[k] for k in TOESLAG_NAMEN if ond.get(k)]
        if onderdeel_is_meterwerk(ond):
            qty = f"{float(ond.get('meters', 0) or 0):g} m"
            lagen_str = ""
        else:
            _lg = ond.get("lagen", 1)
            qty = f"{float(ond.get('m2', 0) or 0):g} m²"
            lagen_str = f"{_lg} {'laag' if _lg == 1 else 'lagen'}"
        _dp = []
        if wz_list:   _dp.append(", ".join(wz_list))
        if lagen_str: _dp.append(lagen_str)
        if tsl_list:  _dp.append("Toeslag: " + ", ".join(tsl_list))
        detail = "     ·     ".join(_dp)
        rowh = 10 if detail else 7.5
        chk(rowh + 1)
        ry = pdf.get_y()
        if _zebra:
            fr(ML, ry, CW, rowh, (250, 251, 253))
        _zebra = not _zebra
        pdf.set_font("Arial", "B", 9); pdf.set_text_color(*INK)
        pdf.set_xy(ML + 4, ry + 1.6); pdf.cell(100, 5, f"{idx + 1}.   {str(ond['naam'])[:46]}")
        if detail:
            pdf.set_font("Arial", "", 7.5); pdf.set_text_color(*MUT)
            pdf.set_xy(ML + 9, ry + 6.0); pdf.cell(96, 4, detail[:92])
        pdf.set_font("Arial", "", 8.5); pdf.set_text_color(*MUT)
        pdf.set_xy(ML + CW - 70, ry + 1.9); pdf.cell(34, 5, qty, align="R")
        pdf.set_font("Arial", "B", 9.5); pdf.set_text_color(*INK)
        pdf.set_xy(ML + CW - 34, ry + 1.9); pdf.cell(30, 5, fmt(calc["excl_btw"]), align="R")
        hl(ry + rowh, ML, ML + CW, (236, 239, 244))
        pdf.set_y(ry + rowh)

    # ════════════════════════════════════════════════════════
    # 5. TOTALEN — breakdown + TE BETALEN band
    # ════════════════════════════════════════════════════════
    chk(34)
    pdf.ln(3)
    TX = ML + CW - 80; TLW = 46; TVW = 34

    def trij(lbl, bedrag, sz=8.5, bold=False):
        yy = pdf.get_y()
        pdf.set_font("Arial", "B" if bold else "", sz); pdf.set_text_color(*(INK if bold else MUT))
        pdf.set_xy(TX, yy); pdf.cell(TLW, 6, lbl)
        pdf.set_font("Arial", "B", sz); pdf.set_text_color(*INK)
        pdf.set_xy(TX + TLW, yy); pdf.cell(TVW, 6, fmt(bedrag), align="R"); pdf.ln(6)

    if totaal["toeslagen"] > 0:
        trij("Toeslagen", totaal["toeslagen"])
    trij("Subtotaal excl. BTW", totaal["excl_btw"], bold=True)
    trij(f"BTW {btw_pct}%", totaal["btw_bedrag"])
    pdf.ln(1)
    yb = pdf.get_y()
    rr(TX - 4, yb, TLW + TVW + 8, 13, NAVY, r=3)
    pdf.set_font("Arial", "", 7); pdf.set_text_color(*(165, 180, 205))
    pdf.set_xy(TX, yb + 2.4); pdf.cell(TLW, 4, "TE BETALEN")
    pdf.set_font("Arial", "B", 14); pdf.set_text_color(*WHITE)
    pdf.set_xy(TX, yb + 5.3); pdf.cell(TLW + TVW - 2, 7, fmt(totaal["incl_btw"]), align="R")
    pdf.set_y(yb + 13 + 7)

    # ════════════════════════════════════════════════════════
    # 6. BETAALGEGEVENS — scanbaar grid
    # ════════════════════════════════════════════════════════
    chk(30)
    py = pdf.get_y(); PAYH = 26
    rr(ML, py, CW, PAYH, CARD, LINE, r=3)
    pdf.set_font("Arial", "B", 8); pdf.set_text_color(*NAVY)
    pdf.set_xy(ML + 6, py + 3.4); pdf.cell(100, 4, "BETAALGEGEVENS")
    hl(py + 9, ML + 6, ML + CW - 6, LINE)
    _pay = [
        ("IBAN", inst.get("iban", "") or "—"),
        ("Factuurnummer", factuur_nr),
        ("Vervaldatum", verval_datum),
        ("T.n.v.", inst.get("bedrijfsnaam", "")),
        ("Betaaltermijn", f"{_termijn} dagen"),
        ("Te betalen", fmt(totaal["incl_btw"])),
    ]
    _pcw = (CW - 12) / 3
    for i, (lbl, val) in enumerate(_pay):
        cx = ML + 6 + (i % 3) * _pcw
        cy = py + 11.5 + (i // 3) * 7.4
        pdf.set_font("Arial", "", 6.8); pdf.set_text_color(*SOFT)
        pdf.set_xy(cx, cy); pdf.cell(_pcw - 3, 3.6, lbl.upper())
        pdf.set_font("Arial", "B", 8.5); pdf.set_text_color(*(GREEN if lbl == "Te betalen" else INK))
        pdf.set_xy(cx, cy + 3.6); pdf.cell(_pcw - 3, 4.6, str(val)[:30])
    pdf.set_y(py + PAYH + 6)

    # ════════════════════════════════════════════════════════
    # 7. ZAKELIJKE AFSLUITING — bewust ontworpen, geen lege ruimte
    # ════════════════════════════════════════════════════════
    chk(26)
    hl(pdf.get_y(), ML, ML + CW, LINE); pdf.ln(4)
    pdf.set_font("Arial", "B", 9.5); pdf.set_text_color(*NAVY)
    pdf.set_x(ML); pdf.cell(CW, 5, "Bedankt voor uw opdracht"); pdf.ln(5.5)
    _voet = (inst.get("factuur_voettekst", "") or "").strip() \
        or "Wij waarderen het vertrouwen in onze dienstverlening."
    pdf.set_font("Arial", "", 8.5); pdf.set_text_color(*MUT)
    pdf.set_x(ML); pdf.multi_cell(CW, 4.7,
        f"Gelieve het bedrag van {fmt(totaal['incl_btw'])} vóór {verval_datum} te voldoen op IBAN "
        f"{inst.get('iban', '')} onder vermelding van factuurnummer {factuur_nr}.  {_voet}")
    pdf.ln(2.5)
    _cp = (inst.get("contactpersoon", "") or "").strip()
    _vraag_delen = [x for x in [_cp, inst.get("email_facturen", "") or inst.get("email", ""), inst.get("telefoon", "")] if x]
    if _vraag_delen:
        pdf.set_font("Arial", "", 8); pdf.set_text_color(*SOFT)
        pdf.set_x(ML); pdf.cell(CW, 4.6, "Vragen over deze factuur?   " + "   ·   ".join(_vraag_delen))
        pdf.ln(5)
    pdf.set_font("Arial", "B", 8.5); pdf.set_text_color(*NAVY)
    pdf.set_x(ML); pdf.cell(CW, 5, inst["bedrijfsnaam"])

    draw_footer()
    bestand = str(tempfile.gettempdir()) + f"/factuur_{project['id']:04d}.pdf"
    pdf.output(bestand)
    return bestand

# =====================================================
# CSS
# =====================================================

_APP_CSS = """
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=DM+Mono:wght@400;500&display=swap');

:root {
  --r-sm:   6px;
  --r-btn:  8px;
  --r-md:   10px;
  --r-lg:   14px;
  --r-xl:   16px;
  --r-full: 9999px;
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 20px; --sp-6: 24px;
  --text-xs: 11px; --text-sm: 12px; --text-base: 13px; --text-md: 14px;
  --text-lg: 16px; --text-xl: 20px; --text-2xl: 24px; --text-3xl: 28px;
}

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

.stApp { background-color: #F0F4F8; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: var(--r-full); }
::-webkit-scrollbar-thumb:hover { background: #94A3B8; }

/* ===================== SIDEBAR ===================== */
[data-testid="stSidebar"] {
    width: 220px !important;
    min-width: 220px !important;
    max-width: 220px !important;
    background: #081A36 !important;
    border-right: 1px solid rgba(255,255,255,0.06) !important;
}
[data-testid="stSidebar"] > div:first-child {
    width: 220px !important;
    overflow: hidden !important;
}
[data-testid="stSidebarContent"] {
    padding: 0 12px !important;
}

/* option_menu container — round corners, sidebar color */
[data-testid="stSidebar"] ul.nav {
    background: #081A36 !important;
    border-radius: 14px !important;
    padding: 4px !important;
}



/* Every nav item — transparent bg, white text */
[data-testid="stSidebar"] a.nav-link,
[data-testid="stSidebar"] .nav-link {
    background: transparent !important;
    background-color: transparent !important;
    color: rgba(255,255,255,0.7) !important;
    border-radius: 10px !important;
    margin: 2px 0 !important;
    padding: 8px 12px !important;
    transition: background 0.15s ease !important;
}
[data-testid="stSidebar"] a.nav-link *,
[data-testid="stSidebar"] .nav-link * {
    color: rgba(255,255,255,0.7) !important;
    fill: rgba(255,255,255,0.7) !important;
}
[data-testid="stSidebar"] a.nav-link:hover,
[data-testid="stSidebar"] .nav-link:hover {
    background: rgba(255,255,255,0.08) !important;
    color: white !important;
}
[data-testid="stSidebar"] a.nav-link:hover *,
[data-testid="stSidebar"] .nav-link:hover * {
    color: white !important;
}

/* Selected — blauw blok, witte tekst + icoon */
[data-testid="stSidebar"] a.nav-link.nav-link-selected,
[data-testid="stSidebar"] .nav-link-selected {
    background: #2563EB !important;
    background-color: #2563EB !important;
    color: white !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    box-shadow: 0 2px 8px rgba(37,99,235,0.4) !important;
}
[data-testid="stSidebar"] a.nav-link.nav-link-selected *,
[data-testid="stSidebar"] .nav-link-selected * {
    color: white !important;
    fill: white !important;
}

[data-testid="stSidebarCollapsedControl"] {
    background: #081A36 !important;
    border-right: 2px solid #2563EB !important;
}

/* ===================== MAIN BUTTONS ===================== */
.stButton > button {
    background: #081A36;
    color: white !important;
    border: none;
    border-radius: var(--r-btn);
    font-family: 'DM Sans', sans-serif;
    font-weight: 500;
    font-size: 13px;
    padding: 5px 14px;
    height: 36px;
    line-height: 1;
    transition: all 0.18s cubic-bezier(0.4,0,0.2,1);
    width: 100%;
    box-shadow: 0 1px 3px rgba(8,26,54,0.2);
}
.stButton > button:hover {
    background: #041124;
    transform: translateY(-1px);
    box-shadow: 0 4px 10px rgba(8,26,54,0.25);
}
.stButton > button:active {
    transform: translateY(0);
    box-shadow: 0 1px 2px rgba(8,26,54,0.2);
}
/* Secondary / outline style for download buttons */
.stDownloadButton > button {
    background: white !important;
    color: #0F172A !important;
    border: 1.5px solid #E2E8F0 !important;
    border-radius: var(--r-btn);
    font-family: 'DM Sans', sans-serif;
    font-weight: 500;
    font-size: 13px;
    padding: 5px 14px;
    height: 36px;
    transition: all 0.18s cubic-bezier(0.4,0,0.2,1);
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.stDownloadButton > button:hover {
    border-color: #94A3B8 !important;
    background: #F8FAFC !important;
    box-shadow: 0 2px 6px rgba(0,0,0,0.08);
    transform: translateY(-1px);
}
/* Form submit buttons */
.stFormSubmitButton > button {
    border-radius: var(--r-btn) !important;
    height: 36px !important;
    font-size: 13px !important;
    font-weight: 600 !important;
    padding: 5px 16px !important;
    box-shadow: 0 1px 3px rgba(8,26,54,0.2) !important;
    transition: all 0.18s ease !important;
}
.stFormSubmitButton > button:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 10px rgba(8,26,54,0.25) !important;
}
/* Primaire form-submit knoppen (Opslaan, Aanmaken …) — donkerblauw vanaf de eerste
   paint. Stond voorheen alleen in de per-pagina (iframe-)CSS die later laadt, waardoor
   de knop eerst het thema-blauw (#2563EB) toonde en pas na een rerun donkerblauw werd. */
.stFormSubmitButton > button[kind="primaryFormSubmit"] {
    background: #081A36 !important;
    border-color: #081A36 !important;
    color: white !important;
}
.stFormSubmitButton > button[kind="primaryFormSubmit"]:hover {
    background: #041124 !important;
    border-color: #041124 !important;
}
/* Secundaire form-submit knoppen (Annuleren, Reset in formulieren) */
.stFormSubmitButton > button[kind="secondaryFormSubmit"] {
    background: white !important;
    color: #475569 !important;
    border: 1px solid #CBD5E1 !important;
    box-shadow: none !important;
}
.stFormSubmitButton > button[kind="secondaryFormSubmit"]:hover {
    background: #F8FAFC !important;
    border-color: #94A3B8 !important;
    box-shadow: none !important;
    transform: none !important;
}

/* ===================== METRIC CARDS ===================== */
.metric-card {
    background: white;
    padding: 16px 18px 18px;
    border-radius: 14px;
    border: 1px solid #E2E8F0;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    margin-bottom: 16px;
    transition: box-shadow 0.2s ease, transform 0.2s ease;
    position: relative;
    overflow: hidden;
}
.metric-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    border-radius: 14px 14px 0 0;
}
.metric-card.blue::before { background: linear-gradient(90deg, #2563EB, #60A5FA); }
.metric-card.green::before { background: linear-gradient(90deg, #059669, #34D399); }
.metric-card.amber::before { background: linear-gradient(90deg, #D97706, #FCD34D); }
.metric-card.indigo::before { background: linear-gradient(90deg, #4F46E5, #818CF8); }

.metric-card:hover {
    box-shadow: 0 8px 24px rgba(0,0,0,0.1);
    transform: translateY(-2px);
}
.metric-card .mc-icon {
    width: 34px; height: 34px;
    border-radius: 9px;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
    margin-bottom: 12px;
}
.metric-card .mc-icon.blue { background: #EFF6FF; }
.metric-card .mc-icon.green { background: #F0FDF4; }
.metric-card .mc-icon.amber { background: #FFFBEB; }
.metric-card .mc-icon.indigo { background: #EEF2FF; }

.metric-card .mc-label {
    font-size: var(--text-xs);
    color: #94A3B8;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 4px;
}
.metric-card .mc-value {
    font-size: var(--text-3xl);
    font-weight: 800;
    color: #0F172A;
    letter-spacing: -1px;
    line-height: 1.1;
    margin-bottom: 4px;
}
.metric-card .mc-sub {
    font-size: var(--text-sm);
    color: #94A3B8;
    font-weight: 400;
}

/* ===================== SECTION CARDS ===================== */
.section-card {
    background: white;
    padding: 20px;
    border-radius: 14px;
    border: 1px solid #E2E8F0;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    margin-bottom: 16px;
}
.section-title {
    font-size: var(--text-md);
    font-weight: 600;
    color: #0F172A;
    letter-spacing: -0.1px;
    margin-bottom: 4px;
}
.section-divider {
    height: 1px;
    background: #F1F5F9;
    margin: 12px 0 16px;
}

/* ===================== PROJECT ROW CARDS ===================== */
.proj-row-card {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 11px 14px;
    border-radius: 10px;
    border: 1px solid #F1F5F9;
    background: #FAFBFC;
    margin-bottom: 6px;
    cursor: pointer;
    transition: all 0.16s cubic-bezier(0.4,0,0.2,1);
    text-decoration: none;
    position: relative;
}
.proj-row-card:hover {
    background: #F0F7FF;
    border-color: #BFDBFE;
    box-shadow: 0 2px 10px rgba(37,99,235,0.07);
    transform: translateX(2px);
}
.proj-row-card:hover .proj-arrow {
    opacity: 1;
    transform: translateX(0);
}
.proj-icon {
    width: 36px; height: 36px;
    background: linear-gradient(135deg, #EFF6FF, #DBEAFE);
    border-radius: 9px;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
    flex-shrink: 0;
}
.proj-info { flex: 1; min-width: 0; }
.proj-name {
    font-size: 13px;
    font-weight: 600;
    color: #0F172A;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.proj-client {
    font-size: 11px;
    color: #94A3B8;
    margin-top: 1px;
}
.proj-badge {
    padding: 3px 8px;
    border-radius: 99px;
    font-size: 11px;
    font-weight: 600;
    white-space: nowrap;
    flex-shrink: 0;
}
.proj-amount {
    font-size: 13px;
    font-weight: 600;
    color: #0F172A;
    font-family: 'DM Mono', monospace;
    white-space: nowrap;
    flex-shrink: 0;
}
.proj-arrow {
    color: #94A3B8;
    font-size: 14px;
    opacity: 0;
    transform: translateX(-4px);
    transition: all 0.15s ease;
    flex-shrink: 0;
}

/* ===================== TAKEN ===================== */
.task-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 10px;
    border-radius: 8px;
    margin-bottom: 3px;
    transition: background 0.12s ease;
    position: relative;
}
.task-row:hover { background: #F8FAFC; }
.task-priority-dot {
    width: 6px; height: 6px;
    border-radius: 99px;
    flex-shrink: 0;
    margin-top: 1px;
}
.task-label {
    font-size: 13px;
    color: #1E293B;
    flex: 1;
    transition: all 0.2s ease;
}
.task-label.done {
    text-decoration: line-through;
    color: #94A3B8;
}
.task-del-btn {
    width: 22px; height: 22px;
    border: 1px solid #E2E8F0;
    background: white;
    border-radius: 6px;
    color: #CBD5E1;
    font-size: 11px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    opacity: 0;
    transition: all 0.12s ease;
    flex-shrink: 0;
    line-height: 1;
    padding: 0;
    font-family: inherit;
}
.task-row:hover .task-del-btn { opacity: 1; }
.task-del-btn:hover {
    background: #FEF2F2;
    border-color: #FECACA;
    color: #EF4444;
}

/* ===================== ACTION CARDS ===================== */
.action-card {
    background: white;
    border: 1px solid #E2E8F0;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 8px;
    cursor: pointer;
    transition: all 0.18s cubic-bezier(0.4,0,0.2,1);
    display: flex;
    align-items: flex-start;
    gap: 12px;
    text-decoration: none;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.action-card:hover {
    border-color: #93C5FD;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.08), 0 6px 20px rgba(37,99,235,0.1);
    transform: translateY(-2px);
}
.action-card-icon {
    width: 36px; height: 36px;
    border-radius: 9px;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px;
    flex-shrink: 0;
}
.action-card-icon.blue { background: linear-gradient(135deg, #EFF6FF, #DBEAFE); }
.action-card-icon.green { background: linear-gradient(135deg, #F0FDF4, #DCFCE7); }
.action-card-icon.purple { background: linear-gradient(135deg, #F5F3FF, #EDE9FE); }
.action-card-body {}
.action-card-title {
    font-size: 13px;
    font-weight: 600;
    color: #0F172A;
    line-height: 1.3;
}
.action-card-desc {
    font-size: 11px;
    color: #94A3B8;
    margin-top: 2px;
    line-height: 1.4;
}

/* ===================== FORMS ===================== */
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stSelectbox > div > div,
.stTextArea > div > div > textarea {
    border-radius: 10px !important;
    border: 1.5px solid #E2E8F0 !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 14px !important;
    background: #FAFAFA !important;
}
/* "Press Enter to apply / submit form" hint onder/naast invulvakken verbergen */
[data-testid="InputInstructions"],
[data-testid="stWidgetInstructions"],
.stTextInput [data-testid="InputInstructions"],
.stNumberInput [data-testid="InputInstructions"],
.stTextArea [data-testid="InputInstructions"] {
    display: none !important;
}

/* ===================== TABS ===================== */
.stTabs [data-baseweb="tab-list"] {
    background-color: #F1F5F9;
    border-radius: 10px;
    padding: 4px;
    gap: 2px;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 7px;
    font-weight: 500;
    font-size: 13px;
    color: #64748B;
    padding: 6px 14px;
}
.stTabs [data-baseweb="tab"]:hover { color: #2563EB; }
/* Actieve tab = transparant (toont de tab-list-achtergrond), geen eigen vlak.
   Alleen de tekst kleurt merk-blauw zodat zichtbaar blijft welke tab actief is.
   Consistent op álle pagina's (globale CSS). */
.stTabs [aria-selected="true"] {
    background-color: transparent !important;
    color: #2563EB !important;
    box-shadow: none !important;
}
.stTabs [aria-selected="true"]:hover { color: #2563EB !important; }

/* ===================== MISC ===================== */
hr { border: none; border-top: 1px solid #E2E8F0; margin: 16px 0; }

.info-box {
    background: #EFF6FF;
    border: 1px solid #BFDBFE;
    border-radius: 10px;
    padding: 12px 16px;
    font-size: 13px;
    color: #1D4ED8;
}

.calc-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
}
.calc-table th {
    background: #F8FAFC;
    padding: 10px 14px;
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    color: #64748B;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border-bottom: 1px solid #E2E8F0;
}
.calc-table td {
    padding: 12px 14px;
    border-bottom: 1px solid #F1F5F9;
    color: #1E293B;
}
.calc-table tr:last-child td { border-bottom: none; }
.calc-table .num { text-align: right; font-family: 'DM Mono', monospace; }

/* ===================== KLANTEN PAGINA ===================== */

/* Klanten tabel wrapper */
.klanten-table-wrap {
    background: white;
    border: 1px solid #E2E8F0;
    border-radius: 16px;
    overflow: hidden;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}

/* Tabel header row */
.kt-header {
    display: grid;
    grid-template-columns: 2.4fr 1.2fr 1fr 1.2fr 1.4fr 0.6fr 1fr 0.9fr 0.9fr;
    padding: 10px 20px;
    background: #F8FAFC;
    border-bottom: 1px solid #E8EFF5;
}
.kt-header-cell {
    font-size: 10.5px;
    font-weight: 700;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: 0.07em;
}

/* Klant rij */
.kt-row {
    display: grid;
    grid-template-columns: 2.4fr 1.2fr 1fr 1.2fr 1.4fr 0.6fr 1fr 0.9fr 0.9fr;
    padding: 14px 20px;
    align-items: center;
    border-bottom: 1px solid #F1F5F9;
    transition: background 0.12s ease, box-shadow 0.12s ease;
}
.kt-row:last-child { border-bottom: none; }
.kt-row:hover { background: #F8FBFF; box-shadow: inset 3px 0 0 #2563EB; }

/* Avatar met initialen */
.klant-avatar {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 700;
    color: white;
    flex-shrink: 0;
}
.klant-naam-block {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
}
.klant-naam-text {
    min-width: 0;
}
.klant-naam {
    font-size: 13px;
    font-weight: 600;
    color: #0F172A;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.klant-bedrijf {
    font-size: 11px;
    color: #94A3B8;
    margin-top: 1px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.klant-adres {
    font-size: 11px;
    color: #94A3B8;
    margin-top: 1px;
}
.kt-cell {
    font-size: 12.5px;
    color: #374151;
}
.kt-cell.mono {
    font-family: 'DM Mono', monospace;
    font-size: 12px;
    color: #2563EB;
}
.kt-cell.email {
    font-size: 12px;
    color: #2563EB;
}
.kt-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 9px;
    border-radius: 99px;
    font-size: 11px;
    font-weight: 600;
}
.kt-badge.actief { background: #D1FAE5; color: #065F46; }
.kt-badge.inactief { background: #F3F4F6; color: #6B7280; }
.kt-badge.prospect { background: #FEF3C7; color: #92400E; }

/* Icon actie knoppen */
.kt-acties {
    display: flex;
    gap: 4px;
    align-items: center;
}
.kt-icon-btn {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: #F8FAFC;
    border: 1px solid #E2E8F0;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    font-size: 13px;
    transition: all 0.13s ease;
    text-decoration: none;
    color: #64748B;
    flex-shrink: 0;
}
.kt-icon-btn:hover { background: #EFF6FF; border-color: #BFDBFE; color: #2563EB; }
.kt-icon-btn.danger:hover { background: #FFF5F5; border-color: #FECACA; color: #EF4444; }

/* Zoekbalk */
.klant-zoek-wrap {
    background: white;
    border: 1px solid #E5E7EB;
    border-radius: 14px;
    padding: 14px 16px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    margin-bottom: 16px;
    display: flex;
    gap: 12px;
    align-items: center;
}

/* Paginatie */
.paginatie-wrap {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    border-top: 1px solid #F1F5F9;
}
.paginatie-info {
    font-size: 12px;
    color: #94A3B8;
}
.paginatie-btns {
    display: flex;
    gap: 4px;
    align-items: center;
}
.pag-btn {
    width: 32px;
    height: 32px;
    border-radius: 8px;
    border: 1px solid #E2E8F0;
    background: white;
    font-size: 12px;
    font-weight: 500;
    color: #374151;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.12s ease;
    font-family: 'DM Sans', sans-serif;
}
.pag-btn:hover { background: #F0F7FF; border-color: #BFDBFE; }
.pag-btn.active { background: #2563EB; border-color: #2563EB; color: white; }
.pag-btn.nav { width: auto; padding: 0 10px; font-size: 12px; gap: 4px; }
.pag-btn.nav:disabled, .pag-btn.nav[disabled] { opacity: 0.4; cursor: not-allowed; }

/* Nieuwe klant form cards */
.klant-form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr 1fr;
    gap: 16px;
    margin-bottom: 80px;
}
.klant-form-card {
    background: white;
    border: 1px solid #E2E8F0;
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.klant-form-card-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid #F1F5F9;
}
.klant-form-card-icon {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    flex-shrink: 0;
}
.klant-form-card-title {
    font-size: 13px;
    font-weight: 600;
    color: #0F172A;
}
.klant-form-card-subtitle {
    font-size: 11px;
    color: #94A3B8;
    margin-left: auto;
}

/* Sticky action bar */
.klant-action-bar {
    position: fixed;
    bottom: 0;
    left: 220px;
    right: 0;
    background: white;
    border-top: 1px solid #E2E8F0;
    padding: 14px 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    z-index: 100;
    box-shadow: 0 -4px 16px rgba(0,0,0,0.06);
}
.klant-save-btn {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    background: #081A36;
    color: white;
    border: none;
    border-radius: 10px;
    padding: 0 20px;
    height: 42px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.16s ease;
    font-family: 'DM Sans', sans-serif;
    box-shadow: 0 2px 8px rgba(8,26,54,0.25);
}
.klant-save-btn:hover {
    background: #041124;
    box-shadow: 0 4px 14px rgba(8,26,54,0.35);
    transform: translateY(-1px);
}
.klant-cancel-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: white;
    color: #374151;
    border: 1.5px solid #E2E8F0;
    border-radius: 10px;
    padding: 0 18px;
    height: 42px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.14s ease;
    font-family: 'DM Sans', sans-serif;
}
.klant-cancel-btn:hover {
    background: #F8FAFC;
    border-color: #CBD5E1;
}

/* Lege staat */
.empty-state {
    text-align: center;
    padding: 56px 24px;
    color: #94A3B8;
}
.empty-state-icon { font-size: 40px; margin-bottom: 12px; }
.empty-state-title { font-size: 15px; font-weight: 600; color: #374151; margin-bottom: 4px; }
.empty-state-sub { font-size: 13px; }

/* Acties icon knoppen in klanten tabel */
.acties-icon-wrap {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px 0 6px 0;
}
.acties-icon-btn {
    width: 30px;
    height: 30px;
    border-radius: 8px;
    background: #F8FAFC;
    border: 1px solid #E2E8F0;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    font-size: 13px;
    transition: all 0.13s ease;
    margin: 0 auto;
}
.acties-icon-btn:hover {
    background: #EFF6FF;
    border-color: #BFDBFE;
}

/* ===================== PREMIUM TABLE ===================== */
.premium-table-wrap {
    background: white;
    border: 1px solid #E2E8F0;
    border-radius: 14px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    margin-bottom: 8px;
}
.premium-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
.premium-table thead tr {
    background: #F8FAFC;
    border-bottom: 1px solid #E8EFF5;
    position: sticky;
    top: 0;
    z-index: 1;
}
.premium-table th {
    padding: 10px 14px;
    text-align: left;
    font-size: var(--text-xs);
    font-weight: 700;
    color: #64748B;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    white-space: nowrap;
}
.premium-table th.r { text-align: right; }
.premium-table tbody tr {
    border-bottom: 1px solid #F1F5F9;
    transition: background 0.12s ease, box-shadow 0.12s ease;
}
.premium-table tbody tr:last-child { border-bottom: none; }
.premium-table tbody tr:hover {
    background: #F0F7FF !important;
    box-shadow: inset 3px 0 0 #2563EB;
}
.premium-table tbody tr:nth-child(even) { background: #FAFBFC; }
.premium-table td {
    padding: 11px 14px;
    color: #1E293B;
    vertical-align: middle;
    font-size: var(--text-base);
}
.premium-table td.r {
    text-align: right;
    font-family: 'DM Mono', monospace;
    font-size: var(--text-sm);
    font-weight: 500;
    color: #0F172A;
}
.premium-table .td-name {
    font-weight: 600;
    color: #0F172A;
    font-size: var(--text-base);
}
.premium-table .td-chip {
    display: inline-block;
    background: #EFF6FF;
    color: #2563EB;
    border-radius: var(--r-sm);
    padding: 2px 7px;
    font-size: var(--text-xs);
    font-weight: 500;
    margin: 2px 2px 0 0;
}

.totaal-balk {
    background: linear-gradient(135deg, #081A36 0%, #041124 100%);
    color: white;
    padding: 14px 18px;
    border-radius: var(--r-lg);
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 6px;
    box-shadow: 0 4px 14px rgba(8,26,54,0.15);
}

/* ===================== OFFERTES CARD ROW ===================== */
/* Outer wrapper — border, radius en hover op hele kaart */
.of-card-row-full {
    background: white;
    border: 1px solid #E8EFF5;
    border-radius: var(--r-lg);
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    display: flex;
    align-items: stretch;
    margin-bottom: 6px;
    overflow: hidden;
    transition: border-color 0.15s ease, box-shadow 0.15s ease;
    text-decoration: none;
}
.of-card-row-full:hover {
    border-color: #BFDBFE;
    box-shadow: 0 4px 14px rgba(0,0,0,0.08);
}
/* Linker content-gebied */
.of-card-inner {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    padding: 14px 16px;
    gap: 14px;
    min-height: 74px;
}
/* Rechter PDF-download link */
.of-pdf-link {
    flex-shrink: 0;
    width: 72px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    background: #F8FAFC;
    border-left: 1px solid #E8EFF5;
    color: #64748B;
    font-size: 11px;
    font-weight: 600;
    text-decoration: none;
    transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
    font-family: 'DM Sans', sans-serif;
    letter-spacing: 0.02em;
    cursor: pointer;
}
.of-pdf-link:hover {
    background: #EFF6FF;
    color: #2563EB;
    border-left-color: #BFDBFE;
}

/* Verberg streamlit branding */
#MainMenu, footer, header { display: none !important; }

/* ── Zoekbalken: professioneel zoekicoon (Bootstrap bi-search SVG) ── */
div[data-testid="stTextInput"] input[placeholder*="Zoek"] {
    padding-left: 34px !important;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' fill='%2394A3B8' viewBox='0 0 16 16'%3E%3Cpath d='M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.099zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0z'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: 10px center !important;
}

/* ── Multiselect tags: blauw (app-breed) ── */
[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
    background-color: #EFF6FF !important;
    border: 1px solid #BFDBFE !important;
    border-radius: 6px !important;
}
[data-testid="stMultiSelect"] span[data-baseweb="tag"] span {
    color: #2563EB !important;
    font-weight: 600 !important;
}
[data-testid="stMultiSelect"] span[data-baseweb="tag"] [role="button"],
[data-testid="stMultiSelect"] span[data-baseweb="tag"] button {
    color: #93C5FD !important;
}
[data-testid="stMultiSelect"] span[data-baseweb="tag"] [role="button"]:hover,
[data-testid="stMultiSelect"] span[data-baseweb="tag"] button:hover {
    color: #2563EB !important;
    background: transparent !important;
}

/* ===================== GEDEELDE KOSTEN-BREAKDOWN ===================== */
/* Eén consistente kosten-breakdown (Calculaties + Project-details). Globaal zodat
   render_kosten_breakdown() overal identiek oogt, ongeacht welke pagina eerst laadt. */
.calc-breakdown-card {
    background: white;
    border: 1px solid #E2E8F0;
    border-radius: 16px;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    margin-top: 28px;
}
.calc-bd-header {
    display: flex;
    padding: 10px 20px;
    background: #F8FAFC;
    border-bottom: 1px solid #E8EFF5;
}
.calc-bd-th {
    font-size: 10.5px;
    font-weight: 700;
    color: #94A3B8;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.calc-bd-row {
    display: flex;
    align-items: center;
    padding: 13px 20px;
    border-bottom: 1px solid #F1F5F9;
    transition: background 0.1s ease;
}
.calc-bd-row:last-child { border-bottom: none; }
.calc-bd-row:hover { background: #F8FBFF; }
.calc-bd-row.even { background: #FAFBFC; }
.calc-bd-row.even:hover { background: #F0F7FF; }
.calc-bd-icon {
    width: 28px; height: 28px; border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; margin-right: 10px; flex-shrink: 0;
}
.calc-bd-label { font-size: 13px; font-weight: 500; color: #1E293B; }
.calc-bd-amount {
    font-size: 13px; font-weight: 600; color: #0F172A;
    font-family: 'DM Mono', monospace; text-align: right;
    padding-right: 8px;
}
.calc-bd-pct { font-size: 12px; color: #64748B; text-align: right; }
.calc-bd-bar-wrap {
    height: 5px; background: #F1F5F9; border-radius: 99px; overflow: hidden; margin-top: 3px;
}
.calc-bd-bar { height: 5px; background: #2563EB; border-radius: 99px; }

/* ===================== SIDEBAR ⋮-UITLOGMENU ===================== */
/* Positionering + styling van het ⋮-profielmenu. Globaal en synchroon (i.p.v. late
   iframe-CSS) én op de popover-WRAPPER zelf via :has() — niet via marker-adjacency
   (`marker + wrapper`). Die adjacency kon bij een rerun kort falen (Streamlit houdt
   stale-elementen zichtbaar, de volgorde wisselt), waardoor de ⋮-knop ~1s onpositioned
   in de flow bóven de pfp verscheen. Met :has([stPopover]) krijgt elke popover-wrapper
   (ook een stale duplicaat) meteen de vaste positie → geen flits. Matcht alleen wanneer
   de popover bestaat (ingelogd + uitgeklapte sidebar). */
[data-testid="stSidebar"] [data-testid="stLayoutWrapper"]:has([data-testid="stPopover"]) {
    position: fixed !important; bottom: 19px !important; left: 180px !important;
    width: 30px !important; min-width: 30px !important; z-index: 1001 !important;
}
[data-testid="stSidebar"] [data-testid="stPopover"] button {
    background: transparent !important; border: none !important; box-shadow: none !important;
    color: rgba(255,255,255,0.55) !important; padding: 0 !important; min-height: 0 !important;
    height: 28px !important; width: 30px !important; line-height: 1 !important; font-size: 20px !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
}
[data-testid="stSidebar"] [data-testid="stPopover"] button:hover {
    color: #fff !important; background: rgba(255,255,255,0.12) !important; border-radius: 7px !important;
}
[data-testid="stSidebar"] [data-testid="stPopover"] button [data-testid="stIconMaterial"] { display: none !important; }

/* ===================== RESPONSIVE — MOBIELE LAAG (FASE 0-4) ===================== */
/* Eén geconsolideerd @media-blok (geen dubbele media-queries). ÁLLE regels staan binnen
   @media (max-width:767px), dus desktop (≥1200), laptop (992-1199) en tablet (768-991)
   blijven exact ongewijzigd — geen enkele desktopregel wordt aangeraakt. De mobiele regels
   gelden voor ≤767px (zowel mobiel 480-767 als kleine telefoon <480). Per onderdeel onder
   de bijbehorende fase-subkop. */
@media (max-width: 767px) {
    /* ── F0 FUNDAMENT — st.columns-rijen stapelen verticaal (m.u.v. de agenda-kalender,
       een 7-koloms grid die grid MOET blijven; herkenbaar aan cel-markers cal-m-*/cal-day-other).
       Scoped op stMain. Absoluut gepositioneerde tabel-overlayknoppen houden hun eigen positie. */
    section[data-testid="stMain"] [data-testid="stHorizontalBlock"]:not(:has([class^="cal-m-"], .cal-day-other)) {
        flex-wrap: wrap !important;
    }
    section[data-testid="stMain"] [data-testid="stHorizontalBlock"]:not(:has([class^="cal-m-"], .cal-day-other)) > [data-testid="stColumn"] {
        flex: 1 1 100% !important; width: 100% !important; min-width: 0 !important;
    }

    /* ── F2 BASISPAGINA'S — handgebouwde HTML-kaartrijen (Offertes/Facturen + Dashboard)
       netjes stapelen i.p.v. samengeperst. Pagina-eigen klassen → rest onaangeraakt. */
    .of-card-inner { flex-direction: column !important; align-items: flex-start !important; gap: 8px !important; }
    .of-card-inner > div { width: 100% !important; flex: none !important; }
    .db-proj-row { flex-wrap: wrap !important; row-gap: 6px !important; }
    .db-proj-name { white-space: normal !important; }

    /* ── F3 FORMULIEREN — comfortabel tikoppervlak (44px) voor invoervelden, dropdowns en
       submit-knoppen (op mobiel gestapeld → geen uitlijningsrisico) + bredere calculatie-velden. */
    section[data-testid="stMain"] [data-baseweb="input"],
    section[data-testid="stMain"] [data-baseweb="select"] { min-height: 44px !important; }
    section[data-testid="stMain"] [data-testid="stFormSubmitButton"] > button { min-height: 44px !important; }
    section[data-testid="stMain"] div[data-testid="stVerticalBlock"]:has(span.calc-params-marker) {
        padding-left: 14px !important; padding-right: 14px !important;
    }

    /* ── F4 TABELLEN/LIJSTEN — brede HTML-tabelrijen (Klanten/Producten/Personeel) worden
       een verticale kaart; alles leesbaar, geen afkapping. Kolomheader verborgen. Actie-iconen
       blijven laatste kind rechtsonder, waar de overlay-knoppen (onderste 62px) overheen liggen
       → klikvlakken blijven uitgelijnd. + Project Details info-grid → 1 kolom. + Admin-header weg. */
    .cf-tbl-head { display: none !important; }
    .cf-tbl-row {
        flex-direction: column !important; align-items: stretch !important;
        gap: 3px !important; padding: 13px 14px 16px 14px !important;
    }
    .cf-tbl-row > div {
        flex: 0 0 auto !important; width: 100% !important;
        white-space: normal !important; overflow: visible !important;
        text-overflow: clip !important; padding-right: 0 !important;
    }
    .cf-tbl-row > div:last-child { justify-content: flex-end !important; margin-top: 2px !important; }
    .pd-info-grid { grid-template-columns: 1fr !important; gap: 12px 0 !important; }
    section[data-testid="stMain"] [data-testid="stHorizontalBlock"]:has(.adm-th) { display: none !important; }

    /* ── F5 DASHBOARD OVERZICHT — de stapeling van "Omzet deze maand" + "Project status" gebeurt
       breedte-onafhankelijk via flex-wrap + min-width op de grafiekkolom (zie ov_html): zodra de
       kaart te smal wordt breekt de status-kolom af onder de grafiek i.p.v. die tot 0px te persen.
       Hier alleen de mobiele afwerking: gestapelde status krijgt een rand bóven i.p.v. links. */
    .db-ovz-status { border-left: none !important; padding-left: 0 !important; border-top: 1px solid #F1F5F9 !important; padding-top: 16px !important; }
}
"""

# ── CSS-injectie via iframe-JavaScript ─────────────────────────────────────
# st.markdown() <style>-tags worden in nieuwere Streamlit-versies gestript;
# components.html() omzeilt dit door CSS via JS in het parent-document te zetten.
_BI_CDN = "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css"
_css_json = json.dumps(_APP_CSS)
# PERF: de basis-CSS (~33 KB) verandert NIET tussen reruns. 'm elke rerun opnieuw via
# een component-iframe injecteren = onnodig 33 KB over de websocket + een volledige
# style-recalc van de pagina bij ELKE klik (client-side de grootste kostenpost, ~2-3s
# op Streamlit Cloud). De <style id="sp-css"> die dit script in de document-head zet
# BLIJFT staan over reruns heen, dus één keer per sessie injecteren volstaat. Bij een
# echte page-load (F5) reset session_state → wordt 'ie opnieuw gezet.
if not st.session_state.get("_css_base_done"):
    _components.html(
    "<script>(function(){"
    "var p=window.parent.document;"
    "var s=p.getElementById('sp-css');"
    "if(!s){s=p.createElement('style');s.id='sp-css';p.head.appendChild(s);}"
    "s.textContent=" + _css_json + ";"
    "if(!p.getElementById('bi-cdn')){"
    "var l=p.createElement('link');"
    "l.id='bi-cdn';l.rel='stylesheet';"
    "l.href='" + _BI_CDN + "';"
    "p.head.appendChild(l);}"
    # Onderdruk de gele browser-validatieballon ("Waarde moet groter dan of gelijk
    # zijn aan 0.") die Streamlit's number_input via reportValidity() oproept zodra je
    # een waarde buiten min/max invoert of het veld leegmaakt. Streamlit weigert/clamp't
    # de waarde zelf nog steeds; alleen de native popup wordt verborgen. Eén keer koppelen.
    "if(!p.__cfNoValidate){p.__cfNoValidate=true;"
    "p.addEventListener('invalid',function(e){e.preventDefault();},true);}"
    "})();</script>",
    height=0, scrolling=False
    )
    st.session_state["_css_base_done"] = True


def _inject_page_css(css):
    """Injecteer pagina-specifieke CSS via iframe-JS.
    Omzeilt het probleem dat <style>-tags in st.markdown() worden gestript
    in nieuwere Streamlit-versies."""
    css_id = "sp-pg-" + hashlib.md5(css.encode()).hexdigest()[:10]
    # PERF: identieke pagina-CSS niet elke rerun opnieuw injecteren (de <style> blijft
    # in de head staan). Per sessie per unieke CSS één keer volstaat.
    _done = st.session_state.setdefault("_css_pg_done", set())
    if css_id in _done:
        return
    _done.add(css_id)
    _components.html(
        "<script>(function(){"
        "var p=window.parent.document;"
        "var s=p.getElementById('" + css_id + "');"
        "if(!s){s=p.createElement('style');s.id='" + css_id + "';p.head.appendChild(s);}"
        "s.textContent=" + json.dumps(css) + ";"
        "})();</script>",
        height=0, scrolling=False
    )


def _inject_keyed_css(key, css):
    """Zoals _inject_page_css maar met een vaste key in plaats van content-hash.
    PERF: alleen (her)injecteren als de inhoud voor deze key WIJZIGDE t.o.v. de vorige
    render. Anders deed elke rerun een component-iframe-round-trip (merkbaar traag op
    Streamlit Cloud) — o.a. de globale 'compact_view' bij élke klik en de overlay-CSS
    op elke datapagina zodra er rijen zijn. De <style id="sp-k-..."> blijft in de
    document-head staan over reruns heen, dus bij gelijke inhoud is opnieuw injecteren
    onnodig. Bij een echte page-load (F5) reset session_state → wordt 'ie opnieuw gezet.
    (De oude 'elke render overschrijven' was tegen stale-CSS bij dev hot-reloads; die
    zijn er in productie niet, en bij gewijzigde inhoud injecteren we alsnog opnieuw.)"""
    css_id = "sp-k-" + key
    _seen = st.session_state.setdefault("_css_keyed_seen", {})
    if _seen.get(key) == css:
        return   # inhoud onveranderd → de bestaande <style> in de head volstaat
    _seen[key] = css
    _components.html(
        "<script>(function(){"
        "var p=window.parent.document;"
        "var s=p.getElementById('" + css_id + "');"
        "if(!s){s=p.createElement('style');s.id='" + css_id + "';p.head.appendChild(s);}"
        "s.textContent=" + json.dumps(css) + ";"
        "})();</script>",
        height=0, scrolling=False
    )


# SP-012: compacte weergave (Instellingen → Voorkeuren) — verkleint card-padding.
# Lege string bij uitgeschakeld zodat de keyed style-tag weer leeg wordt.
_inject_keyed_css("compact_view",
    """
    .db-stat-card, .db-section, .pd-card { padding: 12px 14px !important; }
    .db-proj-row { padding: 6px 8px !important; }
    .of-stat { padding: 12px 14px 11px !important; }
    """ if st.session_state.instellingen.get("compacte_weergave") else "")


# =====================================================
# SIDEBAR NAVIGATIE
# =====================================================

with st.sidebar:
    # FIX 1: semi-inklapbare sidebar. Open = exact zoals nu (220px). Ingeklapt = smalle
    # icoon-rail (72px) die ZICHTBAAR blijft (nooit volledig weg). Status in session_state,
    # dus blijft behouden bij paginawissel/rerun.
    st.session_state.setdefault("sb_collapsed", False)
    _collapsed = st.session_state["sb_collapsed"]
    _sb_w = 72 if _collapsed else 220

    # Sidebar-CSS via st.html (geen iframe → direct in document, geen FOUC)
    st.html("""<style>
    [data-testid="stSidebar"]{
        width:220px !important;min-width:220px !important;max-width:220px !important;
        background:#081A36 !important;
        border-right:1px solid rgba(255,255,255,0.06) !important;
    }
    [data-testid="stSidebar"]>div:first-child{width:220px !important;overflow:hidden !important;}
    [data-testid="stSidebarContent"]{padding:0 12px !important;}
    [data-testid="stSidebarCollapsedControl"]{background:#081A36 !important;border-right:2px solid #2563EB !important;}
    [data-testid="stSidebar"] [data-testid="stHtml"]{display:none !important;height:0 !important;margin:0 !important;padding:0 !important;}
    /* Logo/titel klikbaar → Dashboard: de bijbehorende verborgen knop niet tonen (JS koppelt de klik). */
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.nav-home-mk),
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.nav-home-mk)+[data-testid="stElementContainer"]{display:none !important;height:0 !important;margin:0 !important;}
    /* Bovenruimte pagina's subtiel verkleinen (Streamlit-header is verborgen → 6rem is pure lege ruimte). */
    [data-testid="stMainBlockContainer"], .block-container{padding-top:3rem !important;}
    /* FIX 4: agenda-card chrome SYNCHROON (st.html → direct in document, geen iframe-FOUC),
       zodat de afgeronde hoeken er vanaf de eerste render staan — geen flicker. Zelfde
       radius (14px) als .db-stat-card / .db-section. De gedetailleerde agenda-styling blijft
       in het async-blok; deze regels zetten alleen de card-omlijsting vooraf goed. */
    div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker){
        background:white !important;border-radius:14px !important;}
    div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)>div[data-testid="stVerticalBlock"]{
        border-color:transparent !important;border-radius:14px !important;}
    /* GEEN 'witte waas' meer tijdens reruns: Streamlit dimt herberekende elementen via
       [data-stale="true"] (de waas die de gebruiker ziet bij elke knop-actie). De oude
       inhoud op volle dekking laten staan tot de nieuwe klaar is → rustige, snelle indruk. */
    [data-testid="stElementContainer"][data-stale="true"],
    [data-testid="stVerticalBlock"][data-stale="true"],
    [data-testid="stHorizontalBlock"][data-stale="true"]{ opacity:1 !important; }
    </style>""")

    # Inklap-overrides: breedte + soepele animatie + toggle-knop. Komt ná de basis-CSS
    # (zelfde document) → wint in de cascade. st.html = synchroon, dus geen FOUC/sprong.
    st.html(f"""<style>
    [data-testid="stSidebar"], [data-testid="stSidebar"]>div:first-child {{
        width:{_sb_w}px !important; min-width:{_sb_w}px !important; max-width:{_sb_w}px !important;
        transition: width .22s ease, min-width .22s ease, max-width .22s ease !important;
    }}
    /* toggle-knop: subtiel, rechtsboven in de sidebar; absoluut → geen layout-verschuiving */
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.sb-toggle-mk){{display:none !important;}}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.sb-toggle-mk) + [data-testid="stElementContainer"]{{
        position:absolute !important; top:12px; right:8px; width:auto !important; margin:0 !important; z-index:1003;
    }}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.sb-toggle-mk) + [data-testid="stElementContainer"] button{{
        background:rgba(255,255,255,0.10) !important; color:rgba(255,255,255,0.85) !important; border:none !important;
        border-radius:8px !important; width:26px !important; min-width:26px !important; height:26px !important; min-height:26px !important;
        padding:0 !important; font-size:11px !important; line-height:1 !important; box-shadow:none !important;
        display:flex !important; align-items:center !important; justify-content:center !important;
    }}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.sb-toggle-mk) + [data-testid="stElementContainer"] button:hover{{
        background:rgba(255,255,255,0.20) !important; color:#fff !important;
    }}
    /* ingeklapte icoon-rail: knop-box (zelfde hoogte/spacing als de open nav-items; het
       glyph zelf komt uit de bootstrap-icons ::before-regels hieronder) */
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-mk){{display:none !important;}}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-mk) + [data-testid="stElementContainer"]{{display:flex !important; justify-content:center !important;}}
    /* krappe gap tussen de icoon-knoppen → zelfde verticale ritme als de open nav (~39px h-o-h);
       margin-top lijnt de eerste icoon uit op dezelfde hoogte als het eerste open nav-item */
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] span.navrail-mk){{gap:3px !important; margin-top:11px !important;}}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-mk) + [data-testid="stElementContainer"] button{{
        background:transparent !important; border:none !important; box-shadow:none !important;
        width:42px !important; min-width:42px !important; height:36px !important; min-height:36px !important;
        margin:0 !important; padding:0 !important; border-radius:10px !important; line-height:1 !important;
        display:flex !important; align-items:center !important; justify-content:center !important;
    }}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-mk) + [data-testid="stElementContainer"] button:hover{{
        background:rgba(255,255,255,0.10) !important;
    }}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-active-mk) + [data-testid="stElementContainer"] button{{
        background:#ffffff !important;
    }}
    </style>""")

    # Bootstrap-icons (DEZELFDE iconen als de open nav, via ::before). Raw string → de
    # \\f-codepoints blijven letterlijk; CDN-@font-face wordt door de browser gecachet.
    st.html(r"""<style>
    @font-face{font-family:'bootstrap-icons';font-display:block;
        src:url('https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2') format('woff2');}
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-mk) + [data-testid="stElementContainer"] button{ font-size:0 !important; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-mk) + [data-testid="stElementContainer"] button::before{
        font-family:'bootstrap-icons' !important; font-weight:normal !important; font-style:normal !important;
        font-size:18px !important; line-height:1 !important; color:rgba(255,255,255,0.80) !important;
        -webkit-font-smoothing:antialiased; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-active-mk) + [data-testid="stElementContainer"] button::before{ color:#081A36 !important; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-house-mk)  + [data-testid="stElementContainer"] button::before{ content:"\f424"; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-folder-mk) + [data-testid="stElementContainer"] button::before{ content:"\f3d8"; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-file-mk)   + [data-testid="stElementContainer"] button::before{ content:"\f38a"; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-calc-mk)   + [data-testid="stElementContainer"] button::before{ content:"\f1df"; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-people-mk) + [data-testid="stElementContainer"] button::before{ content:"\f4cf"; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-box-mk)    + [data-testid="stElementContainer"] button::before{ content:"\f7d3"; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-person-mk) + [data-testid="stElementContainer"] button::before{ content:"\f4d2"; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-gear-mk)   + [data-testid="stElementContainer"] button::before{ content:"\f3e2"; }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.navico-admin-mk)  + [data-testid="stElementContainer"] button::before{ content:"\f537"; }

    /* ── Generieke knop-iconen (bootstrap-icons via ::before). Marker vóór de knop; de
       marker-container wordt ingeklapt (geen layout-verschuiving). Icoonkleur erft de
       tekstkleur van de knop. ── */
    [data-testid="stElementContainer"]:has(span.cf-ico-mk){ display:none !important; }
    [data-testid="stElementContainer"]:has(span.cf-ico-mk) + [data-testid="stElementContainer"] button::before,
    [data-testid="stElementContainer"]:has(span.cf-ico-mk) + [data-testid="stElementContainer"] [data-testid="stDownloadButton"] button::before{
        font-family:'bootstrap-icons' !important; margin-right:7px; font-size:14px; vertical-align:-0.1em;
        font-style:normal !important; font-weight:400 !important; line-height:1; }
    [data-testid="stElementContainer"]:has(span.cf-ico-save-mk) + [data-testid="stElementContainer"] button::before{ content:"\f7d7"; }
    /* De primaire submit-wrapper (Opslaan) is ~6px hoger dan de secundaire (Annuleren),
       maar die extra ruimte zit ONDER de knop. Beide knoppen staan van nature bovenaan
       hun wrapper → géén margin op de primaire, anders zakt Opslaan onder Annuleren. */
    [data-testid="stElementContainer"]:has(span.cf-ico-save-mk) + [data-testid="stElementContainer"] [data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]{ margin-top:0 !important; align-self:flex-start !important; }
    /* het hele Opslaan/Annuleren-rijtje iets hoger plaatsen */
    [data-testid="stHorizontalBlock"]:has(span.cf-ico-save-mk){ margin-top:-7px !important; }
    [data-testid="stElementContainer"]:has(span.cf-ico-export-mk) + [data-testid="stElementContainer"] [data-testid="stDownloadButton"] button::before{ content:"\f1c6"; }
    [data-testid="stElementContainer"]:has(span.cf-ico-export-mk) + [data-testid="stElementContainer"] [data-testid="stDownloadButton"] button{ white-space:nowrap !important; min-width:0 !important; }
    [data-testid="stElementContainer"]:has(span.cf-ico-export-mk) + [data-testid="stElementContainer"] [data-testid="stDownloadButton"] button p{ white-space:nowrap !important; }
    </style>""")
    st.markdown('<span class="sb-toggle-mk" style="display:none;"></span>', unsafe_allow_html=True)

    def _toggle_sb():
        st.session_state["sb_collapsed"] = not st.session_state.get("sb_collapsed", False)
    st.button("▶" if _collapsed else "◀", key="sb_toggle", on_click=_toggle_sb,
              help="Sidebar in- of uitklappen")

    inst_naam = st.session_state.instellingen.get("bedrijfsnaam", "CoatFlow")

    # FIX 1: exact hetzelfde logo als op de inlogpagina (auth.py) — de blauwe "C".
    # Zelfde SVG-bron, kleur (#2563EB), verhoudingen en rendering (background-image, contain).
    import urllib.parse as _urlparse
    _cf_logo_uri = "data:image/svg+xml," + _urlparse.quote(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48' fill='none'>"
        "<path d='M35 14.5 A15.5 15.5 0 1 0 35 33.5' stroke='#2563EB' stroke-width='8.5' "
        "stroke-linecap='round'/></svg>", safe="")

    # Logo header (geen -4rem nodig: stSidebarContent padding is 0 via st.html CSS boven)
    # Logo + titel zijn klikbaar → Dashboard (klikgebied id="cf-home-link", via JS gekoppeld
    # aan de verborgen 'Naar dashboard'-knop hieronder).
    if _collapsed:
        # Ingeklapt: alleen de "C" (gecentreerd, onder de toggle-knop). Blijft klikbaar → dashboard.
        st.markdown(f"""
            <div style="background:linear-gradient(180deg,#041124,#081A36);
                        margin:0 -12px 0 -12px;padding:46px 8px 16px 8px;
                        border-bottom:1px solid rgba(255,255,255,0.07);">
                <div id="cf-home-link" title="Naar dashboard" style="display:flex;justify-content:center;cursor:pointer;">
                    <div style="width:34px;height:34px;flex-shrink:0;
                                background:url('{_cf_logo_uri}') center/contain no-repeat;"></div>
                </div>
            </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
            <div style="background:linear-gradient(180deg,#041124,#081A36);
                        margin:0 -12px 0 -12px;padding:32px 16px 18px 16px;
                        border-bottom:1px solid rgba(255,255,255,0.07);">
                <div id="cf-home-link" title="Naar dashboard" style="display:flex;align-items:center;gap:12px;cursor:pointer;">
                    <div style="width:42px;height:42px;flex-shrink:0;
                                background:url('{_cf_logo_uri}') center/contain no-repeat;"></div>
                    <div>
                        <div style="font-size:18px;font-weight:800;color:white;letter-spacing:-0.4px;line-height:1.2;">CoatFlow</div>
                        <div style="font-size:11px;color:rgba(255,255,255,0.45);font-weight:400;margin-top:2px;">Calculatie & Offertes</div>
                    </div>
                </div>
            </div>
        """, unsafe_allow_html=True)

    # Verborgen navigatie-knop (via CSS verborgen, via JS aan de logo-klik gekoppeld) → Dashboard
    st.markdown('<span class="nav-home-mk" style="display:none;"></span>', unsafe_allow_html=True)
    if st.button("Naar dashboard", key="nav_home_btn", use_container_width=True):
        st.session_state["nav_doel"] = "Dashboard"
        st.rerun()
    _components.html("""<script>(function(){
var SPAN_ID='cf-home-link';
var BTN_TXT='Naar dashboard';
function wire(){
    var p=window.parent.document;
    var span=p.getElementById(SPAN_ID);
    var btn=null;
    var all=p.querySelectorAll('button');
    for(var i=0;i<all.length;i++){ if(all[i].textContent.trim()===BTN_TXT){btn=all[i];break;} }
    if(span&&btn){ span.onclick=function(){btn.click();}; }
}
wire();
var obs=new MutationObserver(function(){ clearTimeout(window._cfHomeT); window._cfHomeT=setTimeout(wire,30); });
obs.observe(window.parent.document.body,{childList:true,subtree:true});
})();</script>""", height=0, scrolling=False)

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    # Platform-admin? (server-side, gecachet) → bepaalt of de Admin-pagina in de nav komt.
    # De pagina zelf heeft een eigen server-side guard, dus dit is enkel het tonen/verbergen.
    _is_admin = bool(_AUTH_OK and _auth.is_platform_admin())
    _nav_options = ["Dashboard", "Projecten", "Offertes", "Calculaties", "Klanten", "Producten", "Personeel", "Instellingen"]
    if _is_admin:
        _nav_options.append("Admin")
    # SP-012: startpagina volgt Instellingen → Voorkeuren; interne navigatie (nav_doel) gaat voor.
    # _active_page (huidige pagina) als terugval: nodig bij het in-/uitklappen (key wijzigt →
    # option_menu herinitialiseert) zodat de selectie NIET terugspringt naar de startpagina.
    _nav_start = (st.session_state.get("nav_doel")
                  or st.session_state.get("_active_page")
                  or st.session_state.instellingen.get("startpagina", "Dashboard"))
    _nav_default = _nav_options.index(_nav_start) if _nav_start in _nav_options else 0
    # Programmatische navigatie (logo-klik, 'Bekijk alles', enz.) zet nav_doel. Bump dan een
    # nonce zodat de option_menu volledig herinitialiseert met de nieuwe default_index — anders
    # springt wél de pagina mee, maar blijft de sidebar-markering op het vorige item staan.
    if "nav_doel" in st.session_state:
        st.session_state["_nav_nonce"] = st.session_state.get("_nav_nonce", 0) + 1
        del st.session_state["nav_doel"]

    if _collapsed:
        # Ingeklapt: eigen icoon-rail met DEZELFDE Bootstrap-iconen als uitgeklapt (via de
        # bootstrap-icons ::before-regels hierboven). Géén option_menu omdat dat in een iframe
        # draait waarvan de breedte content-gedreven (~27px) is → iconen werden half afgekapt.
        # Eigen knoppen = volledig zichtbaar, gecentreerd, en exact dezelfde glyphs als open.
        selected = _nav_start
        _nav_ico = {"Dashboard": "house", "Projecten": "folder", "Offertes": "file",
                    "Calculaties": "calc", "Klanten": "people", "Producten": "box",
                    "Personeel": "person", "Instellingen": "gear", "Admin": "admin"}

        def _set_nav(_pg):
            st.session_state["nav_doel"] = _pg   # on_click → vóór de rerun; auto-rerun navigeert

        # In een eigen container met krappe gap → zelfde verticale ritme/spacing als de
        # open nav (anders zit de standaard element-gap van Streamlit ertussen → te ver uit
        # elkaar). De navrail-mk markeert dit blok zodat de CSS de gap kan verkleinen.
        with st.container():
            st.markdown('<span class="navrail-mk" style="display:none;"></span>', unsafe_allow_html=True)
            for _pg in _nav_options:
                _act = " navico-active-mk" if _pg == selected else ""
                st.markdown(f'<span class="navico-mk navico-{_nav_ico[_pg]}-mk{_act}" style="display:none;"></span>',
                            unsafe_allow_html=True)
                st.button(" ", key=f"navico_{_pg}", on_click=_set_nav, args=(_pg,), help=_pg)
    else:
        # Open: icoon + tekst via option_menu.
        _nav_styles = {
            "container": {"padding": "0", "background-color": "#081A36", "background": "#081A36", "border-radius": "0"},
            "icon": {"font-size": "13px"},
            "nav-link": {
                "font-size": "13px",
                "font-weight": "500",
                "color": "rgba(255,255,255,0.75)",
                "background": "transparent",
                "background-color": "transparent",
                "border-radius": "10px",
                "margin": "2px 0",
                "padding": "8px 12px",
            },
            "nav-link-selected": {
                "background": "white",
                "background-color": "white",
                "color": "#081A36",
                "font-weight": "600",
                "border-radius": "10px",
            },
        }
        _nav_icons = ["house-fill", "folder2-open", "file-earmark-text-fill", "calculator-fill",
                      "people-fill", "box-seam-fill", "person-badge-fill", "gear-fill"]
        if "Admin" in _nav_options:
            _nav_icons.append("shield-lock-fill")
        selected = option_menu(
            menu_title=None,
            options=_nav_options,
            icons=_nav_icons,
            default_index=_nav_default,
            key=f"main_nav_{st.session_state.get('_nav_nonce', 0)}",
            styles=_nav_styles,
        )

    # FIX 3: bij iedere paginawissel bovenaan de nieuwe pagina starten. Alleen scrollen
    # wanneer de actieve pagina écht verandert (sidebar-nav of programmatische nav via
    # nav_doel) — niet bij in-pagina reruns, zodat scrollen bínnen een pagina blijft werken.
    if st.session_state.get("_active_page") != selected:
        st.session_state["_active_page"] = selected
        # Sluit open inline bewerk-/verwijder-/bekijk-secties (Klanten/Producten/Personeel)
        # bij een paginawissel — anders blijven ze open zodra je terugkeert naar de pagina.
        for _inline_k in ("kl_edit_id", "kl_del_id", "kl_view_id",
                          "pr_edit_id", "pr_del_id",
                          "ps_edit_id", "ps_del_id"):
            if _inline_k in st.session_state:
                st.session_state[_inline_k] = None
        # Nonce in de scripttekst → unieke component-inhoud per wissel, anders hergebruikt
        # Streamlit het iframe en draait het script niet opnieuw (scroll bleef dan staan).
        _scroll_n = st.session_state.get("_scroll_nonce", 0) + 1
        st.session_state["_scroll_nonce"] = _scroll_n
        _components.html(
            "<script>/* scroll-reset " + str(_scroll_n) + " */(function(){\n"
            "function toTop(){try{\n"
            "  var p=window.parent.document;\n"
            "  var sels=[\"section[data-testid='stMain']\",\"[data-testid='stMain']\",\"[data-testid='stAppViewContainer']\",\".main\"];\n"
            "  for(var i=0;i<sels.length;i++){var e=p.querySelector(sels[i]); if(e){e.scrollTop=0; if(e.scrollTo){e.scrollTo(0,0);}}}\n"
            "  if(p.scrollingElement){p.scrollingElement.scrollTop=0;}\n"
            "  p.documentElement.scrollTop=0; if(p.body){p.body.scrollTop=0;}\n"
            "  window.parent.scrollTo(0,0);\n"
            "}catch(err){}}\n"
            "toTop(); setTimeout(toTop,60); setTimeout(toTop,180); setTimeout(toTop,360);\n"
            "})();</script>", height=0, scrolling=False)

    # ── Profielvoet + uitlog-menu (Fase 2) ──
    bedrijf = st.session_state.instellingen.get("bedrijfsnaam", "Mijn Bedrijf")
    contact = st.session_state.instellingen.get("contactpersoon", "")
    # Fase 2: toon het ingelogde e-mailadres i.p.v. de demo-contactpersoon.
    if st.session_state.get("authenticated") and st.session_state.get("user_email"):
        contact = st.session_state["user_email"]
    if contact:
        delen = contact.replace("@", " ").split()
        initialen = (delen[0][0] + (delen[-1][0] if len(delen) > 1 else "")).upper()
    else:
        initialen = "JD"
        contact = "Jan de Vries"

    if _collapsed:
        # Ingeklapt: alleen de avatar (gecentreerd), naam/bedrijf verborgen. Breedte = rail.
        st.markdown(f"""
            <div style="position:fixed;bottom:0;left:0;width:72px;
                        background:#041124;border-top:1px solid rgba(255,255,255,0.07);
                        padding:12px 0;display:flex;align-items:center;justify-content:center;z-index:100;">
                <div style="width:34px;height:34px;border-radius:99px;background:linear-gradient(135deg,#2563EB,#4F46E5);
                            display:flex;align-items:center;justify-content:center;
                            font-size:12px;font-weight:700;color:white;flex-shrink:0;">{initialen}</div>
            </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f"""
            <div style="position:fixed;bottom:0;left:0;width:220px;
                        background:#041124;border-top:1px solid rgba(255,255,255,0.07);
                        padding:12px 16px;display:flex;align-items:center;gap:10px;z-index:100;">
                <div style="width:34px;height:34px;border-radius:99px;background:linear-gradient(135deg,#2563EB,#4F46E5);
                            display:flex;align-items:center;justify-content:center;
                            font-size:12px;font-weight:700;color:white;flex-shrink:0;">{initialen}</div>
                <div style="min-width:0;flex:1;padding-right:22px;">
                    <div style="font-size:12px;font-weight:600;color:white;white-space:nowrap;
                                overflow:hidden;text-overflow:ellipsis;">{contact}</div>
                    <div style="font-size:10px;color:rgba(255,255,255,0.4);white-space:nowrap;
                                overflow:hidden;text-overflow:ellipsis;">{bedrijf}</div>
                </div>
            </div>
        """, unsafe_allow_html=True)

    # Uitlog-menu: drie verticale puntjes rechts in de profielvoet → dropdown met
    # 'Uitloggen' (alleen wanneer login actief is). Ingeklapt overgeslagen (de ⋮-positie
    # valt buiten de smalle rail) → uitloggen na uitklappen; navigatie blijft intact.
    if _AUTH_OK and _auth.is_active() and st.session_state.get("authenticated") and not _collapsed:
        # Positionering + styling van dit ⋮-menu staat globaal in _APP_CSS (robuuste
        # :has([stPopover])-selector op de wrapper i.p.v. marker-adjacency), zodat de
        # knop bij reruns niet kort onpositioned in de flow bóven de pfp flitst.
        with st.popover("⋮", use_container_width=False):
            st.markdown(
                f'<div style="font-size:11px;color:#94A3B8;font-weight:600;padding:2px 2px 9px;'
                f'white-space:nowrap;">{h(contact)}</div>',
                unsafe_allow_html=True)
            if st.button("⎋  Uitloggen", key="cf_logout", use_container_width=True):
                _auth.sign_out()
                st.rerun()

# ── FASE 1: RESPONSIVE SIDEBAR — mobiele off-canvas overlay ───────────────────
# Additief en UITSLUITEND mobiel (@media max-width:767px): de sidebar wordt een
# off-canvas overlay (hamburger linksboven + verduisterde backdrop) i.p.v. een vaste
# 220px-kolom die de content overlapt. Desktop/tablet (≥768px) raken deze regels NIET
# (media-query) → exact dezelfde breedte/kleuren/spacing/animaties/semi-inklap. Synchrone
# geïnjecteerd via _inject_page_css (iframe-JS → echte <style> in het hoofddocument; st.html
# strip't <style> in deze Streamlit-versie) en draait ná de sidebar-CSS → wint in de cascade.
_inject_page_css("""
.cf-sb-hamburger{ display:none; position:fixed; top:10px; left:10px; z-index:2001;
    width:40px; height:40px; padding:0; border:none; border-radius:10px; background:#081A36;
    cursor:pointer; flex-direction:column; align-items:center; justify-content:center; gap:4px;
    box-shadow:0 2px 10px rgba(8,26,54,.28); }
.cf-sb-hamburger span{ display:block; width:18px; height:2px; background:#fff; border-radius:2px; }
#cf-sb-backdrop{ display:none; position:fixed; inset:0; background:rgba(8,26,54,.45);
    z-index:1999; opacity:0; pointer-events:none; transition:opacity .22s ease; }
html.cf-sb-open #cf-sb-backdrop{ opacity:1; pointer-events:auto; }
html.cf-sb-open .cf-sb-hamburger{ display:none !important; }
@media (max-width:767px){
    /* Sidebar als off-canvas overlay (standaard buiten beeld → geen overlap, content
       volledig zichtbaar). Schuift soepel in zodra <html> de klasse cf-sb-open krijgt. */
    [data-testid="stSidebar"]{
        position:fixed !important; top:0 !important; left:0 !important; height:100vh !important;
        width:220px !important; min-width:220px !important; max-width:220px !important;
        z-index:2000 !important; transform:translateX(-100%);
        transition:transform .25s ease !important; }
    [data-testid="stSidebar"]>div:first-child{ overflow-y:auto !important; }
    html.cf-sb-open [data-testid="stSidebar"]{ transform:translateX(0) !important;
        box-shadow:6px 0 34px rgba(8,26,54,.38) !important; }
    .cf-sb-hamburger{ display:flex; }
    #cf-sb-backdrop{ display:block; }
    /* Semi-inklap-toggle (◀/▶) is een desktopfunctie → op mobiel verbergen. */
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(span.sb-toggle-mk) + [data-testid="stElementContainer"]{ display:none !important; }
    /* Wat extra ruimte bovenaan zodat de hamburger niet over de paginatitel valt. */
    [data-testid="stMainBlockContainer"], .block-container{ padding-top:3.6rem !important; }
}
""")

# Minimale JS: hamburger + backdrop éénmalig in het hoofddocument plaatsen en bedraden.
# De daadwerkelijke in-/uitschuif-animatie is puur CSS (transform). De close-reset draait
# alleen wanneer de PAGINA wisselt: de scriptinhoud bevat `selected`, dus Streamlit hergebruikt
# het iframe bij een gelijke-pagina-rerun (sidebar blijft open) en draait het script opnieuw
# bij navigatie (sidebar sluit automatisch). Op desktop wordt de klasse nooit gezet en is de
# hamburger display:none → daar gebeurt visueel niets.
_components.html(
    "<script>/* cf-msb " + str(selected) + " */(function(){"
    "var p=window.parent.document;"
    "if(!p.getElementById('cf-sb-hamburger')){"
    "var h=p.createElement('button');h.id='cf-sb-hamburger';h.className='cf-sb-hamburger';h.type='button';"
    "h.setAttribute('aria-label','Navigatie openen of sluiten');"
    "h.innerHTML='<span></span><span></span><span></span>';"
    "h.addEventListener('click',function(e){e.stopPropagation();p.documentElement.classList.toggle('cf-sb-open');});"
    "p.body.appendChild(h);"
    "var b=p.createElement('div');b.id='cf-sb-backdrop';"
    "b.addEventListener('click',function(){p.documentElement.classList.remove('cf-sb-open');});"
    "p.body.appendChild(b);}"
    "p.documentElement.classList.remove('cf-sb-open');"
    "})();</script>", height=0, scrolling=False)

# ── Sidebar gegarandeerd zichtbaar houden ─────────────────────────────────────
# Twee dingen die de sidebar kunnen verbergen, hier opgeruimd (draait alleen ná de
# auth-gate, dus in ingelogde staat):
#  1) De login-CSS (#cf-auth-css) zet de sidebar op display:none en blijft in het
#     hoofddocument staan ná inloggen → verwijderen zodra we ingelogd zijn.
#  2) Streamlit kan de sidebar inklappen (smal venster / onthouden voorkeur); de
#     uitklap-knop zit in de verborgen header → we klikken die knop programmatisch.
_components.html("""<script>(function(){
function fixSidebar(){
    var p = window.parent.document;
    var ac = p.getElementById('cf-auth-css');   // login-CSS verbergt de sidebar
    if (ac) { ac.remove(); }
    var sb = p.querySelector('[data-testid="stSidebar"]');
    // Sidebar alleen geforceerd uitklappen op desktop/tablet (>767px). Op mobiel NIET:
    // daar moet Streamlit's eigen ingeklapte-overlay (hamburger + off-canvas) leidend zijn,
    // zodat de sidebar de content niet overlapt (Fase 1 responsive).
    var w = (p.defaultView || window.parent || window).innerWidth || 9999;
    if (w > 767 && sb && sb.getAttribute('aria-expanded') === 'false') {
        var b = p.querySelector('[data-testid="stExpandSidebarButton"]');
        if (b) { b.click(); }
    }
}
fixSidebar();
setTimeout(fixSidebar, 60); setTimeout(fixSidebar, 250); setTimeout(fixSidebar, 600);
})();</script>""", height=0, scrolling=False)

# Bij paginawissel: de Offerte/Factuur-downloadknoppen (Projecten → Acties) weer
# inklappen, zodat ze pas opnieuw verschijnen na een klik op 'PDF genereren'.
if st.session_state.get("_actieve_pagina") != selected:
    _wis_pdf_downloadknoppen()
    st.session_state["_actieve_pagina"] = selected

# Databasestatus: nette melding bovenaan als de opslaglaag terugviel op de lokale
# back-up of als een schrijfactie faalde (geen rauwe exceptie/witte pagina).
if st.session_state.get("_db_fout"):
    st.warning("⚠️ " + st.session_state["_db_fout"])


@st.fragment
def _agenda_taken_fragment(geselecteerde_dag_key, jaar, maand):
    """Agenda: datum-header + takenlijst + toevoegen/verwijderen, geïsoleerd als fragment.
    Toevoegen/verwijderen rerunt ALLÉÉN dit blok — niet het hele dashboard. Daardoor geen
    volledige-pagina-flits (witte waas), geen scroll-sprong, en de zware kalender/KPI-cards
    worden niet opnieuw getekend. De save gebeurt in de on_click-callback vóór de rerun."""
    _DAG_NAMEN = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    _MAANDEN = ["", "Januari", "Februari", "Maart", "April", "Mei", "Juni",
                "Juli", "Augustus", "September", "Oktober", "November", "December"]
    _STATUS_CFG = {
        "In uitvoering":     ("#F59E0B", "db-badge-uitvoering"),
        "Offerte verzonden": ("#8B5CF6", "db-badge-verzonden"),
        "Geaccepteerd":      ("#10B981", "db-badge-geaccepteerd"),
        "Afgerond":          ("#3B82F6", "db-badge-afgerond"),
        "Geannuleerd":       ("#EF4444", "db-badge-geannuleerd"),
        "Afspraak":          ("#4F46E5", "db-badge-afspraak"),
    }
    _DOT_KLEUREN = ["#3B82F6", "#F59E0B", "#10B981", "#8B5CF6", "#EF4444", "#EC4899"]

    def _agenda_del(_daykey, _taskid):
        _lst = st.session_state.agenda_taken.get(_daykey, [])
        st.session_state.agenda_taken[_daykey] = [t for t in _lst if t.get("id") != _taskid]
        save_data()

    def _agenda_add(_daykey):
        _txt = (st.session_state.get(f"agenda_input_{_daykey}", "") or "").strip()
        if not _txt:
            return
        if _daykey not in st.session_state.agenda_taken:
            st.session_state.agenda_taken[_daykey] = []
        _nid = max((t.get("id", 0)
                    for _dag in st.session_state.agenda_taken.values()
                    for t in _dag), default=0) + 1
        st.session_state.agenda_taken[_daykey].append({
            "id": _nid, "tekst": _txt, "titel": _txt,
            "tijd": "", "subtitel": "", "status": "Afspraak"
        })
        save_data()

    sel_dag_nr = int(geselecteerde_dag_key.split("-")[2])
    _sel_datum = date(jaar, maand, sel_dag_nr)
    _dag_vol   = _DAG_NAMEN[_sel_datum.weekday()]
    _maand_vol = _MAANDEN[maand]

    st.markdown(
        f'<div style="margin:10px 0 8px;padding-top:12px;border-top:1px solid #F1F5F9;">'
        f'<span style="font-size:13px;font-weight:700;color:#0F172A;">'
        f'{_dag_vol} {sel_dag_nr} {_maand_vol.lower()} {jaar}</span></div>',
        unsafe_allow_html=True)

    dag_taken   = st.session_state.agenda_taken.get(geselecteerde_dag_key, [])
    MAX_VISIBLE = 3
    if dag_taken:
        _taken_vis   = dag_taken[:MAX_VISIBLE]
        _taken_extra = dag_taken[MAX_VISIBLE:]
        for taak in _taken_vis:
            _titel  = taak.get("titel", taak.get("tekst", ""))
            _sub    = taak.get("subtitel", "")
            _tijd   = taak.get("tijd", "")
            _status = taak.get("status", "")
            _dot_k  = _STATUS_CFG.get(_status, (_DOT_KLEUREN[taak["id"] % len(_DOT_KLEUREN)], ""))[0]
            _bdg_k  = _STATUS_CFG.get(_status, ("", "db-badge-afspraak"))[1]

            t_col, d_col = st.columns([9, 1])
            with t_col:
                _tijd_html  = (f'<div class="db-agenda-time">{h(_tijd)}</div>' if _tijd else "")
                _sub_html   = (f'<div class="db-agenda-sub">{h(_sub)}</div>' if _sub else "")
                _badge_html = (f'<span class="db-agenda-badge {_bdg_k}">{h(_status)}</span>'
                               if _status else "")
                st.markdown(
                    f'<div class="db-agenda-item">'
                    f'<div class="db-agenda-dot" style="background:{_dot_k};"></div>'
                    f'{_tijd_html}'
                    f'<div class="db-agenda-body">'
                    f'<div class="db-agenda-title">{h(_titel)}</div>'
                    f'{_sub_html}{_badge_html}'
                    f'</div></div>',
                    unsafe_allow_html=True)
            with d_col:
                st.markdown('<span class="ag-del-mk" style="display:none;"></span>', unsafe_allow_html=True)
                st.button("×", key=f"del_at_{taak['id']}_{geselecteerde_dag_key}",
                          on_click=_agenda_del, args=(geselecteerde_dag_key, taak["id"]))

        if _taken_extra:
            _n = len(_taken_extra)
            st.markdown(
                f'<div style="font-size:12px;color:#2563EB;font-weight:600;'
                f'padding:6px 0 2px;cursor:pointer;">'
                f'+ {_n} extra {"taak" if _n == 1 else "taken"}</div>',
                unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="font-size:12px;color:#CBD5E1;padding:6px 0 10px;font-style:italic;">'
            'Geen taken voor deze dag.</div>',
            unsafe_allow_html=True)

    col_inp, col_btn = st.columns([5, 1])
    with col_inp:
        st.text_input(
            "Nieuwe taak", placeholder="Taak toevoegen…",
            key=f"agenda_input_{geselecteerde_dag_key}",
            label_visibility="collapsed")
    with col_btn:
        st.button("+", key=f"agenda_add_{geselecteerde_dag_key}", use_container_width=True,
                  on_click=_agenda_add, args=(geselecteerde_dag_key,))


# =====================================================
# ADMIN DASHBOARD — gedeelde helpers (datumopmaak + gecachete cross-tenant fetch)
# =====================================================
def _fmt_reg_datum(iso):
    """ISO-timestamp (created_at) → 'DD-MM-YYYY' (één consistente NL-stijl).
    Leeg of onparsebaar → '—'. Crasht nooit."""
    if not iso:
        return "—"
    s = str(iso)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d-%m-%Y")
    except Exception:
        try:
            j, m, d = s[:10].split("-")
            return f"{d}-{m}-{j}"
        except Exception:
            return s[:10] or "—"


@st.cache_data(ttl=20, show_spinner=False)
def _admin_fetch_data():
    """Cross-tenant admindata (alle gebruikers + platformstatistieken) in ÉÉN gecachete
    aanroep → voorkomt dubbele queries bij elke rerun. TTL 20s; na elke admin-mutatie
    roept de UI `_admin_fetch_data.clear()` aan zodat de live refresh verse data toont."""
    return {"stats": _db.admin_stats(), "users": _db.admin_list_users()}


# =====================================================
# DASHBOARD
# =====================================================

if selected == "Dashboard":

    # ── Dashboard CSS ──
    _inject_page_css("""
    /* ── KPI cards ── */
    .db-stat-card {
        background: white;
        border: 1px solid #E8EFF5;
        border-radius: 14px;
        padding: 18px 20px 16px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        transition: box-shadow 0.16s ease, transform 0.16s ease;
        position: relative;
        overflow: hidden;
        height: 100%;
    }
    .db-stat-card::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        border-radius: 14px 14px 0 0;
    }
    .db-stat-card.blue::before  { background: linear-gradient(90deg,#2563EB,#60A5FA); }
    .db-stat-card.green::before { background: linear-gradient(90deg,#059669,#34D399); }
    .db-stat-card.amber::before { background: linear-gradient(90deg,#D97706,#FBBF24); }
    .db-stat-card.indigo::before{ background: linear-gradient(90deg,#4F46E5,#818CF8); }
    .db-stat-card:hover { box-shadow:0 4px 16px rgba(0,0,0,0.08); transform:translateY(-2px); }
    .db-stat-icon {
        width:34px; height:34px; border-radius:9px;
        display:flex; align-items:center; justify-content:center;
        font-size:16px; margin-bottom:10px; flex-shrink:0;
    }
    .db-stat-icon.blue   { background:#EFF6FF; }
    .db-stat-icon.green  { background:#F0FDF4; }
    .db-stat-icon.amber  { background:#FFFBEB; }
    .db-stat-icon.indigo { background:#EEF2FF; }
    .db-stat-label { font-size:11px; font-weight:700; color:#94A3B8; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:4px; }
    .db-stat-value { font-size:28px; font-weight:800; color:#0F172A; letter-spacing:-1px; line-height:1.1; margin-bottom:4px; }
    /* Tekst-variant (bv. productnaam ipv getal): kleiner + altijd op één regel */
    .db-stat-value.db-stat-value-name { font-size:19px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .db-stat-sub   { font-size:12px; color:#94A3B8; line-height:1.4; }

    /* ── Section cards ── */
    .db-section {
        background: white;
        border: 1px solid #E8EFF5;
        border-radius: 14px;
        padding: 18px 20px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
        margin-bottom: 14px;
    }
    .db-section-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 14px;
        padding-bottom: 12px;
        border-bottom: 1px solid #F1F5F9;
    }
    .db-section-title { font-size:14px; font-weight:700; color:#0F172A; letter-spacing:-0.2px; }
    .db-section-link {
        font-size:11.5px; color:#2563EB; font-weight:600; cursor:pointer;
        background:#EFF6FF; padding:3px 9px; border-radius:6px; text-decoration:none;
        transition: background 0.14s ease;
    }
    .db-section-link:hover { background:#DBEAFE; }

    /* ── Project rows ── */
    .db-proj-row {
        display: flex; align-items: center; gap: 10px;
        padding: 9px 10px; border-radius: 9px;
        border: 1px solid #F1F5F9; background: #FAFBFC;
        margin-bottom: 6px; cursor: pointer;
        transition: all 0.13s ease;
    }
    .db-proj-row:hover { background:#EFF6FF; border-color:#BFDBFE; transform:translateX(2px); }
    .db-proj-thumb {
        width: 38px; height: 38px; border-radius: 9px; flex-shrink: 0;
        background: linear-gradient(135deg,#EFF6FF,#DBEAFE);
        display: flex; align-items: center; justify-content: center; font-size: 18px;
    }
    .db-proj-info { flex: 1; min-width: 0; }
    .db-proj-name { font-size:13px; font-weight:600; color:#0F172A; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; line-height:1.3; }
    .db-proj-addr { font-size:11px; color:#94A3B8; margin-top:1px; }
    .db-proj-badge { padding:2px 8px; border-radius:99px; font-size:10.5px; font-weight:600; white-space:nowrap; flex-shrink:0; }
    .db-proj-amt   { font-size:12.5px; font-weight:700; color:#0F172A; font-family:'DM Mono',monospace; flex-shrink:0; }
    .db-proj-arrow { color:#CBD5E1; font-size:14px; flex-shrink:0; transition: color 0.13s ease; }
    .db-proj-row:hover .db-proj-arrow { color:#2563EB; }

    /* ── Calendar (HTML helper classes) ── */
    .db-cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:3px; margin-bottom:10px; }
    .db-cal-day-header { font-size:9.5px; font-weight:700; color:#94A3B8; text-align:center; padding:3px 0; text-transform:uppercase; letter-spacing:0.05em; }
    .db-cal-day { aspect-ratio:1; display:flex; align-items:center; justify-content:center; border-radius:8px; font-size:12px; cursor:pointer; position:relative; color:#374151; font-weight:400; border:1px solid #F1F5F9; transition:all 0.1s ease; }
    .db-cal-day:hover { background:#EFF6FF; border-color:#BFDBFE; }
    .db-cal-day.today { background:#081A36; color:white; font-weight:700; border-color:#081A36; }
    .db-cal-day.selected { background:#EFF6FF; color:#2563EB; font-weight:600; border:1.5px solid #2563EB; }
    .db-cal-day.other-month { color:#D1D5DB; background:transparent; border-color:transparent; }
    .db-cal-dot { width:4px; height:4px; border-radius:99px; background:#F59E0B; position:absolute; bottom:3px; left:50%; transform:translateX(-50%); }
    .db-cal-dot.blue { background:#3B82F6; }
    .db-cal-day.today .db-cal-dot { background:rgba(255,255,255,0.7); }

    /* ── Agenda taak-items ── */
    .db-agenda-item { display:flex; gap:10px; align-items:flex-start; padding:8px 0; border-bottom:1px solid #F1F5F9; }
    .db-agenda-item:last-child { border-bottom:none; }
    .db-agenda-dot { width:8px; height:8px; border-radius:99px; flex-shrink:0; margin-top:5px; }
    .db-agenda-time { font-size:11px; color:#94A3B8; font-weight:500; min-width:36px; flex-shrink:0; margin-top:3px; }
    .db-agenda-body { flex:1; min-width:0; }
    .db-agenda-title { font-size:12.5px; font-weight:600; color:#0F172A; line-height:1.3; }
    .db-agenda-sub   { font-size:11px; color:#94A3B8; margin-top:1px; }
    .db-agenda-badge { display:inline-flex; align-items:center; padding:2px 8px; border-radius:99px; font-size:10px; font-weight:600; white-space:nowrap; margin-top:4px; }
    .db-badge-uitvoering  { background:#FEF3C7; color:#92400E; }
    .db-badge-verzonden    { background:#EDE9FE; color:#5B21B6; }
    .db-badge-geaccepteerd{ background:#DCFCE7; color:#166534; }
    .db-badge-afgerond    { background:#DBEAFE; color:#1E40AF; }
    .db-badge-geannuleerd { background:#FEE2E2; color:#991B1B; }
    .db-badge-afspraak    { background:#E0E7FF; color:#3730A3; }

    /* ── Activity rows ── */
    .db-activity-row { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid #F8FAFC; }
    .db-activity-row:last-child { border-bottom:none; }
    .db-activity-icon { width:30px; height:30px; border-radius:8px; flex-shrink:0; display:flex; align-items:center; justify-content:center; font-size:13px; }
    .db-activity-info { flex:1; min-width:0; }
    .db-activity-title { font-size:12.5px; font-weight:600; color:#0F172A; }
    .db-activity-sub   { font-size:11px; color:#94A3B8; margin-top:1px; }
    .db-activity-time  { font-size:10.5px; color:#94A3B8; flex-shrink:0; }

    /* ── Trend badge ── */
    .db-trend-badge { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:99px; font-size:10.5px; font-weight:700; }
    .db-trend-up   { background:#DCFCE7; color:#166534; }
    .db-trend-down { background:#FEE2E2; color:#991B1B; }
    """)

    # ── Header ──
    hdr_l, hdr_r = st.columns([6, 2])
    with hdr_l:
        st.markdown("""
        <div style="padding-bottom:4px;">
          <div style="font-size:26px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;line-height:1.2;">Dashboard</div>
          <div style="font-size:12.5px;color:#94A3B8;margin-top:4px;font-weight:400;">Welkom terug — hier is een overzicht van je bedrijf.</div>
        </div>
        """, unsafe_allow_html=True)
    with hdr_r:
        _vandaag_lbl = date.today().strftime("%d %b %Y")
        st.markdown(f"""
        <div style="display:flex;justify-content:flex-end;padding-top:6px;">
          <div style="background:white;border:1px solid #E8EFF5;border-radius:8px;
                      padding:6px 12px;font-size:12px;font-weight:500;color:#374151;
                      display:flex;align-items:center;gap:6px;
                      box-shadow:0 1px 2px rgba(0,0,0,0.04);">
            <i class="bi bi-calendar3" style="font-size:13px;"></i> {_vandaag_lbl}
          </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

    # ── Statistiek cards — omzet dynamisch uit echte projectdata (SP-010) ──
    OMZET_STATUSSEN = ("Geaccepteerd", "In uitvoering", "Afgerond")
    _vandaag = date.today()

    def _proj_datum(p):
        try:
            return date.fromisoformat(str(p.get("aangemaakt", ""))[:10])
        except Exception:
            return None

    # Periode uit Instellingen → Voorkeuren (SP-012: dashboard_periode)
    _periode = st.session_state.instellingen.get("dashboard_periode", "Huidige maand")
    if _periode == "Afgelopen 3 maanden":
        # huidige maand + 2 voorgaande — exacte maand-rekenkunde (geen dagen-benadering)
        _mnd0 = _vandaag.month - 2
        _p_start = date(_vandaag.year + (_mnd0 - 1) // 12, (_mnd0 - 1) % 12 + 1, 1)
    elif _periode == "Huidig jaar":
        _p_start = _vandaag.replace(month=1, day=1)
    elif _periode == "Alles":
        _p_start = None
    else:  # Huidige maand
        _p_start = _vandaag.replace(day=1)

    _m_start  = _vandaag.replace(day=1)
    _vm_eind  = _m_start - timedelta(days=1)
    _vm_start = _vm_eind.replace(day=1)

    omzet_periode    = 0.0   # KPI-kaart (volgt periode-instelling)
    omzet_maand      = 0.0   # grafiek "Omzet deze maand"
    omzet_vorige_mnd = 0.0   # trend t.o.v. vorige maand
    _omzet_per_dag   = {}    # dag → omzet (grafiekdata huidige maand)
    for p in st.session_state.projecten:
        if p["status"] not in OMZET_STATUSSEN:
            continue
        _d = _proj_datum(p)
        _bedrag = bereken_project_totaal(p)["excl_btw"]
        if _p_start is None or (_d and _p_start <= _d <= _vandaag):
            omzet_periode += _bedrag
        if _d and _m_start <= _d <= _vandaag:
            omzet_maand += _bedrag
            _omzet_per_dag[_d.day] = _omzet_per_dag.get(_d.day, 0.0) + _bedrag
        elif _d and _vm_start <= _d <= _vm_eind:
            omzet_vorige_mnd += _bedrag

    # Trend: echte vergelijking met vorige maand (SP-010)
    if omzet_vorige_mnd > 0:
        _trend_pct = (omzet_maand - omzet_vorige_mnd) / omzet_vorige_mnd * 100
        _omzet_sub = f"{'↑' if _trend_pct >= 0 else '↓'} {abs(_trend_pct):.1f}% t.o.v. vorige maand"
    elif omzet_maand > 0:
        _trend_pct = None
        _omzet_sub = "Geen omzet in vorige maand"
    else:
        _trend_pct = None
        _omzet_sub = "Nog geen omzet deze maand"

    open_cnt = sum(1 for p in st.session_state.projecten if p["status"] == "Offerte verzonden")
    uit_cnt  = sum(1 for p in st.session_state.projecten if p["status"] == "In uitvoering")
    afgr_cnt = sum(1 for p in st.session_state.projecten if p["status"] == "Afgerond")
    open_taken = sum(1 for t in st.session_state.taken if not t["klaar"])

    s1, s2, s3, s4 = st.columns(4)
    _stat_icon_clr = {"blue": "#2563EB", "green": "#059669", "amber": "#D97706", "indigo": "#4F46E5"}
    for col, cls, icon, label, val, sub in [
        (s1, "blue",  "file-text",      "OPEN OFFERTES",      open_cnt, f"{open_taken} taak openstaand"),
        (s2, "green", "hammer",         "IN UITVOERING",       uit_cnt,  "Geen actieve projecten" if uit_cnt==0 else f"{uit_cnt} actief"),
        (s3, "amber", "check2-circle",  "AFGEROND",            afgr_cnt, "Projecten afgerond"),
        (s4, "indigo","cash-stack",     f"OMZET ({_periode.upper()})", format_eur(omzet_periode), _omzet_sub),
    ]:
        with col:
            val_style = 'style="color:#4F46E5;font-size:24px;"' if cls == "indigo" else ""
            st.markdown(f"""
            <div class="db-stat-card {cls}">
                <div class="db-stat-icon {cls}"><i class="bi bi-{icon}" style="font-size:17px;color:{_stat_icon_clr.get(cls, '#2563EB')};"></i></div>
                <div class="db-stat-label">{label}</div>
                <div class="db-stat-value" {val_style}>{val}</div>
                <div class="db-stat-sub">{sub}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

    # ── Agenda CSS — vóór kolommen zodat de inject-iframe niet in col_agenda valt ──
    # (een height=0 iframe binnenin col_agenda zou een extra flex gap boven de agenda-card geven)
    _inject_page_css("""
        /* Witte card — scoped to direct child layout */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker){background:white !important;}
        /* Rand transparant: alleen de border-color (border-width/padding/schaduw/achtergrond/hoogte/breedte ongewijzigd). */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker) > div[data-testid="stVerticalBlock"]{border-color:transparent !important;}
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker) div[data-testid="stVerticalBlock"],
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker) div[data-testid="stHorizontalBlock"],
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker) div[data-testid="stColumn"]{background:white !important;}
        /* Vandaag knop — wit, compacter */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.ag-vandaag-mk):not(:has(span.cal-m-day)):not(:has(span.cal-m-tod)) button{
            height:28px !important;min-height:28px !important;padding:0 12px !important;
            font-size:12px !important;font-weight:600 !important;border-radius:7px !important;
            border:1px solid #E2E8F0 !important;background:white !important;color:#374151 !important;
            box-shadow:none !important;transform:none !important;width:100% !important;
            white-space:nowrap !important;}
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.ag-vandaag-mk):not(:has(span.cal-m-day)):not(:has(span.cal-m-tod)) button:hover{
            background:#F8FAFC !important;border-color:#CBD5E1 !important;transform:none !important;}
        /* Pijl-knoppen — wit, vierkant */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.ag-arr-mk):not(:has(span.cal-m-day)):not(:has(span.cal-m-tod)) button{
            height:28px !important;min-height:28px !important;width:28px !important;min-width:28px !important;
            padding:0 !important;font-size:15px !important;font-weight:600 !important;border-radius:7px !important;
            border:1px solid #E2E8F0 !important;background:white !important;color:#374151 !important;
            box-shadow:none !important;transform:none !important;}
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.ag-arr-mk):not(:has(span.cal-m-day)):not(:has(span.cal-m-tod)) button:hover{
            background:#F8FAFC !important;border-color:#CBD5E1 !important;transform:none !important;}
        /* ── COMPACT KALENDER GRID ──
           Alle selectors gebruiken stColumn>stVB>stLW als anker zodat ze ALLEEN
           de agenda bordered container matchen en nooit de buitenste stLW wrapper
           die transitief col_main omvat. */
        /* Week-rijen: minimale horizontale gap */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-day),
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-tod),
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-sel),
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(div.cal-day-other){gap:0 !important;}
        /* Week-rij containers: minimale verticale margin */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stElementContainer"]:has(div[data-testid="stHorizontalBlock"]:has(span.cal-m-day)),
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stElementContainer"]:has(div[data-testid="stHorizontalBlock"]:has(span.cal-m-tod)),
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stElementContainer"]:has(div[data-testid="stHorizontalBlock"]:has(span.cal-m-sel)),
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stElementContainer"]:has(div[data-testid="stHorizontalBlock"]:has(div.cal-day-other)){
            margin:0 !important;padding:0 !important;}
        /* Dag-cel kolommen: geen padding */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-day) div[data-testid="stColumn"],
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-tod) div[data-testid="stColumn"],
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-sel) div[data-testid="stColumn"],
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(div.cal-day-other) div[data-testid="stColumn"]{
            padding:0 !important;min-width:0 !important;}
        /* Dag-cel stVerticalBlock: position:relative voor dot + gap:0 */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-day) div[data-testid="stColumn"]
            div[data-testid="stVerticalBlock"]:not(:has(div[data-testid="stHorizontalBlock"])),
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-tod) div[data-testid="stColumn"]
            div[data-testid="stVerticalBlock"]:not(:has(div[data-testid="stHorizontalBlock"])),
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-sel) div[data-testid="stColumn"]
            div[data-testid="stVerticalBlock"]:not(:has(div[data-testid="stHorizontalBlock"])){
            position:relative !important;gap:0 !important;}
        /* stElementContainers in dag-cellen: geen margin */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-day) div[data-testid="stColumn"]
            div[data-testid="stVerticalBlock"]:not(:has(div[data-testid="stHorizontalBlock"]))
            > div[data-testid="stElementContainer"],
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-tod) div[data-testid="stColumn"]
            div[data-testid="stVerticalBlock"]:not(:has(div[data-testid="stHorizontalBlock"]))
            > div[data-testid="stElementContainer"],
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(span.cal-m-sel) div[data-testid="stColumn"]
            div[data-testid="stVerticalBlock"]:not(:has(div[data-testid="stHorizontalBlock"]))
            > div[data-testid="stElementContainer"]{
            margin:0 !important;padding:0 !important;}
        /* Dot indicator — ::after op de knop zelf, altijd IN het vierkant */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-has-task) button{
            position:relative !important;}
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-has-task) button::after{
            content:'';position:absolute;bottom:4px;left:50%;transform:translateX(-50%);
            width:4px;height:4px;border-radius:99px;background:#F59E0B;pointer-events:none;}
        /* Kalender dag-knoppen */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-day):not(:has(span.ag-vandaag-mk)):not(:has(span.ag-arr-mk)):not(:has(span.ag-add-mk)):not(:has(span.ag-del-mk)) button,
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-sel):not(:has(span.ag-vandaag-mk)):not(:has(span.ag-arr-mk)) button{
            height:38px !important;min-height:38px !important;padding:0 !important;
            font-size:12px !important;border-radius:0 !important;font-weight:400 !important;
            background:white !important;border:1px solid #E6EBF1 !important;
            color:#374151 !important;box-shadow:none !important;
            transform:none !important;width:100% !important;}
        /* Normale dag hover */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-day):not(:has(span.ag-vandaag-mk)):not(:has(span.ag-arr-mk)):not(:has(span.ag-add-mk)):not(:has(span.ag-del-mk)) button:hover{
            background:#EFF6FF !important;color:#2563EB !important;
            border-color:#BFDBFE !important;transform:none !important;}
        /* Geselecteerde dag — volledig blauw gevuld.
           Extra :not(:has(ag-arr-mk)) zodat de specificiteit de basis-knopregel
           evenaart en deze regel (later in bron) wint. */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-sel):not(:has(span.ag-vandaag-mk)):not(:has(span.ag-arr-mk)) button{
            background:#2563EB !important;color:white !important;
            font-weight:700 !important;border:1px solid #2563EB !important;}
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-sel):not(:has(span.ag-vandaag-mk)):not(:has(span.ag-arr-mk)) button:hover{
            background:#1D4FD7 !important;color:white !important;border-color:#1D4FD7 !important;}
        /* Vandaag cel (niet geselecteerd) — subtiele blauwe markering */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-tod):not(:has(span.ag-vandaag-mk)):not(:has(span.ag-arr-mk)) button{
            height:38px !important;min-height:38px !important;padding:0 !important;
            font-size:12px !important;border-radius:0 !important;width:100% !important;
            background:#EFF6FF !important;color:#2563EB !important;
            font-weight:700 !important;border:1px solid #BFDBFE !important;
            box-shadow:none !important;transform:none !important;}
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.cal-m-tod):not(:has(span.ag-vandaag-mk)):not(:has(span.ag-arr-mk)) button:hover{
            background:#DBEAFE !important;color:#2563EB !important;border-color:#93C5FD !important;}
        /* Andere maand — zelfde gridcel, lichtgrijs */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker) div.cal-day-other{
            height:38px;display:flex;align-items:center;justify-content:center;
            font-size:11px;color:#CBD5E1;background:#FBFCFD;
            border:1px solid #E6EBF1;box-sizing:border-box;}
        /* Verwijder-knop */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.ag-del-mk):not(:has(span.cal-m-day)):not(:has(span.cal-m-tod)) button{
            height:20px !important;min-height:20px !important;padding:0 !important;
            font-size:13px !important;border-radius:5px !important;
            background:transparent !important;border:none !important;
            color:#CBD5E1 !important;box-shadow:none !important;transform:none !important;}
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stColumn"]:has(span.ag-del-mk):not(:has(span.cal-m-day)):not(:has(span.cal-m-tod)) button:hover{
            background:#FEE2E2 !important;color:#DC2626 !important;transform:none !important;}
        /* Toevoegen-knop — stHB met input, laatste kolom */
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(input[placeholder])
            div[data-testid="stColumn"]:last-child button{
            height:38px !important;min-height:38px !important;padding:0 !important;
            font-size:18px !important;border-radius:8px !important;font-weight:600 !important;
            background:#081A36 !important;border:none !important;color:white !important;
            box-shadow:none !important;transform:none !important;width:100% !important;}
        div[data-testid="stColumn"]>div[data-testid="stVerticalBlock"]>div[data-testid="stLayoutWrapper"]:has(span.agenda-card-marker)
            div[data-testid="stHorizontalBlock"]:has(input[placeholder])
            div[data-testid="stColumn"]:last-child button:hover{
            background:#041124 !important;transform:none !important;}
        """)

    # ── Hoofd-layout: links (projecten + overzicht + activiteit) | rechts (agenda) ──
    col_main, col_agenda = st.columns([3, 2])

    with col_main:

        # ── Recente projecten ──
        PROJECT_ICONEN = ["house-fill", "building", "buildings-fill", "house-door-fill", "building-fill", "hammer", "palette-fill"]

        # SP-012: Instellingen → Voorkeuren "Standaard filter" toepassen
        _db_filter = st.session_state.instellingen.get("dashboard_filter", "Alle projecten")
        _DB_FILTER_STATUS = {"In uitvoering":      ["In uitvoering"],
                             "Offertes uitstaand": ["Offerte verzonden"],
                             "Afgerond":           ["Afgerond"]}
        _recent_bron = st.session_state.projecten
        if _db_filter in _DB_FILTER_STATUS:
            _recent_bron = [p for p in _recent_bron if p["status"] in _DB_FILTER_STATUS[_db_filter]]

        # Rijen HTML opbouwen — geen inspringing om Markdown code-block detectie te vermijden
        if not _recent_bron:
            _proj_rows_html = '<div style="font-size:13px;color:#94A3B8;padding:12px 0;">Nog geen projecten aangemaakt.</div>'
        else:
            _proj_rows_html = ""
            for project in reversed(_recent_bron[-5:]):
                calc     = bereken_project_totaal(project)
                bi_icon  = PROJECT_ICONEN[project["id"] % len(PROJECT_ICONEN)]
                color_s, bg_s = STATUS_KLEUREN.get(project["status"], ("#475569","#F1F5F9"))
                adres = project.get("adres","")
                _proj_rows_html += (
                    f'<div class="db-proj-row">'
                    f'<div class="db-proj-thumb"><i class="bi bi-{bi_icon}" style="font-size:17px;color:#2563EB;"></i></div>'
                    f'<div class="db-proj-info">'
                    f'<div class="db-proj-name">{h(project["naam"])}</div>'
                    f'<div class="db-proj-addr">{h(adres)}</div>'
                    f'</div>'
                    f'<span class="db-proj-badge" style="background:{bg_s};color:{color_s};">{h(project["status"])}</span>'
                    f'<span class="db-proj-amt">{format_eur(calc["excl_btw"])}</span>'
                    f'<span class="db-proj-arrow">›</span>'
                    f'</div>'
                )

        # Kaart als .db-section HTML — geen inspringing op de wrapper (zelfde patroon als act_html/ov_html)
        _proj_card_html = (
            '<div class="db-section">'
            '<div class="db-section-header">'
            '<span class="db-section-title">Recente projecten</span>'
            '<span class="db-section-link" id="db-proj-bekijk-link" style="cursor:pointer;">Bekijk alles →</span>'
            '</div>'
            + _proj_rows_html +
            '</div>'
        )
        st.markdown(_proj_card_html, unsafe_allow_html=True)

        # ── Overzicht (grafiek + donut) — volledig in één HTML string ──
        # SP-010: grafiek = echte cumulatieve omzet per dag van de huidige maand
        import math as m
        import calendar as _cal
        _dim = _cal.monthrange(_vandaag.year, _vandaag.month)[1]
        pts_raw, _lopend = [], 0.0
        for _dg in range(1, _dim + 1):
            _lopend += _omzet_per_dag.get(_dg, 0.0)
            pts_raw.append(_lopend)
        npts  = len(pts_raw)
        max_v = max(pts_raw) if max(pts_raw) > 0 else 1
        W, H = 320, 72
        xs = [int(i * W / (npts-1)) for i in range(npts)]
        ys = [int(H - (v/max_v) * H * 0.85) for v in pts_raw]
        path = " ".join(f"{'M' if i==0 else 'L'}{x},{y}" for i,(x,y) in enumerate(zip(xs,ys)))
        fill_path = f"{path} L{xs[-1]},{H} L{xs[0]},{H} Z"

        # SP-010: trendbadge en as-labels uit echte data i.p.v. hardcoded
        if _trend_pct is None:
            _trend_badge = ""
        else:
            _t_cls   = "db-trend-up" if _trend_pct >= 0 else "db-trend-down"
            _t_arrow = "↑" if _trend_pct >= 0 else "↓"
            _trend_badge = f'<span class="db-trend-badge {_t_cls}">{_t_arrow} {abs(_trend_pct):.1f}%</span>'
        _MND_KORT = ["jan","feb","mrt","apr","mei","jun","jul","aug","sep","okt","nov","dec"]
        _mk = _MND_KORT[_vandaag.month - 1]
        _as_labels = "".join(f"<span>{_dg} {_mk}</span>" for _dg in (1, 8, 15, 22, _dim))

        # Donut — alle 6 statussen uit STATUS_KLEUREN
        _STATUS_DONUT_KLEUREN = {
            "Concept":           "#94A3B8",
            "Offerte verzonden": "#3B82F6",
            "Geaccepteerd":      "#10B981",
            "In uitvoering":     "#F59E0B",
            "Afgerond":          "#8B5CF6",
            "Geannuleerd":       "#EF4444",
        }
        status_data = [
            (s, sum(1 for p in st.session_state.projecten if p["status"] == s), clr)
            for s, clr in _STATUS_DONUT_KLEUREN.items()
            if sum(1 for p in st.session_state.projecten if p["status"] == s) > 0
        ]
        if not status_data:
            status_data = [("Geen projecten", 1, "#E2E8F0")]
        cx, cy, r, sw = 55, 55, 40, 14
        total_shown = sum(v for _, v, _ in status_data) or 1
        circumference = 2 * m.pi * r
        arcs = ""
        offset_d = 0
        for label, val, color in status_data:
            dash = (val / total_shown) * circumference
            arcs += f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-dasharray="{dash:.1f} {circumference:.1f}" stroke-dashoffset="-{offset_d:.1f}" style="transform:rotate(-90deg);transform-origin:{cx}px {cy}px;"/>'
            offset_d += dash
        total_lbl = len(st.session_state.projecten)
        legend_items = "".join(
            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;flex-wrap:nowrap;">'
            f'<div style="width:8px;height:8px;min-width:8px;border-radius:99px;background:{color};"></div>'
            f'<span style="font-size:11.5px;color:#374151;font-weight:500;white-space:nowrap;">{label}</span>'
            f'<span style="font-size:11px;color:#94A3B8;white-space:nowrap;margin-left:2px;">{val} ({int(val/total_shown*100)}%)</span>'
            f'</div>'
            for label, val, color in status_data
        )

        ov_html = f"""
        <div class="db-section">
          <div class="db-section-header">
            <div class="db-section-title">Overzicht</div>
          </div>
          <div class="db-ovz-row" style="display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start;">
            <div style="flex:3;min-width:min(240px,100%);">
              <div style="font-size:11px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">Omzet deze maand</div>
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                <span style="font-size:20px;font-weight:800;color:#0F172A;font-family:'DM Mono',monospace;letter-spacing:-0.5px;">{format_eur(omzet_maand)}</span>{_trend_badge}
              </div>
              <svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" style="width:100%;height:72px;display:block;overflow:visible;">
                <defs>
                  <linearGradient id="lg2" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#2563EB" stop-opacity="0.12"/>
                    <stop offset="100%" stop-color="#2563EB" stop-opacity="0"/>
                  </linearGradient>
                </defs>
                <path d="{fill_path}" fill="url(#lg2)"/>
                <path d="{path}" fill="none" stroke="#2563EB" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
              </svg>
              <div style="display:flex;justify-content:space-between;font-size:9.5px;color:#94A3B8;margin-top:3px;">
                {_as_labels}
              </div>
            </div>
            <div class="db-ovz-status" style="flex:2;flex-shrink:0;border-left:1px solid #F1F5F9;padding-left:20px;">
              <div style="font-size:11px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:10px;">Project status</div>
              <div style="display:flex;align-items:center;gap:14px;">
                <svg viewBox="0 0 110 110" style="width:100px;height:100px;flex-shrink:0;">
                  <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#F1F5F9" stroke-width="{sw}"/>
                  {arcs}
                  <text x="{cx}" y="{cy-4}" text-anchor="middle" dominant-baseline="central" font-size="17" font-weight="800" fill="#0F172A">{total_lbl}</text>
                  <text x="{cx}" y="{cy+13}" text-anchor="middle" font-size="9" fill="#94A3B8">Totaal</text>
                </svg>
                <div style="flex:1;">{legend_items}</div>
              </div>
            </div>
          </div>
        </div>"""
        st.markdown(ov_html, unsafe_allow_html=True)

        # ── Recente activiteit ──
        def _rel_datum(ds):
            if not ds: return ""
            try:
                s = str(ds)
                _has_time = len(s) > 10 and s[10] in ('T', ' ')
                if _has_time:
                    dt_obj = datetime.fromisoformat(s[:16])
                    d = dt_obj.date()
                    t = dt_obj.strftime("%H:%M")
                else:
                    d = date.fromisoformat(s[:10])
                    t = None
                delta = (date.today() - d).days
                if delta == 0:   return f"Vandaag {t}" if t else "Vandaag"
                if delta == 1:   return f"Gisteren {t}" if t else "Gisteren"
                if t:            return f"{d.strftime('%d-%m-%Y')} {t}"
                if delta < 7:    return f"{delta} d. geleden"
                if delta < 30:   return f"{delta // 7} wk geleden"
                return d.strftime("%d %b")
            except Exception:
                return ""

        _ACT_STATUS_CFG = {
            "Concept":           ("folder2",      "#FFFBEB", "#D97706", "Project aangemaakt"),
            "Offerte verzonden": ("file-text",    "#EFF6FF", "#2563EB", "Offerte verzonden"),
            "Geaccepteerd":      ("check-circle", "#F0FDF4", "#059669", "Project geaccepteerd"),
            "In uitvoering":     ("tools",        "#FEF3C7", "#92400E", "Project in uitvoering"),
            "Afgerond":          ("check2-all",   "#EDE9FE", "#5B21B6", "Project afgerond"),
            "Geannuleerd":       ("x-circle",     "#FEE2E2", "#991B1B", "Project geannuleerd"),
        }
        def _norm_sk(ds):
            s = str(ds) if ds else ""
            if len(s) > 10 and s[10] in ('T', ' '):
                return s[:16]
            return (s[:10] + "T00:00") if s else "0000-00-00T00:00"

        _acts = []
        for p in st.session_state.projecten:
            _cfg = _ACT_STATUS_CFG.get(p.get("status", "Concept"), _ACT_STATUS_CFG["Concept"])
            _dt  = p.get("aangemaakt", "")
            _sk  = f"{_norm_sk(_dt)}_{p['id']:06d}"
            _acts.append((_sk, _cfg[0], _cfg[1], _cfg[2],
                          f"{_cfg[3]}: {p['naam']}", get_klant_naam(p['klant_id']), _rel_datum(_dt)))
        for k in st.session_state.klanten:
            _dt = k.get("aangemaakt", "")
            _sk = f"{_norm_sk(_dt)}_{k['id']:06d}"
            _acts.append((_sk, "person", "#F0FDF4", "#059669",
                          f"Klant toegevoegd: {k['naam']}", k.get("stad", ""), _rel_datum(_dt)))
        for mw in st.session_state.personeel:
            _sk = f"0000-00-00_{mw['id']:06d}"
            _acts.append((_sk, "people", "#EFF6FF", "#2563EB",
                          f"Medewerker toegevoegd: {mw['naam']}", mw.get("functie", ""), ""))
        _acts.sort(key=lambda x: x[0], reverse=True)
        ACTIVITEITEN = [(ic, bg, cl, ti, su, tj) for _, ic, bg, cl, ti, su, tj in _acts[:4]]

        act_html = """
        <div class="db-section">
          <div class="db-section-header">
            <div class="db-section-title">Recente activiteit</div>
          </div>"""
        if not ACTIVITEITEN:
            act_html += '<div style="padding:20px 0;text-align:center;color:#94A3B8;font-size:13px;">Nog geen activiteiten</div>'
        for icon, bg, clr, title, sub, tijd in ACTIVITEITEN:
            act_html += f"""
          <div class="db-activity-row">
            <div class="db-activity-icon" style="background:{bg};"><i class="bi bi-{icon}" style="color:{clr};font-size:14px;"></i></div>
            <div class="db-activity-info">
              <div class="db-activity-title">{h(title)}</div>
              <div class="db-activity-sub">{h(sub)}</div>
            </div>
            <div class="db-activity-time">{tijd}</div>
          </div>"""
        act_html += "</div>"
        st.markdown(act_html, unsafe_allow_html=True)

        # Streamlit-knop voor navigatie-callback — wordt via JS verborgen en aan de HTML-span gekoppeld
        if st.button("Bekijk alles →", key="db_proj_bekijk_alles"):
            st.session_state["nav_doel"] = "Projecten"
            st.rerun()

        # JS: verberg Streamlit-knop en koppel HTML-span-klik eraan
        _html_component("""<script>(function(){
var SPAN_ID='db-proj-bekijk-link';
var BTN_TXT='Bekijk alles →';
function wire(){
    var p=window.parent.document;
    var span=p.getElementById(SPAN_ID);
    var btn=null;
    var all=p.querySelectorAll('button');
    for(var i=0;i<all.length;i++){
        if(all[i].textContent.trim()===BTN_TXT){btn=all[i];break;}
    }
    if(btn){
        var wrap=btn.closest('[data-testid="stButton"]');
        if(wrap) wrap.style.cssText='position:fixed;left:-9999px;top:-9999px;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none;';
    }
    if(span&&btn) span.onclick=function(){btn.click();};
}
wire();
var obs=new MutationObserver(function(){
    clearTimeout(window._dbProjT);
    window._dbProjT=setTimeout(wire,30);
});
obs.observe(window.parent.document.body,{childList:true,subtree:true});
})();</script>""", height=0, scrolling=False)

    # ── AGENDA ──
    with col_agenda:
        import calendar as cal_lib

        # ── Session state ──
        if "agenda_maand_offset" not in st.session_state:
            st.session_state.agenda_maand_offset = 0
        if "agenda_taken" not in st.session_state:
            # Voorbeelddata voor demo
            _demo_dag = str(date.today())
            st.session_state.agenda_taken = {
                _demo_dag: [
                    {"id": 1, "tekst": "Buitenschilderwerk Parkflat",
                     "tijd": "09:00", "titel": "Buitenschilderwerk Parkflat",
                     "subtitel": "VvE Parkflat", "status": "In uitvoering"},
                    {"id": 2, "tekst": "Woonkamer renovatie Jansen",
                     "tijd": "14:00", "titel": "Woonkamer renovatie Jansen",
                     "subtitel": "Familie Jansen", "status": "Offerte verzonden"},
                    {"id": 3, "tekst": "Nieuw project intake",
                     "tijd": "16:30", "titel": "Nieuw project intake",
                     "subtitel": "De Vries Bouw", "status": "Afspraak"},
                ]
            }
        if "agenda_geselecteerde_dag" not in st.session_state:
            st.session_state.agenda_geselecteerde_dag = str(date.today())

        today  = date.today()
        offset = st.session_state.agenda_maand_offset
        jaar   = today.year  + (today.month - 1 + offset) // 12
        maand  = (today.month - 1 + offset) % 12 + 1

        MAANDEN   = ["","Januari","Februari","Maart","April","Mei","Juni",
                     "Juli","Augustus","September","Oktober","November","December"]
        DAG_NAMEN = ["Maandag","Dinsdag","Woensdag","Donderdag","Vrijdag","Zaterdag","Zondag"]

        # Status → dot-kleur + badge-klasse
        _STATUS_CFG = {
            "In uitvoering":    ("#F59E0B", "db-badge-uitvoering"),
            "Offerte verzonden":("#8B5CF6", "db-badge-verzonden"),
            "Geaccepteerd":     ("#10B981", "db-badge-geaccepteerd"),
            "Afgerond":         ("#3B82F6", "db-badge-afgerond"),
            "Geannuleerd":      ("#EF4444", "db-badge-geannuleerd"),
            "Afspraak":         ("#4F46E5", "db-badge-afspraak"),
        }
        _DOT_KLEUREN = ["#3B82F6","#F59E0B","#10B981","#8B5CF6","#EF4444","#EC4899"]

        with st.container(border=True):
            st.markdown('<span class="agenda-card-marker" style="display:none;"></span>', unsafe_allow_html=True)

            # ── HEADER: 📅 Agenda | [Vandaag] [‹] [›] ──
            ag_title, ag_vandaag, ag_prev, ag_next = st.columns([2.4, 1.7, 0.55, 0.55])
            with ag_title:
                st.markdown(
                    '<div style="display:flex;align-items:center;gap:7px;padding:4px 0 2px;">'
                    '<i class="bi bi-calendar3" style="font-size:14px;color:#2563EB;"></i>'
                    '<span style="font-size:14px;font-weight:700;color:#0F172A;letter-spacing:-0.2px;">Agenda</span>'
                    '</div>',
                    unsafe_allow_html=True)
            with ag_vandaag:
                st.markdown('<span class="ag-vandaag-mk" style="display:none;"></span>', unsafe_allow_html=True)
                if st.button("Vandaag", key="agenda_vandaag"):
                    st.session_state.agenda_maand_offset = 0
                    st.session_state.agenda_geselecteerde_dag = str(today)
                    st.rerun()
            with ag_prev:
                st.markdown('<span class="ag-arr-mk" style="display:none;"></span>', unsafe_allow_html=True)
                if st.button("‹", key="agenda_prev"):
                    st.session_state.agenda_maand_offset -= 1
                    st.rerun()
            with ag_next:
                st.markdown('<span class="ag-arr-mk" style="display:none;"></span>', unsafe_allow_html=True)
                if st.button("›", key="agenda_next"):
                    st.session_state.agenda_maand_offset += 1
                    st.rerun()

            # ── MAAND LABEL ──
            st.markdown(
                f'<div style="text-align:center;font-size:13px;font-weight:600;color:#0F172A;'
                f'margin:6px 0 8px;">{MAANDEN[maand]} {jaar}</div>',
                unsafe_allow_html=True)

            # ── DAG-NAMEN HEADER — één HTML grid, geen st.columns overhead ──
            st.markdown(
                '<div style="display:grid;grid-template-columns:repeat(7,1fr);gap:0;'
                'margin:2px 0 4px;">'
                + ''.join(
                    f'<div style="font-size:9px;font-weight:700;color:#94A3B8;'
                    f'text-align:center;padding:3px 0;text-transform:uppercase;'
                    f'letter-spacing:0.03em;">{dn}</div>'
                    for dn in ["Ma","Di","Wo","Do","Vr","Za","Zo"])
                + '</div>',
                unsafe_allow_html=True)

            # ── KALENDER GRID ──
            first_day     = date(jaar, maand, 1)
            start_weekday = first_day.weekday()
            days_in_month = cal_lib.monthrange(jaar, maand)[1]
            prev_jaar     = jaar if maand > 1 else jaar - 1
            prev_maand_nr = maand - 1 if maand > 1 else 12
            prev_m_days   = cal_lib.monthrange(prev_jaar, prev_maand_nr)[1]

            total_cells = start_weekday + days_in_month
            total_weeks = (total_cells + 6) // 7
            for _wk in range(total_weeks):
                _wk_cols = st.columns(7)
                for _wd in range(7):
                    _ci = _wk * 7 + _wd
                    _d  = _ci - start_weekday + 1
                    with _wk_cols[_wd]:
                        if _d < 1:
                            # Laatste dagen vorige maand — lichtgrijs, niet klikbaar
                            st.markdown(f'<div class="cal-day-other">{prev_m_days + _d}</div>', unsafe_allow_html=True)
                        elif _d > days_in_month:
                            # Eerste dagen volgende maand — lichtgrijs, niet klikbaar
                            st.markdown(f'<div class="cal-day-other">{_d - days_in_month}</div>', unsafe_allow_html=True)
                        else:
                            _dkey   = f"{jaar}-{maand:02d}-{_d:02d}"
                            _is_tod = (_d == today.day and maand == today.month and jaar == today.year)
                            _is_sel = (_dkey == st.session_state.agenda_geselecteerde_dag)
                            _heeft_t = bool(st.session_state.agenda_taken.get(_dkey))
                            # Cel-type marker — selectie heeft voorrang (gevulde blauwe cel)
                            if _is_sel:
                                st.markdown('<span class="cal-m-sel" style="display:none;"></span>', unsafe_allow_html=True)
                            elif _is_tod:
                                st.markdown('<span class="cal-m-tod" style="display:none;"></span>', unsafe_allow_html=True)
                            else:
                                st.markdown('<span class="cal-m-day" style="display:none;"></span>', unsafe_allow_html=True)
                            # Taak-dot — ::after op knop via CSS; verborgen op de gevulde selectie-cel
                            if _heeft_t and not _is_sel:
                                st.markdown('<span class="cal-m-has-task" style="display:none;"></span>', unsafe_allow_html=True)
                            if st.button(str(_d), key=f"cal_{_dkey}", use_container_width=True):
                                st.session_state.agenda_geselecteerde_dag = _dkey
                                st.rerun()

            # ── GESELECTEERDE DAG ──
            if st.session_state.agenda_geselecteerde_dag.startswith(f"{jaar}-{maand:02d}"):
                geselecteerde_dag_key = st.session_state.agenda_geselecteerde_dag
            else:
                geselecteerde_dag_key = f"{jaar}-{maand:02d}-01"
                st.session_state.agenda_geselecteerde_dag = geselecteerde_dag_key

            # Takenlijst + toevoegen/verwijderen als geïsoleerd FRAGMENT (def bovenaan): add/
            # delete rerunt alleen dit blok → geen volledige-pagina-flits, geen scroll-sprong,
            # de kalender/KPI-cards worden niet hertekend.
            _agenda_taken_fragment(geselecteerde_dag_key, jaar, maand)

# =====================================================
# PROJECTEN
# =====================================================

elif selected == "Projecten":

    _inject_keyed_css("pj_page", """
    /* ── Paginatitel ── */
    .pj-page-title{font-size:26px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;line-height:1.2;}
    .pj-page-sub{font-size:12.5px;color:#94A3B8;font-weight:400;margin-top:3px;}
    /* ── Badges ── */
    .pj-badge{display:inline-flex;align-items:center;gap:5px;padding:4px 11px;border-radius:99px;font-size:11.5px;font-weight:600;white-space:nowrap;}
    .pj-badge-dot{width:5px;height:5px;border-radius:99px;flex-shrink:0;}
    .pj-badge.concept{background:#F1F5F9;color:#475569;}.pj-badge.concept .pj-badge-dot{background:#94A3B8;}
    .pj-badge.verzonden{background:#DBEAFE;color:#1E40AF;}.pj-badge.verzonden .pj-badge-dot{background:#3B82F6;}
    .pj-badge.geaccepteerd{background:#DCFCE7;color:#166534;}.pj-badge.geaccepteerd .pj-badge-dot{background:#16A34A;}
    .pj-badge.uitvoering{background:#FEF3C7;color:#92400E;}.pj-badge.uitvoering .pj-badge-dot{background:#F59E0B;}
    .pj-badge.afgerond{background:#F0FDF4;color:#15803D;}.pj-badge.afgerond .pj-badge-dot{background:#22C55E;}
    .pj-badge.geannuleerd{background:#FEE2E2;color:#991B1B;}.pj-badge.geannuleerd .pj-badge-dot{background:#EF4444;}
    /* ── Projectkaarten: witte achtergrond + schaduw (outer container only) ── */
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has([data-testid="stColumn"]:nth-child(4)){background:#FFFFFF !important;border-radius:12px !important;box-shadow:0 1px 3px rgba(0,0,0,0.04) !important;border:1px solid transparent !important;margin-bottom:6px !important;}
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has([data-testid="stColumn"]:nth-child(4))>[data-testid="stVerticalBlock"]{background:#FFFFFF !important;border-color:transparent !important;}
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has([data-testid="stColumn"]:nth-child(4)):hover{box-shadow:0 4px 12px rgba(37,99,235,0.10) !important;border-color:#BFDBFE !important;}
    /* ── Bekijken knop: donker (scoped op projectkaarten) ── */
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has([data-testid="stColumn"]:nth-child(4)) [data-testid="stBaseButton-secondary"]{background:#081A36 !important;color:white !important;font-size:12px !important;font-weight:600 !important;border:none !important;border-radius:8px !important;box-shadow:none !important;white-space:nowrap !important;overflow:hidden !important;}
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has([data-testid="stColumn"]:nth-child(4)) [data-testid="stBaseButton-secondary"]:hover{background:#041124 !important;}
    /* ── ⋮ popover knop: subtiel, transparante achtergrond + rand, geen chevron ── */
    /* border blijft 1px (transparant) zodat grootte/positie identiek blijven */
    [data-testid="stPopoverButton"]{background:transparent !important;color:#94A3B8 !important;font-size:16px !important;font-weight:400 !important;border:1px solid transparent !important;border-radius:8px !important;padding:8px 6px !important;box-shadow:none !important;height:auto !important;min-height:0 !important;line-height:1.4 !important;white-space:nowrap !important;}
    [data-testid="stPopoverButton"]:hover{background:#F1F5F9 !important;border-color:transparent !important;color:#374151 !important;}
    [data-testid="stPopoverButton"] [data-testid="stIconMaterial"]{display:none !important;}
    [data-testid="stPopoverButton"] > div > div:last-child{display:none !important;}
    [data-testid="stPopoverButton"] p{font-size:20px !important;line-height:1.2 !important;margin:0 !important;text-align:center !important;}
    /* ── Popover actiemenu: compact ── */
    [data-testid="stPopoverBody"]{min-width:130px !important;max-width:160px !important;padding:6px !important;}
    [data-testid="stPopoverBody"] [data-testid="stButton"] > button{background:transparent !important;border:none !important;box-shadow:none !important;color:#374151 !important;font-size:12px !important;font-weight:500 !important;padding:5px 8px !important;text-align:left !important;border-radius:6px !important;height:auto !important;min-height:0 !important;line-height:1.4 !important;justify-content:flex-start !important;width:100% !important;}
    [data-testid="stPopoverBody"] [data-testid="stButton"] > button:hover{background:#F1F5F9 !important;color:#0F172A !important;}
    [data-testid="stPopoverBody"] [data-testid="stMarkdownContainer"]:has(span.pj-del-mk) ~ [data-testid="stButton"] > button{color:#DC2626 !important;}
    [data-testid="stPopoverBody"] [data-testid="stMarkdownContainer"]:has(span.pj-del-mk) ~ [data-testid="stButton"] > button:hover{background:#FFF5F5 !important;color:#B91C1C !important;}
    /* ── Tekststijlen ── */
    .pj-name{font-size:14px;font-weight:700;color:#0F172A;letter-spacing:-0.2px;line-height:1.3;margin-bottom:3px;}
    .pj-client{font-size:12px;font-weight:500;color:#475569;margin-bottom:1px;}
    .pj-addr{font-size:11.5px;color:#94A3B8;}
    .pj-price{font-size:15px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;font-family:'DM Mono',monospace;}
    .pj-price-sub{font-size:10.5px;color:#94A3B8;margin-top:2px;}
    .pj-meta{font-size:11px;color:#94A3B8;margin-top:6px;}
    .pj-date-lbl{font-size:10px;font-weight:700;color:#CBD5E1;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:2px;}
    .pj-date-val{font-size:12px;font-weight:500;color:#475569;}
    /* ── Lege staat ── */
    .pj-empty{text-align:center;padding:56px 24px;background:white;border:1px solid #E8EFF5;border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04);}
    /* ── Nieuw project form: witte kaart ── */
    [data-testid="stForm"]:has(span.pj-form-mk){background:#FFFFFF !important;border:1px solid #E8EFF5 !important;border-radius:14px !important;box-shadow:0 1px 4px rgba(0,0,0,0.05) !important;}
    [data-testid="stForm"]:has(span.pj-form-mk) [data-testid="stMarkdownContainer"],[data-testid="stForm"]:has(span.pj-form-mk) [data-testid="stHorizontalBlock"],[data-testid="stForm"]:has(span.pj-form-mk) [data-testid="stColumn"]{background:#FFFFFF !important;}
    /* ── Nieuw project submit knop: donker ── */
    [data-testid="stForm"]:has(span.pj-form-mk) [data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]{background:#081A36 !important;border-color:#081A36 !important;color:white !important;}
    [data-testid="stForm"]:has(span.pj-form-mk) [data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]:hover{background:#041124 !important;border-color:#041124 !important;}
    /* stMarkdownContainer binnen de knop mag NIET wit worden ── */
    [data-testid="stForm"]:has(span.pj-form-mk) [data-testid="stFormSubmitButton"] [data-testid="stMarkdownContainer"]{background:transparent !important;}
    /* ═══════════════════════════════════════
       PROJECT DETAIL PAGINA
    ═══════════════════════════════════════ */
    /* Witte card base */
    .pd-card{background:#FFFFFF;border:1px solid #E8EFF5;border-radius:18px;box-shadow:0 1px 4px rgba(0,0,0,0.05);padding:24px 28px;margin-bottom:16px;}
    /* Sectieheader */
    .pd-sec-head{display:flex;align-items:center;gap:10px;margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid #F1F5F9;}
    .pd-sec-icon-box{width:34px;height:34px;border-radius:9px;background:#EFF6FF;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
    .pd-sec-title{font-size:15px;font-weight:700;color:#0F172A;}
    /* Info grid (4 blokken naast elkaar) */
    .pd-info-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0;}
    .pd-info-block{padding-right:20px;border-right:1px solid #F1F5F9;}
    .pd-info-block:last-child{border-right:none;padding-right:0;}
    .pd-info-block:not(:first-child){padding-left:20px;}
    .pd-info-lbl{font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.07em;margin-bottom:5px;}
    .pd-info-val{font-size:14px;font-weight:500;color:#0F172A;line-height:1.45;}
    /* Tabel */
    .pd-table-wrap{overflow-x:auto;}
    .pd-table{width:100%;border-collapse:collapse;}
    .pd-table th{font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;padding:8px 14px 10px;border-bottom:2px solid #F1F5F9;text-align:left;white-space:nowrap;}
    .pd-table th.r{text-align:right;}
    .pd-table td{padding:12px 14px;border-bottom:1px solid #F8FAFC;vertical-align:middle;font-size:13px;color:#374151;}
    .pd-table td.r{text-align:right;}
    .pd-table td.nm{font-weight:600;color:#0F172A;}
    .pd-table tr:last-child td{border-bottom:none;}
    .pd-table tbody tr:hover td{background:#FAFBFE;}
    /* Chips */
    .pd-chip-b{display:inline-flex;align-items:center;padding:3px 9px;background:#DBEAFE;color:#1E40AF;border-radius:99px;font-size:11px;font-weight:600;margin:2px 3px 2px 0;}
    .pd-chip-y{display:inline-flex;align-items:center;padding:3px 9px;background:#FEF3C7;color:#92400E;border-radius:99px;font-size:11px;font-weight:600;margin:2px 3px 2px 0;}
    /* Totaal */
    .pd-totaal-lbl{font-size:11px;font-weight:700;color:#64748B;text-transform:uppercase;letter-spacing:0.07em;}
    .pd-totaal-val{font-size:28px;font-weight:800;color:#0F172A;font-family:'DM Mono',monospace;letter-spacing:-0.8px;line-height:1.1;}
    .pd-totaal-sub{font-size:11.5px;color:#94A3B8;margin-top:3px;}
    /* Acties kaart: witte achtergrond */
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk){background:#FFFFFF !important;border:1px solid #E8EFF5 !important;border-radius:18px !important;box-shadow:0 1px 4px rgba(0,0,0,0.05) !important;}
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) [data-testid="stVerticalBlock"]{background:#FFFFFF !important;gap:4px !important;}
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) [data-testid="stMarkdownContainer"]{background:#FFFFFF !important;}
    /* Transparant voor stMarkdownContainer BINNEN knoppen (fix witte blokken) */
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) [data-testid="stBaseButton-secondary"] [data-testid="stMarkdownContainer"]{background:transparent !important;}
    /* Acties: Status opslaan (donker + floppy-icoon) */
    [data-testid="stElementContainer"]:has(.pj-act-save-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]{background:#081A36 !important;color:white !important;border:none !important;border-radius:10px !important;font-weight:600 !important;font-size:13px !important;}
    [data-testid="stElementContainer"]:has(.pj-act-save-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]:hover{background:#041124 !important;}
    [data-testid="stElementContainer"]:has(.pj-act-save-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]::before{font-family:"bootstrap-icons";content:"\\f7d7";margin-right:7px;font-size:14px;vertical-align:-0.1em;font-style:normal;font-weight:400;}
    /* Acties: PDF genereren (blauw rand + document-icoon) */
    [data-testid="stElementContainer"]:has(.pj-act-pdf-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]{background:white !important;color:#2563EB !important;border:1.5px solid #BFDBFE !important;border-radius:10px !important;font-weight:600 !important;font-size:13px !important;}
    [data-testid="stElementContainer"]:has(.pj-act-pdf-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]:hover{background:#EFF6FF !important;}
    [data-testid="stElementContainer"]:has(.pj-act-pdf-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]::before{font-family:"bootstrap-icons";content:"\\f38a";margin-right:7px;font-size:14px;vertical-align:-0.1em;font-style:normal;font-weight:400;}
    /* Acties: Offerte + Factuur (naast elkaar, blauwe rand, op één regel) */
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) [data-testid="stDownloadButton"] button{background:white !important;color:#2563EB !important;border:1.5px solid #BFDBFE !important;border-radius:10px !important;font-weight:600 !important;font-size:13px !important;white-space:nowrap !important;min-width:0 !important;}
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) [data-testid="stDownloadButton"] button:hover{background:#EFF6FF !important;}
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) [data-testid="stDownloadButton"] button div,[data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) [data-testid="stDownloadButton"] button p{white-space:nowrap !important;overflow:visible !important;}
    [data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) div[data-testid="stHorizontalBlock"]:has(.pj-act-dl-mk),[data-testid="stLayoutWrapper"]:has(>[data-testid="stVerticalBlock"]):has(.pj-act-mk) div[data-testid="stHorizontalBlock"]{gap:6px !important;}
    /* Verwijderen (rode rand + trash-icoon) — gedeelde centrale stijl: projectacties + onderdeel-bevestiging */
    [data-testid="stElementContainer"]:has(.pj-act-del-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"],
    [data-testid="stElementContainer"]:has(span.pd-ond-confirm-del-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]{background:white !important;color:#DC2626 !important;border:1.5px solid #FEE2E2 !important;border-radius:10px !important;font-weight:600 !important;font-size:13px !important;}
    [data-testid="stElementContainer"]:has(.pj-act-del-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]:hover,
    [data-testid="stElementContainer"]:has(span.pd-ond-confirm-del-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]:hover{background:#FFF5F5 !important;border-color:#FECACA !important;}
    [data-testid="stElementContainer"]:has(.pj-act-del-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]::before,
    [data-testid="stElementContainer"]:has(span.pd-ond-confirm-del-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]::before{font-family:"bootstrap-icons";content:"\\f5de";margin-right:7px;font-size:14px;vertical-align:-0.1em;font-style:normal;font-weight:400;}
    /* Terug-knop (donker) */
    [data-testid="stElementContainer"]:has(.pj-terug-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]{background:#081A36 !important;color:white !important;border:none !important;border-radius:10px !important;font-weight:600 !important;font-size:13px !important;}
    [data-testid="stElementContainer"]:has(.pj-terug-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]:hover{background:#041124 !important;}
    /* ── Onderdeel toevoegen: reactieve container-kaart — wit, geen rand ── */
    [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] span.pj-ond-mk){background:#FFFFFF !important;border:none !important;box-shadow:none !important;border-radius:18px !important;}
    [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] span.pj-ond-mk) [data-testid="stVerticalBlock"],
    [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] span.pj-ond-mk) [data-testid="stMarkdownContainer"],
    [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] span.pj-ond-mk) [data-testid="stHorizontalBlock"]{background:#FFFFFF !important;border:none !important;box-shadow:none !important;}
    /* FIX wit blok IN de 'Toon alle toeslagen' expander-knop: het label-container mag
       niet wit gemaakt worden door de regel hierboven — transparant houden. */
    [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] span.pj-ond-mk) [data-testid="stExpander"] [data-testid="stMarkdownContainer"]{background:transparent !important;}
    /* stColumn krijgt GEEN witte achtergrond — transparant laten zodat de multiselect-dropdown
       niet wordt afgedekt door de achterliggende witte achtergrond van een zusterkolom (oc3) */
    [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] span.pj-ond-mk) [data-testid="stColumn"]{border:none !important;box-shadow:none !important;}
    /* Toevoegen-knop: outline stijl (marker + volgende container) */
    [data-testid="stElementContainer"]:has(span.pj-ond-add-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]{background:white !important;color:#0F172A !important;border:1.5px solid #E2E8F0 !important;border-radius:10px !important;font-weight:600 !important;}
    [data-testid="stElementContainer"]:has(span.pj-ond-add-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]:hover{background:#F8FAFC !important;border-color:#CBD5E1 !important;}
    [data-testid="stElementContainer"]:has(span.pj-ond-add-mk)+[data-testid="stElementContainer"] [data-testid="stMarkdownContainer"]{background:transparent !important;border:none !important;}
    /* ── Onderdeel verwijderen via long-press ── */
    .pd-table tbody tr.pd-ond-row{cursor:pointer;user-select:none;-webkit-user-select:none;}
    @keyframes pdHoldFill{from{background-size:0% 100%;}to{background-size:100% 100%;}}
    .pd-table tbody tr.pd-ond-pressing td{
        background-image:linear-gradient(90deg,#FECACA,#FCA5A5) !important;
        background-repeat:no-repeat !important;background-position:left center !important;
        background-size:0% 100% !important;animation:pdHoldFill 0.62s linear forwards;}
    .pd-ond-hint{font-size:11.5px;color:#94A3B8;margin:-4px 0 14px 2px;display:flex;align-items:center;}
    .pd-ond-confirm{background:#FFF5F5;border:1px solid #FECACA;border-radius:12px;padding:13px 16px;margin-bottom:12px;}
    .pd-ond-confirm-txt{font-size:13px;color:#7F1D1D;font-weight:500;display:flex;align-items:center;}
    /* Bevestig-knop "Verwijderen" — gebruikt de gedeelde verwijder-stijl hierboven (pd-ond-confirm-del-mk staat in de gedeelde selector) */
    /* Bevestig-knop "Annuleren" (licht/neutraal) — marker + volgende container */
    [data-testid="stElementContainer"]:has(span.pd-ond-confirm-no-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]{background:white !important;color:#475569 !important;border:1px solid #E2E8F0 !important;border-radius:9px !important;font-weight:600 !important;font-size:13px !important;white-space:nowrap !important;}
    [data-testid="stElementContainer"]:has(span.pd-ond-confirm-no-mk)+[data-testid="stElementContainer"] [data-testid="stBaseButton-secondary"]:hover{background:#F8FAFC !important;border-color:#CBD5E1 !important;}
    /* Verberg de verborgen long-press trigger-knoppen (alleen de directe container) */
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] span.pd-lptrig-mk){position:absolute !important;left:-9999px !important;width:1px !important;height:1px !important;overflow:hidden !important;opacity:0 !important;pointer-events:none !important;}
    """)

    tab1, tab2 = st.tabs(["Overzicht", "+ Nieuw project"])

    # UX: na het aanmaken van een project automatisch terug naar de Overzicht-tab, zodat
    # het nieuwe project direct zichtbaar is (st.tabs kent geen programmatische selectie →
    # klik de Overzicht-tab éénmalig via JS zodra de vlag staat; retry tot 4s tegen hydration).
    if st.session_state.pop("pj_goto_overzicht", False):
        ga_naar_tab("Overzicht")
    # UX: 'Bewerken' (3-puntjes) springt naar de '+ Nieuw project'-tab, die dan voorgevuld
    # het project bewerkt (zelfde JS-tabklik-truc).
    if st.session_state.pop("pj_goto_nieuw", False):
        ga_naar_tab("+ Nieuw project")

    with tab1:
        st.markdown('<div style="margin-bottom:20px;"><div class="pj-page-title">Projecten</div><div class="pj-page-sub">Beheer alle lopende, afgeronde en geplande projecten.</div></div>', unsafe_allow_html=True)

        # ── Toolbar ──
        tf1, tf2, tf3 = st.columns([5, 2.5, 2.5])
        with tf1:
            pj_zoek = st.text_input("Zoeken", placeholder="Zoek project, klant of adres…", key="pj_zoek")
        with tf2:
            pj_status = st.selectbox("Status", ["Alle statussen"] + list(STATUS_KLEUREN.keys()), key="pj_status")
        with tf3:
            # SP-012: standaard sortering volgt Instellingen → Voorkeuren
            _pj_sort_opts = ["Nieuwste eerst","Oudste eerst","Hoogste waarde","Laagste waarde","Naam A–Z","Naam Z–A"]
            _std_sort_raw = st.session_state.instellingen.get("std_sorteervolgorde", "Nieuwste eerst")
            _std_sort = {"Hoogste bedrag": "Hoogste waarde", "Naam A-Z": "Naam A–Z", "Naam Z-A": "Naam Z–A"}.get(_std_sort_raw, _std_sort_raw)
            pj_sort = st.selectbox("Sorteren op", _pj_sort_opts,
                                   index=_pj_sort_opts.index(_std_sort) if _std_sort in _pj_sort_opts else 0,
                                   key="pj_sort")

        st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

        # ── Filter & sort ──
        gefilterd = list(reversed(st.session_state.projecten))
        if pj_zoek:
            _pjq = pj_zoek.lower()
            gefilterd = [p for p in gefilterd if _pjq in p["naam"].lower() or _pjq in get_klant_naam(p["klant_id"]).lower() or _pjq in p.get("adres","").lower()]
        if pj_status != "Alle statussen":
            gefilterd = [p for p in gefilterd if p["status"] == pj_status]
        if pj_sort == "Oudste eerst":
            gefilterd = list(reversed(gefilterd))
        elif pj_sort == "Hoogste waarde":
            gefilterd = sorted(gefilterd, key=lambda p: bereken_project_totaal(p)["excl_btw"], reverse=True)
        elif pj_sort == "Laagste waarde":
            gefilterd = sorted(gefilterd, key=lambda p: bereken_project_totaal(p)["excl_btw"])
        elif pj_sort == "Naam A–Z":
            gefilterd = sorted(gefilterd, key=lambda p: p["naam"].lower())
        elif pj_sort == "Naam Z–A":
            gefilterd = sorted(gefilterd, key=lambda p: p["naam"].lower(), reverse=True)

        # ── Paginatie state ──
        _PJ_PER_PG = 8
        _pj_totpg  = max(1, (len(gefilterd) + _PJ_PER_PG - 1) // _PJ_PER_PG)
        if "pj_pagina" not in st.session_state:
            st.session_state.pj_pagina = 1
        _pj_curr_flt = (pj_zoek, pj_status, pj_sort)
        if st.session_state.get("pj_prev_flt") != _pj_curr_flt:
            st.session_state.pj_pagina = 1
            st.session_state.pj_prev_flt = _pj_curr_flt
        _pj_pg   = min(st.session_state.pj_pagina, _pj_totpg)
        _pj_slice = gefilterd[(_pj_pg - 1) * _PJ_PER_PG : _pj_pg * _PJ_PER_PG]

        PJ_BADGE_MAP = {
            "Concept":           ("concept",     "Concept"),
            "Offerte verzonden": ("verzonden",    "Offerte verzonden"),
            "Geaccepteerd":      ("geaccepteerd", "Geaccepteerd"),
            "In uitvoering":     ("uitvoering",   "In uitvoering"),
            "Afgerond":          ("afgerond",     "Afgerond"),
            "Geannuleerd":       ("geannuleerd",  "Geannuleerd"),
        }
        # Kleurenpalette + iconen voor projectkaart-placeholder
        _PJ_KLEUREN = [
            ("#2563EB","#EFF6FF"), ("#059669","#ECFDF5"), ("#7C3AED","#F5F3FF"),
            ("#D97706","#FFFBEB"), ("#0891B2","#ECFEFF"), ("#DC2626","#FEF2F2"),
        ]
        _PJ_ICONEN = ["house-door","building","buildings","palette","paint-bucket","hammer"]

        if not gefilterd:
            st.markdown('<div class="pj-empty"><div style="font-size:40px;margin-bottom:10px;">📁</div><div style="font-size:15px;font-weight:700;color:#0F172A;margin-bottom:5px;">Geen projecten gevonden</div><div style="font-size:12.5px;color:#94A3B8;">Pas je filters aan of maak een nieuw project aan.</div></div>', unsafe_allow_html=True)
        else:
            _cnt_col, _exp_col = st.columns([10.3, 2.2])
            with _cnt_col:
                st.markdown(
                    f'<div style="font-size:13px;color:#64748B;font-weight:500;padding-top:6px;">'
                    f'{len(gefilterd)} project{"en" if len(gefilterd)!=1 else ""}</div>',
                    unsafe_allow_html=True,
                )
            with _exp_col:
                st.markdown('<span class="cf-ico-mk cf-ico-export-mk"></span>', unsafe_allow_html=True)
                st.download_button(
                    "Exporteren",
                    data=json.dumps(gefilterd, ensure_ascii=False, indent=2),
                    file_name=f"projecten_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
                    mime="application/json",
                    key="pj_export",
                    use_container_width=True,
                )

            for project in _pj_slice:
                calc       = bereken_project_totaal(project)
                klant_naam = get_klant_naam(project["klant_id"])
                badge_cls, badge_lbl = PJ_BADGE_MAP.get(project["status"], ("concept", project["status"]))
                aangemaakt = project.get("aangemaakt", "")
                n_ond      = len(project["onderdelen"])
                _pj_fg, _pj_bg = _PJ_KLEUREN[project["id"] % len(_PJ_KLEUREN)]
                _pj_ico        = _PJ_ICONEN[project["id"] % len(_PJ_ICONEN)]

                with st.container(border=True):
                    c_info, c_price, c_date, c_act = st.columns([6.3, 2.2, 1.8, 2.2])

                    with c_info:
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:14px;padding:10px 0;">'
                            f'<div style="width:72px;min-width:72px;height:72px;border-radius:12px;background:{_pj_bg};'
                            f'display:flex;align-items:center;justify-content:center;flex-shrink:0;">'
                            f'<i class="bi bi-{_pj_ico}" style="font-size:26px;color:{_pj_fg};"></i></div>'
                            f'<div style="min-width:0;">'
                            f'<div class="pj-name">{h(project["naam"])}</div>'
                            f'<div class="pj-client"><i class="bi bi-person" style="color:#CBD5E1;margin-right:3px;font-size:11px;"></i>{h(klant_naam)}</div>'
                            f'<div class="pj-addr"><i class="bi bi-geo-alt" style="color:#CBD5E1;margin-right:3px;font-size:11px;"></i>{h(project["adres"])}</div>'
                            f'<div style="margin-top:5px;display:flex;gap:5px;flex-wrap:wrap;align-items:center;">'
                            f'<span class="pj-badge {badge_cls}"><span class="pj-badge-dot"></span>{badge_lbl}</span>'
                            f'<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;background:#F1F5F9;border-radius:99px;font-size:10.5px;font-weight:500;color:#64748B;">'
                            f'<i class="bi bi-layers" style="font-size:10px;"></i>{n_ond} onderdeel{"" if n_ond==1 else "delen"}</span>'
                            f'</div>'
                            f'</div>'
                            f'</div>',
                            unsafe_allow_html=True)

                    with c_price:
                        st.markdown(
                            f'<div style="padding-top:2px;">'
                            f'<div class="pj-date-lbl">Totaalbedrag</div>'
                            f'<div class="pj-price">{format_eur(calc["excl_btw"])}</div>'
                            f'<div class="pj-price-sub">incl. BTW: {format_eur(calc["incl_btw"])}</div>'
                            f'</div>',
                            unsafe_allow_html=True)

                    with c_date:
                        st.markdown(
                            f'<div style="padding-top:2px;">'
                            f'<div class="pj-date-lbl">Aangemaakt</div>'
                            f'<div class="pj-date-val">{h(format_datum(aangemaakt)) if aangemaakt else "—"}</div>'
                            f'</div>',
                            unsafe_allow_html=True)

                    with c_act:
                        c_view, c_dots = st.columns([5, 1])
                        with c_view:
                            if st.button("Bekijken", key=f"pj_view_{project['id']}", use_container_width=True):
                                st.session_state.geselecteerd_project = project["id"]
                                st.session_state.pj_edit_in_form = None   # view-modus, niet bewerken
                                _wis_pdf_downloadknoppen(project["id"])   # start ingeklapt
                                st.rerun()
                        with c_dots:
                            with st.popover("⋮", use_container_width=False):
                                if st.button("✏  Bewerken", key=f"pj_edit_{project['id']}", use_container_width=True):
                                    # Bewerken opent het '+ Nieuw project'-formulier voorgevuld (geen inline-uitklap).
                                    st.session_state.geselecteerd_project = None
                                    st.session_state.pj_edit_in_form = project["id"]
                                    st.session_state.pj_goto_nieuw = True
                                    st.rerun()
                                if st.button("⊞  Dupliceren", key=f"pj_dup_{project['id']}", use_container_width=True):
                                    import copy as _copy
                                    _dup = _copy.deepcopy(project)
                                    _dup["id"]         = st.session_state.volgende_project_id
                                    _dup["naam"]       = f"Kopie — {_dup['naam']}"
                                    _dup["aangemaakt"] = str(date.today())
                                    st.session_state.projecten.append(_dup)
                                    st.session_state.volgende_project_id += 1
                                    save_data()
                                    ui_alert("Project gedupliceerd!")
                                    st.rerun()
                                st.markdown('<hr style="margin:4px 0;border:none;border-top:1px solid #F1F5F9;"><span class="pj-del-mk" style="display:none;"></span>', unsafe_allow_html=True)
                                if st.button("🗑  Verwijderen", key=f"pj_del_{project['id']}", use_container_width=True):
                                    st.session_state.projecten = [p for p in st.session_state.projecten if p["id"] != project["id"]]
                                    if st.session_state.geselecteerd_project == project["id"]:
                                        st.session_state.geselecteerd_project = None
                                    prune_personeel_projectkoppelingen()   # SP-005
                                    save_data()
                                    ui_alert("Project verwijderd.")
                                    st.rerun()

            # JS: popover compact + chevron verbergen
            _html_component("""<script>(function(){
var p=window.parent.document;
/* Verberg expand_more chevron in ALLE popover-knoppen */
function hideChevrons(){
    p.querySelectorAll('[data-testid="stPopoverButton"]').forEach(function(btn){
        var ico=btn.querySelector('[data-testid="stIconMaterial"]');
        if(ico) ico.style.setProperty('display','none','important');
    });
}
hideChevrons();
[100,300,700].forEach(function(t){setTimeout(hideChevrons,t);});
/* Popover body compact wanneer geopend */
if(!p._pjPopWatching){
    p._pjPopWatching=true;
    new MutationObserver(function(){
        p.querySelectorAll('[data-testid="stPopoverBody"]').forEach(function(pb){
            if(!pb._pjStyled){
                pb._pjStyled=true;
                pb.style.setProperty('min-width','130px','important');
                pb.style.setProperty('max-width','155px','important');
                pb.style.setProperty('padding','6px','important');
                pb.querySelectorAll('[data-testid="stButton"]>button').forEach(function(b){
                    b.style.setProperty('padding','5px 8px','important');
                    b.style.setProperty('font-size','12px','important');
                });
            }
        });
    }).observe(p.body,{childList:true,subtree:true});
}
})();</script>""", height=0, scrolling=False)

            # ── Paginatie ──
            if _pj_totpg > 1:
                _ppc = st.columns(_pj_totpg + 2)
                with _ppc[0]:
                    if st.button("← Vorige", key="pj_prev", disabled=(_pj_pg <= 1), use_container_width=True):
                        st.session_state.pj_pagina = _pj_pg - 1
                        st.rerun()
                for _pi in range(_pj_totpg):
                    with _ppc[_pi + 1]:
                        if st.button(str(_pi + 1), key=f"pj_pg_{_pi}",
                                     type="primary" if _pj_pg == _pi + 1 else "secondary",
                                     use_container_width=True):
                            st.session_state.pj_pagina = _pi + 1
                            st.rerun()
                with _ppc[_pj_totpg + 1]:
                    if st.button("Volgende →", key="pj_next", disabled=(_pj_pg >= _pj_totpg), use_container_width=True):
                        st.session_state.pj_pagina = _pj_pg + 1
                        st.rerun()

        # ── Detail view ──
        if st.session_state.geselecteerd_project:
            project_idx = next((i for i, p in enumerate(st.session_state.projecten) if p["id"] == st.session_state.geselecteerd_project), None)
            if project_idx is not None:
                project = st.session_state.projecten[project_idx]
                marge = project.get("marge", st.session_state.instellingen["standaard_marge"])
                btw   = project.get("btw",   st.session_state.instellingen["standaard_btw"])
                badge_cls2, badge_lbl2 = PJ_BADGE_MAP.get(project["status"], ("concept", project["status"]))

                # ── Datum formattering: "2025-01-05" → "5 januari 2025" ──
                _MAANDEN_NL = {1:"januari",2:"februari",3:"maart",4:"april",5:"mei",6:"juni",
                               7:"juli",8:"augustus",9:"september",10:"oktober",11:"november",12:"december"}
                try:
                    _dd = date.fromisoformat(str(project["aangemaakt"]))
                    _datum_nl = f"{_dd.day} {_MAANDEN_NL[_dd.month]} {_dd.year}"
                except Exception:
                    _datum_nl = str(project["aangemaakt"])

                st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

                # ── Header card (geel folder-icoon) ──
                st.markdown(
                    f'<div class="pd-card" style="display:flex;align-items:center;justify-content:space-between;gap:16px;">'
                    f'<div style="display:flex;align-items:center;gap:14px;">'
                    f'<div style="width:48px;height:48px;border-radius:12px;background:#FEF9C3;display:flex;align-items:center;justify-content:center;flex-shrink:0;">'
                    f'<i class="bi bi-folder-fill" style="font-size:22px;color:#F59E0B;"></i></div>'
                    f'<div>'
                    f'<div style="font-size:22px;font-weight:800;color:#0F172A;letter-spacing:-0.4px;line-height:1.2;">{h(project["naam"])}</div>'
                    f'<div style="font-size:12.5px;color:#94A3B8;margin-top:3px;">{h(get_klant_naam(project["klant_id"]))} · {h(project["adres"])}</div>'
                    f'</div></div>'
                    f'<span class="pj-badge {badge_cls2}" style="flex-shrink:0;font-size:12.5px;padding:5px 14px;">'
                    f'<span class="pj-badge-dot"></span>{badge_lbl2}</span>'
                    f'</div>',
                    unsafe_allow_html=True)

                # ── Two-column layout: main content + acties sidebar ──
                col_main, col_actions = st.columns([5, 2])

                with col_actions:
                    with st.container(border=True):
                        st.markdown('<span class="pj-act-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        st.markdown(
                            '<div class="pd-sec-head">'
                            '<div class="pd-sec-icon-box">'
                            '<i class="bi bi-lightning-charge" style="font-size:15px;color:#2563EB;"></i>'
                            '</div>'
                            '<span class="pd-sec-title">Acties</span>'
                            '</div>',
                            unsafe_allow_html=True)
                        _status_keys = list(STATUS_KLEUREN.keys())
                        _huidig_idx  = _status_keys.index(project["status"]) if project["status"] in _status_keys else 0
                        nieuwe_status = st.selectbox("Status", _status_keys, index=_huidig_idx, key=f"status_{project['id']}")
                        st.markdown('<span class="pj-act-save-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Status opslaan", key=f"pj_st_save_{project['id']}", use_container_width=True):
                            st.session_state.projecten[project_idx]["status"] = nieuwe_status
                            # SP-008: bij (her)uitgifte de actuele prijzen bevriezen
                            if nieuwe_status in FROZEN_STATUSSEN:
                                maak_prijs_snapshot(st.session_state.projecten[project_idx])
                            save_data()
                            ui_alert("Status bijgewerkt!")
                            st.rerun()
                        st.markdown('<span class="pj-act-pdf-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        _pid = project["id"]
                        if st.button("PDF genereren", key=f"pj_pdf_{_pid}", use_container_width=True):
                            try:
                                # SP-008: prijzen bevriezen vóór het genereren (offerte én factuur)
                                if verzeker_prijs_snapshot(project):
                                    save_data()
                                # Permanent factuurnummer toekennen en beide PDF's vooraf klaarzetten,
                                # zodat de Factuur-knop hieronder een directe download-knop kan zijn.
                                verzeker_factuur_nummer(project)
                                save_data()
                                with open(maak_offerte_pdf(project), "rb") as _fh:
                                    st.session_state[f"_off_bytes_{_pid}"] = _fh.read()
                                with open(maak_factuur_pdf(project), "rb") as _fh:
                                    st.session_state[f"_fact_bytes_{_pid}"] = _fh.read()
                            except Exception as e:
                                ui_alert(f"PDF fout: {e}", "error")
                        # Na 'PDF genereren': Offerte + Factuur naast elkaar; beide downloaden direct.
                        if st.session_state.get(f"_off_bytes_{_pid}") and st.session_state.get(f"_fact_bytes_{_pid}"):
                            st.markdown('<span class="pj-act-dl-mk" style="display:none;"></span>', unsafe_allow_html=True)
                            _dlc1, _dlc2 = st.columns(2)
                            with _dlc1:
                                st.download_button("Offerte", data=st.session_state[f"_off_bytes_{_pid}"],
                                                   file_name=f"offerte_{_pid:04d}.pdf", mime="application/pdf",
                                                   key=f"pj_dl_off_{_pid}", use_container_width=True)
                            with _dlc2:
                                _fnaam = (project.get("factuur_nummer") or f"factuur_{_pid:04d}").replace(" ", "")
                                st.download_button("Factuur", data=st.session_state[f"_fact_bytes_{_pid}"],
                                                   file_name=f"{_fnaam}.pdf", mime="application/pdf",
                                                   key=f"pj_dl_fact_{_pid}", use_container_width=True)
                        st.markdown('<span class="pj-act-del-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Verwijderen", key=f"pj_detail_del_{project['id']}", use_container_width=True):
                            st.session_state.projecten.pop(project_idx)
                            st.session_state.geselecteerd_project = None
                            prune_personeel_projectkoppelingen()   # SP-005
                            save_data()
                            st.rerun()

                with col_main:
                    # ── Projectinformatie card ──
                    _notities_val = h(project.get("notities", "")) or '<span style="color:#CBD5E1;">—</span>'
                    st.markdown(
                        f'<div class="pd-card">'
                        f'<div class="pd-sec-head">'
                        f'<div class="pd-sec-icon-box">'
                        f'<i class="bi bi-info-circle" style="font-size:15px;color:#2563EB;"></i>'
                        f'</div>'
                        f'<span class="pd-sec-title">Projectinformatie</span>'
                        f'</div>'
                        f'<div class="pd-info-grid">'
                        f'<div class="pd-info-block"><div class="pd-info-lbl">Klant</div><div class="pd-info-val">{h(get_klant_naam(project["klant_id"]))}</div></div>'
                        f'<div class="pd-info-block"><div class="pd-info-lbl">Adres</div><div class="pd-info-val">{h(project["adres"])}</div></div>'
                        f'<div class="pd-info-block"><div class="pd-info-lbl">Aangemaakt</div><div class="pd-info-val"><i class="bi bi-calendar3" style="font-size:12px;color:#94A3B8;margin-right:5px;vertical-align:0.05em;"></i>{_datum_nl}</div></div>'
                        f'<div class="pd-info-block"><div class="pd-info-lbl">Notities</div><div class="pd-info-val">{_notities_val}</div></div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True)

                    # ── Onderdelen card ──
                    _tabel_rows = ""
                    _ond_calcs = bereken_onderdelen_lijst(project, marge, btw)
                    for _ond_i, (onderdeel, calc_o) in enumerate(zip(project["onderdelen"], _ond_calcs)):
                        wz_chips = "".join(f'<span class="pd-chip-b">{h(w)}</span>' for w in onderdeel.get("werkzaamheden", []))
                        tsl = []
                        if onderdeel.get("toeslag_hoogte"):  tsl.append("Hoogte")
                        if onderdeel.get("toeslag_spoed"):   tsl.append("Spoed")
                        if onderdeel.get("toeslag_buiten"):  tsl.append("Buiten")
                        if onderdeel.get("toeslag_steiger"): tsl.append("Steiger")
                        if onderdeel.get("toeslag_weekend"): tsl.append("Weekend")
                        if onderdeel.get("toeslag_avond"):   tsl.append("Avond")
                        if onderdeel.get("toeslag_winter"):  tsl.append("Winter")
                        if onderdeel.get("toeslag_reis"):    tsl.append("Reis")
                        _toesl = "".join(f'<span class="pd-chip-y">{t}</span>' for t in tsl) if tsl else '<span style="color:#CBD5E1;">—</span>'
                        # Omvang met eenheid achter de waarde: m² voor oppervlaktewerk, m¹ voor
                        # strekkende meter. Dual-unit (Schuren/Gronden) toont beide indien ingevuld.
                        _m2v = float(onderdeel.get("m2", 0) or 0)
                        _mtv = float(onderdeel.get("meters", 0) or 0)
                        if onderdeel_is_meterwerk(onderdeel):
                            # Puur meterwerk (kit/afplak): strekkende meters; lagen n.v.t.
                            _omvang_cell = f'{_mtv:g} m¹'
                            _lagen_cell  = '—'
                        else:
                            _delen = []
                            if _m2v > 0: _delen.append(f'{_m2v:g} m²')
                            if _mtv > 0: _delen.append(f'{_mtv:g} m¹')
                            _omvang_cell = ' + '.join(_delen) if _delen else f'{_m2v:g} m²'
                            _lagen_cell  = f'{onderdeel.get("lagen", 1)}×'
                        _tabel_rows += (
                            f'<tr class="pd-ond-row" data-ond-idx="{_ond_i}">'
                            f'<td class="nm">{h(onderdeel.get("naam", ""))}</td>'
                            f'<td style="color:#475569;font-weight:500;">{_omvang_cell}</td>'
                            f'<td style="color:#475569;font-weight:500;">{_lagen_cell}</td>'
                            f'<td>{wz_chips}</td>'
                            f'<td class="r" style="white-space:nowrap;">{format_eur(calc_o["materiaal"])}</td>'
                            f'<td class="r" style="white-space:nowrap;">{format_eur(calc_o["arbeid"])}</td>'
                            f'<td class="r">{_toesl}</td>'
                            f'<td class="r" style="font-weight:700;white-space:nowrap;">{format_eur(calc_o["excl_btw"])}</td>'
                            f'</tr>'
                        )
                    if not project["onderdelen"]:
                        _tabel_rows = '<tr><td colspan="8" style="text-align:center;color:#94A3B8;padding:28px;">Nog geen onderdelen toegevoegd</td></tr>'
                    st.markdown(
                        f'<div class="pd-card">'
                        f'<div class="pd-sec-head">'
                        f'<div class="pd-sec-icon-box">'
                        f'<i class="bi bi-box-seam" style="font-size:15px;color:#2563EB;"></i>'
                        f'</div>'
                        f'<span class="pd-sec-title">Onderdelen</span>'
                        f'</div>'
                        f'<div class="pd-table-wrap">'
                        f'<table class="pd-table"><thead><tr>'
                        f'<th>Onderdeel</th><th>m²/m¹</th><th>Lagen</th><th>Werkzaamheden</th>'
                        f'<th class="r">Materiaal</th><th class="r">Arbeid</th>'
                        f'<th class="r">Toeslagen</th><th class="r">Totaal excl. BTW</th>'
                        f'</tr></thead><tbody>{_tabel_rows}</tbody></table>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True)

                    # ── Onderdeel verwijderen: long-press → bevestiging → verwijderen ──
                    # De long-press opent alleen de bevestiging; de werkelijke verwijdering
                    # is een expliciete knop, zodat data nooit per ongeluk verloren gaat.
                    _ond_pending = st.session_state.get("ond_del_pending")
                    if (isinstance(_ond_pending, dict)
                            and _ond_pending.get("pid") == project["id"]
                            and 0 <= _ond_pending.get("idx", -1) < len(project["onderdelen"])):
                        _del_idx  = _ond_pending["idx"]
                        _del_naam = project["onderdelen"][_del_idx]["naam"]
                        st.markdown(
                            f'<div class="pd-ond-confirm"><div class="pd-ond-confirm-txt">'
                            f'<i class="bi bi-exclamation-triangle-fill" style="color:#DC2626;margin-right:8px;"></i>'
                            f'Onderdeel <strong>&nbsp;{h(_del_naam)}&nbsp;</strong> definitief verwijderen?'
                            f'</div></div>',
                            unsafe_allow_html=True)
                        _cf1, _cf2, _cf3 = st.columns([2.2, 2.2, 4])
                        with _cf1:
                            st.markdown('<span class="pd-ond-confirm-del-mk" style="display:none;"></span>', unsafe_allow_html=True)
                            if st.button("Verwijderen", key=f"ond_del_yes_{project['id']}", use_container_width=True):
                                st.session_state.projecten[project_idx]["onderdelen"].pop(_del_idx)
                                # SP-008: offerte-inhoud gewijzigd → snapshot verversen (zoals bij toevoegen)
                                verzeker_prijs_snapshot(st.session_state.projecten[project_idx])
                                save_data()
                                st.session_state.ond_del_pending = None
                                ui_alert("Onderdeel verwijderd.")
                                st.rerun()
                        with _cf2:
                            st.markdown('<span class="pd-ond-confirm-no-mk" style="display:none;"></span>', unsafe_allow_html=True)
                            if st.button("Annuleren", key=f"ond_del_no_{project['id']}", use_container_width=True):
                                st.session_state.ond_del_pending = None
                                st.rerun()

                    if project["onderdelen"]:
                        st.markdown(
                            '<div class="pd-ond-hint"><i class="bi bi-hand-index" style="margin-right:6px;"></i>'
                            'Houd een onderdeel ingedrukt om het te verwijderen</div>',
                            unsafe_allow_html=True)
                        # Verborgen trigger-knoppen — één per onderdeel; de long-press JS
                        # klikt de juiste knop, die de bevestiging opent.
                        _lp_box = st.container()
                        with _lp_box:
                            st.markdown('<span class="pd-lptrig-mk" style="display:none;"></span>', unsafe_allow_html=True)
                            for _ti in range(len(project["onderdelen"])):
                                if st.button(f"__lpdel__{_ti}", key=f"ond_lp_{project['id']}_{_ti}"):
                                    st.session_state.ond_del_pending = {"pid": project["id"], "idx": _ti}
                                    st.rerun()
                        # BUG-FIX (beta-blocker): long-press via EVENT-DELEGATIE op het
                        # hoofddocument i.p.v. per-rij listeners. Streamlit hergebruikt de
                        # <tr>-nodes bij een rerun; de oude aanpak markeerde ze met _lpWired
                        # én bond listeners aan de iframe-context. Zodra die iframe na een
                        # verwijdering werd herbouwd, wezen de (op herbruikte rijen behouden)
                        # listeners naar een vernietigde context → long-press werkte daarna
                        # niet meer tot een refresh. Delegatie leest de rij pas op event-tijd,
                        # dus elke huidige rij werkt; bij elke run worden de vorige handlers
                        # netjes verwijderd en een lopende timer gestopt → onbeperkt en direct
                        # achter elkaar verwijderen, zonder refresh of paginawissel.
                        _html_component("""<script>(function(){
var p=window.parent.document;
var THRESHOLD=620;
function findTrigger(idx){
    var btns=p.querySelectorAll('button');
    for(var i=0;i<btns.length;i++){
        if(btns[i].textContent.trim()==='__lpdel__'+idx) return btns[i];
    }
    return null;
}
/* Vorige handlers (van een eerder iframe-leven) verwijderen + lopende timer stoppen,
   zodat herbruikte tabelrijen nooit aan een vernietigde iframe-context blijven hangen. */
if(p._ondLp){
    var o=p._ondLp;
    p.removeEventListener('mousedown',o.down,true);
    p.removeEventListener('touchstart',o.down,true);
    p.removeEventListener('mouseup',o.cancel,true);
    p.removeEventListener('mousemove',o.move,true);
    p.removeEventListener('touchend',o.cancel,true);
    p.removeEventListener('touchcancel',o.cancel,true);
    p.removeEventListener('touchmove',o.cancel,true);
    p.removeEventListener('scroll',o.cancel,true);
    if(o.st){ if(o.st.timer) clearTimeout(o.st.timer); if(o.st.row) o.st.row.classList.remove('pd-ond-pressing'); }
}
var st={timer:null,row:null};
function cancel(){
    if(st.row) st.row.classList.remove('pd-ond-pressing');
    if(st.timer){clearTimeout(st.timer);st.timer=null;}
    st.row=null;
}
function down(ev){
    var row=ev.target&&ev.target.closest?ev.target.closest('tr.pd-ond-row'):null;
    if(!row) return;
    if(ev.type==='mousedown'&&ev.button!==0) return;
    cancel();
    st.row=row; row.classList.add('pd-ond-pressing');
    st.timer=setTimeout(function(){
        row.classList.remove('pd-ond-pressing'); st.timer=null; st.row=null;
        var b=findTrigger(row.getAttribute('data-ond-idx'));
        if(b) b.click();
    },THRESHOLD);
}
function move(ev){
    if(st.row){
        var over=ev.target&&ev.target.closest?ev.target.closest('tr.pd-ond-row'):null;
        if(over!==st.row) cancel();
    }
}
p.addEventListener('mousedown',down,true);
p.addEventListener('touchstart',down,{passive:true,capture:true});
p.addEventListener('mouseup',cancel,true);
p.addEventListener('mousemove',move,true);
p.addEventListener('touchend',cancel,true);
p.addEventListener('touchcancel',cancel,true);
p.addEventListener('touchmove',cancel,true);
p.addEventListener('scroll',cancel,true);
p._ondLp={down:down,cancel:cancel,move:move,st:st};
})();</script>""", height=0, scrolling=False)

                    # ── Totaal card ──
                    totaal = bereken_project_totaal(project)
                    # SP-012: waarschuwing onder minimale projectprijs (Instellingen → Financieel)
                    try:
                        _min_prijs = float(st.session_state.instellingen.get("min_projectprijs", 0) or 0)
                    except (TypeError, ValueError):
                        _min_prijs = 0.0
                    _min_warn = ""
                    if _min_prijs > 0 and project.get("onderdelen") and totaal["excl_btw"] < _min_prijs:
                        _min_warn = (
                            f'<div style="font-size:11.5px;color:#D97706;margin-top:8px;text-align:right;">'
                            f'<i class="bi bi-exclamation-triangle" style="margin-right:4px;"></i>'
                            f'Onder de minimale projectprijs van {format_eur(_min_prijs)}</div>')
                    st.markdown(
                        f'<div class="pd-card" style="padding:18px 28px;">'
                        f'<div style="display:flex;align-items:center;justify-content:space-between;gap:20px;">'
                        f'<span style="font-size:13px;font-weight:600;color:#64748B;">Totaal excl. BTW</span>'
                        f'<div style="text-align:right;">'
                        f'<div class="pd-totaal-val">{format_eur(totaal["excl_btw"])}</div>'
                        f'<div style="font-size:12px;color:#94A3B8;margin-top:5px;">'
                        f'BTW ({btw}%): <strong style="color:#475569;">{format_eur(totaal["btw_bedrag"])}</strong>'
                        f'&nbsp;&nbsp;'
                        f'<span style="color:#0F172A;font-weight:700;">Incl. BTW: {format_eur(totaal["incl_btw"])}</span>'
                        f'</div>'
                        f'{_min_warn}'
                        f'</div>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True)

                    # ── Kosten breakdown analyse ──
                    # Zelfde analyse als op de Calculaties-pagina (render_kosten_breakdown),
                    # gevoed met het snapshot-bewuste projecttotaal zodat de bedragen exact
                    # overeenkomen met "Totaal excl. BTW" hierboven. Standaard ingeklapt.
                    if project.get("onderdelen"):
                        with st.expander("Kosten breakdown analyse", expanded=False):
                            render_kosten_breakdown(totaal, marge, btw)

                    # ── Onderdeel toevoegen — zelfde dynamische 3-koloms workflow als de Calculatiepagina:
                    #    LINKS "wat & wie?"  → naam · werkzaamheden · arbeidsuren
                    #    MIDDEN "hoe groot?" → DYNAMISCHE afmetingen: alleen de velden die bij de gekozen
                    #                          werkzaamheden horen (via de gedeelde dimensie_flags()/lengte_label()).
                    #    RECHTS "toeslagen?" → toeslagen (ongewijzigd).
                    #    De reken-engine, de opgeslagen onderdeel-structuur, de arbeidsuren-logica en de
                    #    validatie blijven ONGEWIJZIGD; verborgen dimensies tellen als effectieve waarde
                    #    (m²=0 / lagen=1 / meters=0) mee — exact zoals de Calculatiepagina.
                    _pid       = project["id"]
                    # Nonce in de keys: na toevoegen verhogen we 'm zodat de widgets
                    # vers (leeg) renderen — keyed widgets resetten niet via pop() alleen.
                    _nonce     = st.session_state.get(f"ond_nonce_{_pid}", 0)
                    _sfx       = f"{_pid}_{_nonce}"
                    _wz_key    = f"ond_wz_{_sfx}"
                    _lagen_key = f"ond_lagen_{_sfx}"
                    _naam_key  = f"ond_naam_{_sfx}"
                    _m2_key    = f"ond_m2_{_sfx}"
                    _meters_key = f"ond_meters_{_sfx}"
                    _th_key, _ts_key, _tb_key = f"ond_th_{_sfx}", f"ond_ts_{_sfx}", f"ond_tb_{_sfx}"
                    _twk_key, _tav_key, _twn_key = f"ond_twk_{_sfx}", f"ond_tav_{_sfx}", f"ond_twn_{_sfx}"
                    _uren_key, _uren_touched_key = f"ond_uren_{_sfx}", f"ond_uren_touched_{_sfx}"
                    _houttype_key, _houttype_waarde_key = f"ond_houttype_{_sfx}", f"ond_houttype_waarde_{_sfx}"
                    _houtwerk_lagen_key = f"ond_houtwerk_lagen_{_sfx}"
                    # Beginwaarden via setdefault (géén value= op de widgets → voorkomt de
                    # "widget met default én Session-State-waarde"-waarschuwing, net als op Calculaties).
                    st.session_state.setdefault(_naam_key, "")
                    st.session_state.setdefault(_m2_key, 20)
                    st.session_state.setdefault(_lagen_key, 2)
                    st.session_state.setdefault(_meters_key, 0)
                    st.session_state.setdefault(_houttype_key, "Kozijnen")
                    st.session_state.setdefault(_houttype_waarde_key, 0.0)
                    st.session_state.setdefault(_houtwerk_lagen_key, HOUTWERK_LAGEN)
                    # Persistentie-lus (identiek aan Calculaties): een tijdelijk verborgen dimensieveld
                    # behoudt zijn waarde over reruns i.p.v. terug te vallen op de default.
                    for _ok in (_naam_key, _m2_key, _lagen_key, _meters_key, _wz_key,
                                _houttype_key, _houttype_waarde_key, _houtwerk_lagen_key,
                                _uren_key, _uren_touched_key, _th_key, _ts_key, _tb_key,
                                _twk_key, _tav_key, _twn_key):
                        if _ok in st.session_state:
                            st.session_state[_ok] = st.session_state[_ok]
                    # Houtwerk-type + hoeveelheid (alleen bij "Houtwerk schilderen"); in de
                    # middenkolom gezet en bij opslaan meegenomen (engine rekent naar oppervlak).
                    _ond_houttype = None
                    _ond_houttype_waarde = 0.0
                    with st.container(border=True):
                        st.markdown('<span class="pj-ond-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        st.markdown(
                            '<div style="display:flex;align-items:center;gap:10px;margin-bottom:20px;">'
                            '<i class="bi bi-plus-circle-fill" style="font-size:20px;color:#2563EB;flex-shrink:0;"></i>'
                            '<div style="font-size:14px;font-weight:700;color:#0F172A;">Onderdeel toevoegen</div>'
                            '</div>',
                            unsafe_allow_html=True)
                        oc1, oc2, oc3 = st.columns(3)
                        # ── LINKS: naam · werkzaamheden · arbeidsuren ──
                        with oc1:
                            st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Naam onderdeel <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                            ond_naam = st.text_input("Naam onderdeel", placeholder="Bijv: Slaapkamer", label_visibility="collapsed", key=_naam_key)
                            st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:14px;">Werkzaamheden <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                            ond_wz    = st.multiselect("Werkzaamheden", WERKZAAMHEDEN_OPTIES,
                                                       placeholder="Kies werkzaamheden…",
                                                       label_visibility="collapsed", key=_wz_key)
                            # Welke dimensievelden horen bij de selectie? (bepaalt de middenkolom) — gedeelde helper
                            _show_kit, _show_afplak, _show_meters, _show_m2, _show_lagen, _show_houtwerk = dimensie_flags(ond_wz)
                            # Effectieve dimensies uit session-state (de middenkolom-widgets renderen pas
                            # daarna); een verborgen dimensie telt niet mee (m²=0 / lagen=1 / meters=0).
                            _eff_m2     = st.session_state.get(_m2_key, 0)     if _show_m2     else 0
                            _eff_lagen  = st.session_state.get(_lagen_key, 1)  if _show_lagen  else 1
                            _eff_meters = st.session_state.get(_meters_key, 0) if _show_meters else 0
                            # Houtwerk: effectief schilderoppervlak (type + hoeveelheid) met 2 lagen,
                            # exact zoals de engine → auto-uren preview klopt met het resultaat.
                            _eff_hout_m2 = (houtwerk_effectief_m2(st.session_state.get(_houttype_key),
                                                                  st.session_state.get(_houttype_waarde_key, 0))
                                            if _show_houtwerk else 0)
                            _uren_m2    = _eff_hout_m2 if _show_houtwerk else _eff_m2
                            _uren_lagen = (st.session_state.get(_houtwerk_lagen_key, HOUTWERK_LAGEN)
                                           if _show_houtwerk else _eff_lagen)
                            # Arbeidsuren: automatisch via centrale productienormen, handmatig te overschrijven.
                            _auto_uren = round(auto_arbeidsuren(ond_wz, _uren_m2, _uren_lagen, _eff_meters, houtwerk_m2=_uren_m2), 1)
                            if not st.session_state.get(_uren_touched_key, False):
                                st.session_state[_uren_key] = float(_auto_uren)
                            st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:14px;">Arbeidsuren <span style="color:#94A3B8;font-weight:400;">· auto, aanpasbaar</span></div>', unsafe_allow_html=True)
                            ond_uren  = st.number_input("Arbeidsuren", min_value=0.0, step=0.5, format="%.1f",
                                                        label_visibility="collapsed", key=_uren_key,
                                                        on_change=_markeer_uren_touched, args=(_uren_touched_key,))
                        # ── MIDDEN: DYNAMISCHE afmetingen — alleen de velden die bij de selectie horen ──
                        with oc2:
                            st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Afmetingen</div>', unsafe_allow_html=True)
                            if not ond_wz:
                                st.markdown(
                                    '<div style="color:#94A3B8;font-size:12.5px;padding:6px 2px 0;line-height:1.5;">'
                                    '<i class="bi bi-arrow-left" style="margin-right:5px;color:#CBD5E1;"></i>'
                                    'Kies eerst werkzaamheden — de benodigde afmetingen verschijnen hier.'
                                    '</div>', unsafe_allow_html=True)
                            else:
                                # Houtwerk: Type houtwerk + typespecifiek maatveld vervangen de
                                # generieke m²/lagen (gedeelde helper; engine rekent naar oppervlak).
                                if _show_houtwerk:
                                    _ond_houttype, _ond_houttype_waarde = render_houttype(_houttype_key, _houttype_waarde_key)
                                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:14px;">Aantal lagen</div>', unsafe_allow_html=True)
                                    st.number_input("Aantal lagen (houtwerk)", min_value=1, max_value=5, label_visibility="collapsed", key=_houtwerk_lagen_key)
                                if _show_m2:
                                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Oppervlakte (m²) <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                                    st.number_input("Oppervlakte (m²)", min_value=0, label_visibility="collapsed", key=_m2_key)
                                if _show_meters:
                                    # Contextlabel op basis van de selectie; één gedeelde meters-waarde.
                                    _lbl = lengte_label(_show_kit, _show_afplak)
                                    st.markdown(f'<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:14px;">{_lbl} <span style="color:#94A3B8;font-weight:400;">· per strekkende meter</span></div>', unsafe_allow_html=True)
                                    st.number_input(_lbl, min_value=0, label_visibility="collapsed", key=_meters_key,
                                                    help="Strekkende meters (per meter berekend): kit-/afplakwerk of het strekkende deel van schuur-/grondwerk.")
                                if _show_lagen:
                                    # Lagen-limiet: max 2 als álle gekozen werkzaamheden Gronden/Afplakken/Kitwerk zijn.
                                    _ond_maxlagen = max_lagen_voor(ond_wz)
                                    if st.session_state.get(_lagen_key, 2) > _ond_maxlagen:
                                        st.session_state[_lagen_key] = _ond_maxlagen
                                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:14px;">Aantal lagen</div>', unsafe_allow_html=True)
                                    st.number_input("Aantal lagen", min_value=1, max_value=_ond_maxlagen, label_visibility="collapsed", key=_lagen_key)
                        # ── RECHTS: toeslagen — alle direct zichtbaar (geen uitklapper) ──
                        with oc3:
                            st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:8px;">Toeslagen</div>', unsafe_allow_html=True)
                            t_hoogte = st.checkbox("Hoogte (>2.5m)", key=_th_key)
                            t_spoed  = st.checkbox("Spoed", key=_ts_key)
                            t_buiten = st.checkbox("Buitenwerk", key=_tb_key)
                            t_weekend = st.checkbox("Weekend", key=_twk_key)
                            t_avond   = st.checkbox("Avond",   key=_tav_key)
                            t_winter  = st.checkbox("Winter",  key=_twn_key)

                        # Effectieve dimensies voor validatie/opslaan — uit de (nu gerenderde) session-state,
                        # zodat een verborgen dimensie niet meetelt. Identiek aan wat de zichtbare velden tonen
                        # en aan de Calculatiepagina.
                        _save_m2     = st.session_state.get(_m2_key, 0)     if _show_m2     else 0
                        _save_lagen  = st.session_state.get(_lagen_key, 1)  if _show_lagen  else 1
                        _save_meters = st.session_state.get(_meters_key, 0) if _show_meters else 0
                        # Houtwerk: type + hoeveelheid gaan mee (engine rekent naar schilderoppervlak).
                        _save_houttype        = st.session_state.get(_houttype_key, "Kozijnen") if _show_houtwerk else None
                        _save_houttype_waarde = st.session_state.get(_houttype_waarde_key, 0.0) if _show_houtwerk else 0.0
                        _save_houtwerk_lagen  = int(st.session_state.get(_houtwerk_lagen_key, HOUTWERK_LAGEN)) if _show_houtwerk else HOUTWERK_LAGEN
                        _save_hout_m2 = houtwerk_effectief_m2(_save_houttype, _save_houttype_waarde) if _show_houtwerk else 0

                        st.markdown('<span class="pj-ond-add-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Onderdeel toevoegen", key=f"ond_add_{_pid}"):
                            # Centrale invoervalidatie (beta-blocker): blokkeer onrealistische /
                            # ontbrekende invoer met duidelijke meldingen. Géén wijziging aan de
                            # calculatielogica — alleen ongeldige invoer wordt tegengehouden.
                            _ond_fout = eerste_validatiefout(
                                valideer_tekst(ond_naam, "Naam onderdeel", min_len=2),
                                valideer_getal(_save_m2, "m2", "Oppervlakte (m²)"),
                                valideer_getal(_save_meters, "meters", "Lengte (m)"),
                                valideer_getal(_save_lagen, "lagen", "Aantal lagen", toestaan_nul=False),
                                valideer_getal(_save_hout_m2, "m2", "Houtwerk schilderoppervlak"),
                                valideer_getal(ond_uren, "uren", "Arbeidsuren"),
                            )
                            if not _ond_fout and not ond_wz:
                                _ond_fout = "Selecteer minimaal één werkzaamheid."
                            if (not _ond_fout and float(_save_m2 or 0) <= 0 and float(_save_meters or 0) <= 0
                                    and float(_save_hout_m2 or 0) <= 0):
                                _ond_fout = "Vul een oppervlakte (m²), lengte (m) óf houtwerk-hoeveelheid groter dan 0 in."
                            if not _ond_fout:
                                # Arbeidsuren-override: alleen vastleggen als de gebruiker de uren
                                # daadwerkelijk afwijkt van de automatische berekening; anders None
                                # (blijft automatisch meeberekenen). Houtwerk → effectief oppervlak + 2 lagen.
                                _auto_final = round(auto_arbeidsuren(
                                    ond_wz,
                                    _save_hout_m2 if _show_houtwerk else _save_m2,
                                    _save_houtwerk_lagen if _show_houtwerk else _save_lagen,
                                    _save_meters,
                                    houtwerk_m2=(_save_hout_m2 if _show_houtwerk else _save_m2)), 1)
                                _uren_override = (float(ond_uren)
                                                  if abs(float(ond_uren) - _auto_final) > 1e-6
                                                  else None)
                                st.session_state.projecten[project_idx]["onderdelen"].append({
                                    "naam": ond_naam, "m2": _save_m2, "lagen": int(_save_lagen),
                                    "meters": _save_meters,
                                    "werkzaamheden": ond_wz,
                                    "toeslag_hoogte": t_hoogte,
                                    "toeslag_spoed":  t_spoed,
                                    "toeslag_buiten": t_buiten,
                                    "toeslag_weekend": t_weekend,
                                    "toeslag_avond":   t_avond,
                                    "toeslag_winter":  t_winter,
                                    "arbeid_uren_override": _uren_override,
                                    # Houtwerk-type + hoeveelheid (alleen bij Houtwerk schilderen;
                                    # anders None/0). De engine rekent dit om naar schilderoppervlak.
                                    "houttype": _save_houttype,
                                    "houttype_waarde": _save_houttype_waarde,
                                    "houtwerk_lagen": _save_houtwerk_lagen,
                                })
                                # SP-008: offerte-inhoud gewijzigd → snapshot verversen
                                verzeker_prijs_snapshot(st.session_state.projecten[project_idx])
                                save_data()
                                # Invoervelden leegmaken: oude keys opruimen + nonce verhogen
                                # zodat de widgets met verse (lege) keys terugkomen.
                                for _k in (_naam_key, _m2_key, _meters_key, _lagen_key, _wz_key,
                                           _houttype_key, _houttype_waarde_key,
                                           _th_key, _ts_key, _tb_key,
                                           _twk_key, _tav_key, _twn_key,
                                           _uren_key, _uren_touched_key):
                                    st.session_state.pop(_k, None)
                                st.session_state[f"ond_nonce_{_pid}"] = _nonce + 1
                                ui_alert("Onderdeel toegevoegd!")
                                st.rerun()
                            else:
                                ui_alert(_ond_fout, "error")

                st.markdown('<span class="pj-terug-mk" style="display:none;"></span>', unsafe_allow_html=True)
                if st.button("← Terug naar overzicht", key="pj_terug"):
                    st.session_state.geselecteerd_project = None
                    st.session_state.ond_del_pending = None   # openstaande bevestiging wissen
                    _wis_pdf_downloadknoppen()                # Offerte/Factuur-knoppen inklappen
                    st.rerun()

    with tab2:
        # Bewerk-modus: koos 'Bewerken' (3-puntjes) een project, dan vult dit formulier zich
        # met dat project en slaat het de wijzigingen op i.p.v. een nieuw project aan te maken.
        _ep_id = st.session_state.get("pj_edit_in_form")
        _ep = next((p for p in st.session_state.projecten if p["id"] == _ep_id), None) if _ep_id else None
        with st.form("nieuw_project_form", clear_on_submit=True):
            st.markdown('<span class="pj-form-mk" style="display:none;"></span>', unsafe_allow_html=True)

            # ── Card header ──
            st.markdown(
                '<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;">'
                '<div style="width:38px;height:38px;border-radius:10px;background:#EFF6FF;display:flex;align-items:center;justify-content:center;flex-shrink:0;">'
                f'<i class="bi bi-{"pencil-square" if _ep else "book"}" style="font-size:18px;color:#2563EB;"></i></div>'
                '<div>'
                f'<div style="font-size:15px;font-weight:700;color:#0F172A;line-height:1.2;">{"Project bewerken" if _ep else "Projectgegevens"}</div>'
                f'<div style="font-size:12px;color:#94A3B8;margin-top:2px;">{"Wijzig de gegevens van dit project" if _ep else "Basis informatie over het project"}</div>'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

            fc1, fc2 = st.columns(2)
            with fc1:
                st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Projectnaam <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                proj_naam = st.text_input("Projectnaam", value=(_ep["naam"] if _ep else ""), placeholder="Bijv: Woonkamer renovatie", label_visibility="collapsed")
                st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Projectadres <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                proj_adres = st.text_input("Projectadres", value=(_ep.get("adres", "") if _ep else ""), placeholder="Straat + huisnummer, stad", label_visibility="collapsed")
            with fc2:
                st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Klant <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                klant_opties = {k["naam"]: k["id"] for k in st.session_state.klanten}
                _klant_namen = list(klant_opties.keys()) if klant_opties else ["-- Geen klanten --"]
                _klant_idx = 0
                if _ep and klant_opties:
                    _ep_kn = next((n for n, i in klant_opties.items() if i == _ep.get("klant_id")), None)
                    if _ep_kn in _klant_namen:
                        _klant_idx = _klant_namen.index(_ep_kn)
                klant_keuze = st.selectbox("Klant", _klant_namen, index=_klant_idx, label_visibility="collapsed")
                # SP-012: standaard status nieuw project volgt Instellingen → Voorkeuren
                _status_opts = list(STATUS_KLEUREN.keys())
                _std_status = st.session_state.instellingen.get("std_project_status", "Concept")
                _status_bron = _ep["status"] if (_ep and _ep.get("status") in _status_opts) else _std_status
                proj_status = st.selectbox("Beginstatus" if not _ep else "Status", _status_opts,
                                           index=_status_opts.index(_status_bron) if _status_bron in _status_opts else 0)

            fc3, fc4 = st.columns(2)
            with fc3:
                _marge_bron = _ep.get("marge") if _ep else None
                if _marge_bron is None:
                    _marge_bron = st.session_state.instellingen["standaard_marge"]
                _marge_def = max(0, min(60, int(_marge_bron)))
                proj_marge = st.slider("Winstmarge %", 0, 60, _marge_def)
            with fc4:
                _btw_cur = (_ep.get("btw") if _ep else None)
                if _btw_cur is None:
                    _btw_cur = st.session_state.instellingen["standaard_btw"]
                proj_btw = st.selectbox("BTW %", [0, 9, 21], index=[0, 9, 21].index(_btw_cur) if _btw_cur in [0, 9, 21] else 2)

            proj_notities = st.text_area("Notities", value=(_ep.get("notities", "") if _ep else ""), placeholder="Extra informatie over dit project...")

            _, _pj_ann_col, _pj_btn_col = st.columns([5, 1.4, 2])
            with _pj_ann_col:
                _pj_annuleren = st.form_submit_button("Annuleren", use_container_width=True)
            with _pj_btn_col:
                submitted = st.form_submit_button(("✓  Wijzigingen opslaan" if _ep else "＋  Project aanmaken"),
                                                  type="primary", use_container_width=True)
            if _pj_annuleren and _ep:
                # Bewerken geannuleerd → terug naar het overzicht zonder wijzigingen.
                st.session_state.pj_edit_in_form = None
                st.session_state["pj_goto_overzicht"] = True
                st.rerun()
            if submitted:
                if proj_naam and proj_adres and klant_opties:
                    if _ep:   # ── BEWERKEN: velden bijwerken; id/onderdelen/medewerkers/aangemaakt behouden ──
                        _ep["naam"]     = proj_naam
                        _ep["klant_id"] = klant_opties[klant_keuze]
                        _ep["adres"]    = proj_adres
                        _ep["status"]   = proj_status
                        _ep["notities"] = proj_notities
                        _ep["marge"]    = proj_marge
                        _ep["btw"]      = proj_btw
                        verzeker_prijs_snapshot(_ep)   # SP-008: bevriezen bij uitgifte-status
                        st.session_state.pj_edit_in_form = None
                        save_data()
                        st.toast(f"Project '{proj_naam}' bijgewerkt!", icon="✅")
                        st.session_state["pj_goto_overzicht"] = True
                        st.rerun()
                    else:     # ── NIEUW project aanmaken ──
                        nieuw = {
                            "id": st.session_state.volgende_project_id,
                            "naam": proj_naam,
                            "klant_id": klant_opties[klant_keuze],
                            "adres": proj_adres,
                            "status": proj_status,
                            "aangemaakt": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                            "onderdelen": [],
                            "medewerkers": [],
                            "notities": proj_notities,
                            "marge": proj_marge,
                            "btw": proj_btw,
                        }
                        st.session_state.projecten.append(nieuw)
                        # SP-008: direct bevriezen als de beginstatus al "uitgebracht" is
                        verzeker_prijs_snapshot(nieuw)
                        st.session_state.volgende_project_id += 1
                        save_data()
                        st.toast(f"Project '{proj_naam}' aangemaakt!", icon="✅")
                        # UX: automatisch terug naar de Overzicht-tab (JS-tabklik na de rerun).
                        st.session_state["pj_goto_overzicht"] = True
                        st.rerun()
                else:
                    ui_alert("Vul alle verplichte velden in en zorg dat er minimaal één klant bestaat.", "error")

# =====================================================
# OFFERTES
# =====================================================

elif selected == "Offertes":

    _inject_page_css("""
    .of-page-title { font-size:26px; font-weight:800; color:#0F172A; letter-spacing:-0.5px; line-height:1.2; }
    .of-page-sub   { font-size:12.5px; color:#94A3B8; font-weight:400; margin-top:3px; }
    .of-stat {
        background:white; border:1px solid #E8EFF5; border-radius:14px;
        padding:18px 20px 16px; box-shadow:0 1px 3px rgba(0,0,0,0.05);
        transition: box-shadow 0.15s ease, transform 0.15s ease;
        position:relative; overflow:hidden;
    }
    .of-stat::before { content:''; position:absolute; top:0; left:0; right:0; height:3px; border-radius:14px 14px 0 0; }
    .of-stat.blue::before   { background:linear-gradient(90deg,#2563EB,#60A5FA); }
    .of-stat.amber::before  { background:linear-gradient(90deg,#D97706,#FBBF24); }
    .of-stat.green::before  { background:linear-gradient(90deg,#059669,#34D399); }
    .of-stat.indigo::before { background:linear-gradient(90deg,#4F46E5,#818CF8); }
    .of-stat:hover { box-shadow:0 5px 16px rgba(0,0,0,0.09); transform:translateY(-2px); }
    .of-stat-icon { width:34px; height:34px; border-radius:9px; font-size:16px; display:flex; align-items:center; justify-content:center; margin-bottom:10px; }
    .of-stat-label { font-size:10px; font-weight:700; color:#94A3B8; text-transform:uppercase; letter-spacing:0.08em; margin-bottom:4px; }
    .of-stat-value { font-size:28px; font-weight:800; letter-spacing:-1px; line-height:1.1; margin-bottom:3px; }
    .of-stat-sub   { font-size:11px; color:#94A3B8; line-height:1.4; }
    .of-badge { display:inline-flex; align-items:center; gap:5px; padding:3px 10px; border-radius:99px; font-size:11px; font-weight:600; white-space:nowrap; }
    .of-badge-dot { width:5px; height:5px; border-radius:99px; flex-shrink:0; }
    .of-badge.concept      { background:#F1F5F9; color:#475569; }
    .of-badge.concept .of-badge-dot      { background:#94A3B8; }
    .of-badge.verzonden    { background:#EDE9FE; color:#5B21B6; }
    .of-badge.verzonden .of-badge-dot    { background:#7C3AED; }
    .of-badge.geaccepteerd { background:#DCFCE7; color:#166534; }
    .of-badge.geaccepteerd .of-badge-dot { background:#16A34A; }
    .of-badge.uitvoering   { background:#FEF3C7; color:#92400E; }
    .of-badge.uitvoering .of-badge-dot   { background:#F59E0B; }
    .of-badge.afgerond     { background:#F0FDF4; color:#166534; }
    .of-badge.afgerond .of-badge-dot     { background:#22C55E; }
    .of-badge.geannuleerd  { background:#FEE2E2; color:#991B1B; }
    .of-badge.geannuleerd .of-badge-dot  { background:#EF4444; }
    .of-bedrag-excl       { font-size:17px; font-weight:800; color:#0F172A; letter-spacing:-0.3px; font-family:'DM Mono',monospace; }
    .of-bedrag-excl-label { font-size:10px; color:#94A3B8; margin-left:2px; font-weight:400; }
    .of-bedrag-incl       { font-size:11px; color:#94A3B8; margin-top:2px; }
    .of-num { display:inline-block; background:#F1F5F9; color:#64748B; font-size:10px; font-weight:700; padding:2px 7px; border-radius:5px; letter-spacing:0.04em; margin-bottom:4px; }
    .of-title { font-size:13.5px; font-weight:700; color:#0F172A; letter-spacing:-0.1px; line-height:1.3; margin-bottom:2px; }
    .of-meta  { font-size:11px; color:#94A3B8; }
    """)

    st.markdown("""
    <div style="margin-bottom:20px;">
      <div class="of-page-title">Facturen &amp; Offertes</div>
      <div class="of-page-sub">Overzicht van al je facturen en offertes</div>
    </div>
    """, unsafe_allow_html=True)

    open_cnt  = sum(1 for p in st.session_state.projecten if p["status"] == "Offerte verzonden")
    uitz_cnt  = sum(1 for p in st.session_state.projecten if p["status"] == "In uitvoering")
    afgr_cnt  = sum(1 for p in st.session_state.projecten if p["status"] == "Afgerond")
    tot_omzet = sum(bereken_project_totaal(p)["excl_btw"] for p in st.session_state.projecten
                    if p["status"] in ["Geaccepteerd","In uitvoering","Afgerond"])

    qs1, qs2, qs3, qs4 = st.columns(4)
    for col, cls, icon, icon_bg, icon_clr, label, val, sub, accent in [
        (qs1,"blue",  "file-text",       "#EFF6FF","#2563EB","Open offertes",  open_cnt,              "Wachten op reactie","#2563EB"),
        (qs2,"amber", "hammer",          "#FFFBEB","#D97706","In uitvoering",  uitz_cnt,              "Actieve projecten", "#D97706"),
        (qs3,"green", "check2-circle",   "#F0FDF4","#059669","Afgerond",       afgr_cnt,              "Projecten klaar",   "#059669"),
        (qs4,"indigo","cash-stack",      "#EEF2FF","#4F46E5","Totale omzet",   format_eur(tot_omzet), "Excl. BTW",         "#4F46E5"),
    ]:
        with col:
            st.markdown(f"""
            <div class="of-stat {cls}">
              <div class="of-stat-icon" style="background:{icon_bg};"><i class="bi bi-{icon}" style="font-size:17px;color:{icon_clr};"></i></div>
              <div class="of-stat-label">{label}</div>
              <div class="of-stat-value" style="color:{accent};">{val}</div>
              <div class="of-stat-sub">{sub}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

    f1, f2, f3 = st.columns([4, 2, 2])
    with f1:
        zoek_of = st.text_input("Zoeken", placeholder="Zoek offerte, klant of project…",
                                key="of_zoek")
    with f2:
        filter_status_of = st.selectbox("Status", ["Alle statussen","Concept","Offerte verzonden",
                                             "Geaccepteerd","In uitvoering","Afgerond","Geannuleerd"],
                                        key="of_status")
    with f3:
        sorteer_of = st.selectbox("Sorteer op", ["Nieuwste eerst","Oudste eerst","Hoogste bedrag","Laagste bedrag"],
                                  key="of_sort")

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    projecten_of = list(reversed(st.session_state.projecten))
    if zoek_of:
        q = zoek_of.lower()
        projecten_of = [p for p in projecten_of
                        if q in p["naam"].lower()
                        or q in get_klant_naam(p["klant_id"]).lower()
                        or q in f"#{p['id']:04d}"]
    if filter_status_of != "Alle statussen":
        projecten_of = [p for p in projecten_of if p["status"] == filter_status_of]
    if sorteer_of == "Oudste eerst":
        projecten_of = list(reversed(projecten_of))
    elif sorteer_of == "Hoogste bedrag":
        projecten_of = sorted(projecten_of, key=lambda p: bereken_project_totaal(p)["excl_btw"], reverse=True)
    elif sorteer_of == "Laagste bedrag":
        projecten_of = sorted(projecten_of, key=lambda p: bereken_project_totaal(p)["excl_btw"])

    BADGE_MAP = {
        "Concept":           ("concept",      "Concept"),
        "Offerte verzonden": ("verzonden",     "Offerte verzonden"),
        "Geaccepteerd":      ("geaccepteerd",  "Geaccepteerd"),
        "In uitvoering":     ("uitvoering",    "In uitvoering"),
        "Afgerond":          ("afgerond",      "Afgerond"),
        "Geannuleerd":       ("geannuleerd",   "Geannuleerd"),
    }

    if not projecten_of:
        st.markdown("""
        <div style="text-align:center;padding:56px 24px;background:white;border:1px solid #E8EFF5;
                    border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04);">
            <div style="font-size:40px;margin-bottom:10px;">📄</div>
            <div style="font-size:15px;font-weight:700;color:#0F172A;margin-bottom:5px;">Geen offertes gevonden</div>
            <div style="font-size:12.5px;color:#94A3B8;">Pas je filters aan of maak eerst een project aan.</div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(f'<div style="font-size:11.5px;color:#94A3B8;margin-bottom:8px;">{len(projecten_of)} offerte{"s" if len(projecten_of)!=1 else ""}</div>', unsafe_allow_html=True)

        for project in projecten_of:
            calc       = bereken_project_totaal(project)
            klant_naam = get_klant_naam(project["klant_id"])
            badge_cls, badge_lbl = BADGE_MAP.get(project["status"], ("concept", project["status"]))
            aangemaakt = project.get("aangemaakt", "")
            pid = project['id']
            fname = f"offerte_{pid:04d}_{project['naam'].replace(' ', '_')}.pdf"

            # PDF bytes ophalen uit cache (alleen herberekend als project gewijzigd is)
            try:
                pdf_data = get_pdf_bytes(project)
                pdf_b64  = pdf_data["b64"]
                # Eén klik → offerte ÉN factuur (beide uit dezelfde projectgegevens). De
                # factuur wordt meegedownload via een verborgen sibling-link die de offerte-
                # link bij klik aanroept. Mislukt de factuur onverhoopt, dan blijft de
                # offerte-download gewoon werken (aparte try).
                try:
                    _fact_b64   = get_factuur_bytes(project)["b64"]
                    _fact_fname = f"factuur_{pid:04d}_{project['naam'].replace(' ', '_')}.pdf"
                except Exception:
                    _fact_b64 = None
                if _fact_b64:
                    # Offerte-link + verborgen factuur-link (of-fact-dl). Eén klik downloadt
                    # beide via de gedelegeerde click-listener onderaan de lijst (Streamlit
                    # strípt inline onclick, dus koppelen we het daar).
                    pdf_link = (
                        f'<a href="data:application/pdf;base64,{pdf_b64}" download="{fname}" '
                        f'class="of-pdf-link">'
                        f'<i class="bi bi-file-earmark-arrow-down" style="font-size:16px;"></i>PDF</a>'
                        f'<a href="data:application/pdf;base64,{_fact_b64}" download="{_fact_fname}" '
                        f'class="of-fact-dl" style="display:none;"></a>'
                    )
                else:
                    pdf_link = (
                        f'<a href="data:application/pdf;base64,{pdf_b64}" '
                        f'download="{fname}" class="of-pdf-link">'
                        f'<i class="bi bi-file-earmark-arrow-down" style="font-size:16px;"></i>'
                        f'PDF</a>'
                    )
            except Exception as e:
                pdf_link = (
                    f'<div class="of-pdf-link" style="color:#DC2626;cursor:default;">'
                    f'<i class="bi bi-exclamation-circle" style="font-size:16px;"></i>'
                    f'Fout</div>'
                )
                ui_alert(f"PDF fout ({pid:04d}): {e}", "error")

            st.markdown(f"""
            <div class="of-card-row-full">
              <div class="of-card-inner">
                <div style="flex:4;min-width:0;">
                  <div class="of-num">#{pid:04d}</div>
                  <div class="of-title">{h(project['naam'])}</div>
                  <div class="of-meta"><i class="bi bi-person" style="color:#94A3B8;margin-right:3px;"></i>{h(klant_naam)}{f" · {h(aangemaakt)}" if aangemaakt else ""}</div>
                </div>
                <div style="flex:2;flex-shrink:0;">
                  <span class="of-badge {badge_cls}">
                    <span class="of-badge-dot"></span>{badge_lbl}
                  </span>
                </div>
                <div style="flex:2.2;flex-shrink:0;">
                  <div><span class="of-bedrag-excl">{format_eur(calc['excl_btw'])}</span>
                  <span class="of-bedrag-excl-label">excl. BTW</span></div>
                  <div class="of-bedrag-incl">incl. BTW: {format_eur(calc['incl_btw'])}</div>
                </div>
              </div>
              {pdf_link}
            </div>
            """, unsafe_allow_html=True)

        # Eén klik op de PDF-knop → offerte ÉN factuur downloaden. Streamlit strípt inline
        # onclick-handlers, dus koppelen we de dubbele download via een gedelegeerde
        # click-listener op het hoofddocument. Bij elke run herbonden (oude eerst weg) →
        # geen dode iframe-closures (zelfde les als de long-press-fix).
        _html_component("""<script>(function(){
var p=window.parent.document;
if(p._ofPdfDual){ p.removeEventListener('click', p._ofPdfDual, true); }
p._ofPdfDual=function(ev){
    var a=(ev.target && ev.target.closest) ? ev.target.closest('a.of-pdf-link') : null;
    if(!a || !a.parentNode) return;
    var f=a.parentNode.querySelector('a.of-fact-dl');
    // SYNCHROON binnen dezelfde klik (user-gesture) → de browser blokkeert de tweede
    // download niet. Een setTimeout brak de gesture, waardoor alleen de offerte kwam.
    if(f){ f.click(); }
};
p.addEventListener('click', p._ofPdfDual, true);
})();</script>""", height=0)

# CALCULATIES
# =====================================================

elif selected == "Calculaties":

    # ── Premium CSS voor calculaties pagina ──
    _inject_page_css("""
    /* ── Multiselect tags: blauw i.p.v. rood ── */
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] {
        background-color: #EFF6FF !important;
        border: 1px solid #BFDBFE !important;
        border-radius: 6px !important;
    }
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] span {
        color: #2563EB !important;
        font-weight: 600 !important;
    }
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] [role="button"],
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] button {
        color: #93C5FD !important;
    }
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] [role="button"]:hover,
    [data-testid="stMultiSelect"] span[data-baseweb="tag"] button:hover {
        color: #2563EB !important;
        background: transparent !important;
    }
    /* ── Slider: blauwe lijn, nummer en thumb ── */
    /* Gevulde lijn (track fill) */
    [data-testid="stSlider"] [role="progressbar"] {
        background: #2563EB !important;
        background-color: #2563EB !important;
    }
    /* Thumb (ronde knop) */
    [data-testid="stSlider"] [role="slider"] {
        background: #2563EB !important;
        background-color: #2563EB !important;
        border-color: #2563EB !important;
        box-shadow: 0 0 0 4px rgba(37,99,235,0.15) !important;
    }
    /* Getal boven de thumb */
    [data-testid="stThumbValue"] {
        color: #2563EB !important;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }
    /* Grijze container transparant (verwijder grijs blok) */
    [data-testid="stSlider"] > div,
    [data-testid="stSlider"] [data-baseweb="slider"],
    [data-testid="stSlider"] [data-baseweb="slider"] > div {
        background: transparent !important;
        box-shadow: none !important;
    }

    /* Parameters card */
    .calc-params-card {
        background: white;
        border: 1px solid #E2E8F0;
        border-radius: 16px;
        padding: 24px 28px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        margin-bottom: 28px;
    }
    .calc-section-title {
        font-size: 16px;
        font-weight: 700;
        color: #0F172A;
        letter-spacing: -0.2px;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    /* Resultaat cards */
    .calc-result-card {
        background: white;
        border: 1px solid #E2E8F0;
        border-radius: 14px;
        padding: 20px 22px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        transition: box-shadow 0.16s ease, transform 0.16s ease;
        height: 100%;
    }
    .calc-result-card:hover {
        box-shadow: 0 6px 18px rgba(0,0,0,0.08);
        transform: translateY(-2px);
    }
    .calc-result-card.totaal {
        background: linear-gradient(135deg, #EFF6FF 0%, #EEF2FF 100%);
        border-color: #BFDBFE;
        box-shadow: 0 0 0 2px rgba(37,99,235,0.18), 0 4px 16px rgba(37,99,235,0.10);
    }
    .calc-result-card.totaal .calc-rc-amount {
        font-size: 32px;
        font-weight: 800;
    }
    .calc-rc-label {
        font-size: 11px;
        font-weight: 700;
        color: #94A3B8;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 10px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .calc-rc-amount {
        font-size: 28px;
        font-weight: 700;
        color: #0F172A;
        letter-spacing: -0.8px;
        line-height: 1.1;
        margin-bottom: 6px;
        font-family: 'DM Mono', monospace;
    }
    .calc-rc-amount.blue { color: #2563EB; }
    .calc-rc-sub {
        font-size: 11.5px;
        color: #94A3B8;
    }
    .calc-rc-sub.blue { color: #3B82F6; font-weight: 500; }
    /* Breakdown-tabel (.calc-breakdown-card / .calc-bd-*) staat nu globaal in _APP_CSS,
       gedeeld met de Project-details via render_kosten_breakdown(). */
    /* Totaal balk */
    .calc-totaal-balk {
        background: linear-gradient(135deg, #081A36 0%, #041124 100%);
        border-radius: 14px;
        padding: 18px 24px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 6px;
        box-shadow: 0 4px 20px rgba(8,26,54,0.2);
    }
    .calc-totaal-label {
        font-size: 14px; font-weight: 500; color: #94A3B8;
    }
    .calc-totaal-amount {
        font-size: 32px; font-weight: 800; color: white;
        font-family: 'DM Mono', monospace; letter-spacing: -0.5px;
    }
    /* Disclaimer */
    .calc-disclaimer {
        font-size: 12px; color: #94A3B8;
        padding: 10px 4px; display: flex; align-items: center; gap: 6px;
    }
    /* Reset knop: blauw (zelfde als Product ophalen) */
    div[data-testid="stColumn"]:has(span.calc-reset-mk) .stButton > button {
        background: #2563EB !important;
        color: white !important;
        border: 1px solid #2563EB !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.calc-reset-mk) .stButton > button:hover {
        background: #1D4ED8 !important;
        border-color: #1D4ED8 !important;
        transform: none !important;
        box-shadow: none !important;
    }
    """)

    # ── Header ──
    h_left, h_right = st.columns([5, 3])
    with h_left:
        st.markdown("""
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">
            <div style="width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,#EFF6FF,#E0E7FF);
                        display:flex;align-items:center;justify-content:center;">
                <i class="bi bi-calculator" style="font-size:20px;color:#4F46E5;"></i>
            </div>
            <div>
                <div style="font-size:26px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;line-height:1.2;">Snelle Calculatie</div>
            </div>
        </div>
        <div style="font-size:13px;color:#64748B;margin-bottom:20px;padding-left:54px;">
            Bereken direct materiaal-, arbeids- en totaalprijzen voor schilderprojecten.
        </div>
        """, unsafe_allow_html=True)
    with h_right:
        st.markdown('<span class="calc-reset-mk" style="display:none;"></span>', unsafe_allow_html=True)
        if st.button("↺ Reset", key="calc_reset", use_container_width=True):
            # Volledige reset naar de EXACTE beginstaat (zelfde waarden als bij het
            # eerste openen). Oorzaak van de oude bug: 'calc_meters' (Lengte) en
            # 'calc_uren' werden niet gereset en m²/marge/BTW kregen afwijkende waarden,
            # waardoor de persistente state (zie de setdefault-lus bovenaan) oude/gedeeltelijke
            # waarden vasthield. Alle invoervelden worden nu expliciet teruggezet.
            _std_marge = st.session_state.instellingen.get("standaard_marge", 25)
            _std_btw   = st.session_state.instellingen.get("standaard_btw", 21)
            st.session_state["calc_m2"]     = 0
            st.session_state["calc_meters"] = 0
            st.session_state["calc_lagen"]  = 1
            st.session_state["calc_wz"]     = []
            st.session_state["calc_houttype"] = "Kozijnen"
            st.session_state["calc_houttype_waarde"] = 0.0
            st.session_state["calc_houtwerk_lagen"] = HOUTWERK_LAGEN
            st.session_state["calc_marge"]  = _std_marge
            st.session_state["calc_btw"]    = _std_btw
            st.session_state["ct_hoogte"]   = False
            st.session_state["ct_spoed"]    = False
            st.session_state["ct_buiten"]   = False
            st.session_state["ct_weekend"]  = False
            st.session_state["ct_avond"]    = False
            st.session_state["ct_winter"]   = False
            # Arbeidsuren expliciet 0 + override loslaten → geen oude uren en geen
            # automatische herberekening (m²=0/lengte=0 geeft auto-uren = 0). Doordat
            # werkzaamheden leeg zijn, verdwijnt ook elk oud resultaat.
            st.session_state["calc_uren"]         = 0.0
            st.session_state["calc_uren_touched"] = False
            st.rerun()

    # ── Witte card: gebruik negatieve margin om card over kolommen te leggen ──
    # De kolommen meten we af en de card wordt er exact over gelegd via CSS

    # Tel hoeveel elementen de card moet omvatten
    # Aanpak: witte achtergrond op de st.columns via een scoped CSS class op de container

    with st.container():
        # Stel de witte achtergrond in via inline style op de container
        # door een unieke marker te gebruiken die we CSS-scopen
        _inject_page_css("""
        /* Witte card om het hele project parameters blok */
        div[data-testid="stVerticalBlock"]:has(span.calc-params-marker) {
            background: white !important;
            border: 1px solid #E8EFF5 !important;
            border-radius: 16px !important;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04) !important;
            padding: 20px 24px !important;
            margin-bottom: 20px !important;
        }
        div[data-testid="stVerticalBlock"]:has(span.calc-params-marker) div[data-testid="stHorizontalBlock"],
        div[data-testid="stVerticalBlock"]:has(span.calc-params-marker) div[data-testid="stColumn"] {
            background: white !important;
        }
        div[data-testid="stVerticalBlock"]:has(span.calc-params-marker) input[type="number"] {
            border: 1.5px solid #CBD5E1 !important;
            border-radius: 10px !important;
            background: white !important;
        }
        div[data-testid="stVerticalBlock"]:has(span.calc-params-marker) [data-baseweb="select"] > div:first-child {
            border: 1.5px solid #CBD5E1 !important;
            border-radius: 10px !important;
            background: white !important;
        }
        """)
        st.markdown("""
        <span class="calc-params-marker" style="display:none;"></span>
        <div style="font-size:15px;font-weight:700;color:#0F172A;margin-bottom:16px;display:flex;align-items:center;gap:8px;"><i class="bi bi-sliders" style="color:#2563EB;"></i> Project parameters</div>
        """, unsafe_allow_html=True)

        # ── Drie kolommen — de natuurlijke workflow van de schilder ──────────────
        # LINKS  = "wat ga ik doen?"  → werkzaamheden + instellingen (winstmarge, uren)
        # MIDDEN = "hoe groot is het?" → DYNAMISCHE afmetingen: alleen de velden die bij
        #          de gekozen werkzaamheden horen (oppervlaktewerk → m²/lagen; kit-/
        #          afplakwerk → lengte, met een contextlabel).
        # RECHTS = "welke instellingen?" → BTW + toeslagen.
        # De reken-engine, de session-state-keys én de resultaten blijven ONGEWIJZIGD;
        # alleen de indeling en het TÓNEN van de dimensievelden veranderen. Verborgen
        # widgets behouden hun waarde via de persistence-lus bovenaan.
        col_l, col_m, col_r = st.columns(3)

        with col_l:
            st.markdown('<div style="font-size:11px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Werkzaamheden</div>', unsafe_allow_html=True)
            calc_wz = st.multiselect("Werkzaamheden", WERKZAAMHEDEN_OPTIES,
                                     placeholder="Kies werkzaamheden…", key="calc_wz",
                                     label_visibility="collapsed")

            # Welke dimensievelden horen bij de selectie? (bepaalt de middenkolom) — gedeelde helper
            _show_kit, _show_afplak, _show_meters, _show_m2, _show_lagen, _show_houtwerk = dimensie_flags(calc_wz)
            # Effectieve dimensies uit session-state (de middenkolom-widgets renderen pas
            # daarna); een verborgen dimensie telt niet mee (m²=0 / lagen=1 / meters=0).
            _eff_m2     = st.session_state.get("calc_m2", 0)     if _show_m2     else 0
            _eff_lagen  = st.session_state.get("calc_lagen", 1)  if _show_lagen  else 1
            _eff_meters = st.session_state.get("calc_meters", 0) if _show_meters else 0
            # Houtwerk: effectief schilderoppervlak (type + hoeveelheid) met 2 lagen — precies
            # zoals de engine het berekent, zodat de auto-uren-preview klopt met het resultaat.
            _eff_hout_m2 = (houtwerk_effectief_m2(st.session_state.get("calc_houttype"),
                                                  st.session_state.get("calc_houttype_waarde", 0))
                            if _show_houtwerk else 0)
            _uren_m2    = _eff_hout_m2 if _show_houtwerk else _eff_m2
            _uren_lagen = (st.session_state.get("calc_houtwerk_lagen", HOUTWERK_LAGEN)
                           if _show_houtwerk else _eff_lagen)

            st.markdown('<div style="font-size:11px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;margin-top:14px;">Instellingen</div>', unsafe_allow_html=True)
            calc_marge = st.slider("Winstmarge %", 0, 60, key="calc_marge")
            _calc_auto_uren = round(auto_arbeidsuren(calc_wz, _uren_m2, _uren_lagen, _eff_meters, houtwerk_m2=_uren_m2), 1)
            if not st.session_state.get("calc_uren_touched", False):
                st.session_state["calc_uren"] = float(_calc_auto_uren)
            calc_uren = st.number_input("Arbeidsuren", min_value=0.0, step=0.5, format="%.1f",
                                        key="calc_uren",
                                        on_change=_markeer_uren_touched, args=("calc_uren_touched",),
                                        help="Automatisch berekend; pas aan voor je eigen ureninschatting.")

        with col_m:
            st.markdown('<div style="font-size:11px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">Afmetingen</div>', unsafe_allow_html=True)
            if not calc_wz:
                st.markdown(
                    '<div style="color:#94A3B8;font-size:12.5px;padding:6px 2px 0;line-height:1.5;">'
                    '<i class="bi bi-arrow-left" style="margin-right:5px;color:#CBD5E1;"></i>'
                    'Kies eerst werkzaamheden — de benodigde afmetingen verschijnen hier.'
                    '</div>', unsafe_allow_html=True)
            else:
                # Houtwerk: Type houtwerk + typespecifiek maatveld vervangen de generieke
                # m²/lagen (gedeelde helper). De engine rekent dit om naar schilderoppervlak.
                if _show_houtwerk:
                    render_houttype("calc_houttype", "calc_houttype_waarde")
                    st.number_input("Aantal lagen", min_value=1, max_value=5, key="calc_houtwerk_lagen")
                if _show_m2:
                    st.number_input("Oppervlakte (m²)", min_value=0, key="calc_m2")
                if _show_meters:
                    # Contextlabel op basis van de selectie; één gedeelde meters-waarde
                    # (calc_meters) → engine, resultaten en session-state ongewijzigd.
                    _lengte_label = lengte_label(_show_kit, _show_afplak)
                    st.number_input(_lengte_label, min_value=0, key="calc_meters",
                                    help="Strekkende meters (per meter berekend): kit-/afplakwerk of het strekkende deel van schuur-/grondwerk.")
                if _show_lagen:
                    # Lagen-limiet: max 2 als álle gekozen werkzaamheden Gronden/Afplakken/Kitwerk zijn.
                    _calc_maxlagen = max_lagen_voor(calc_wz)
                    if st.session_state.get("calc_lagen", 1) > _calc_maxlagen:
                        st.session_state.calc_lagen = _calc_maxlagen
                    st.number_input("Aantal lagen", min_value=1, max_value=_calc_maxlagen, key="calc_lagen")

        with col_r:
            st.markdown('<div style="font-size:11px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;">BTW</div>', unsafe_allow_html=True)
            calc_btw = st.selectbox("BTW %", [0, 9, 21], key="calc_btw")
            st.markdown('<div style="font-size:11px;font-weight:600;color:#94A3B8;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;margin-top:14px;">Toeslagen</div>', unsafe_allow_html=True)
            ct_hoogte = st.checkbox("Hoogte (>2,5m)", key="ct_hoogte")
            ct_spoed  = st.checkbox("Spoed",          key="ct_spoed")
            ct_buiten = st.checkbox("Buitenwerk",     key="ct_buiten")
            with st.expander("Toon alle toeslagen"):
                ct_weekend = st.checkbox("Weekend", key="ct_weekend")
                ct_avond   = st.checkbox("Avond",   key="ct_avond")
                ct_winter  = st.checkbox("Winter",  key="ct_winter")

        # Effectieve dimensies voor de berekening — uit de (nu gerenderde) session-state,
        # zodat een verborgen dimensie niet meetelt. Identiek aan wat de zichtbare velden tonen.
        _eff_m2     = st.session_state.get("calc_m2", 0)     if _show_m2     else 0
        _eff_lagen  = st.session_state.get("calc_lagen", 1)  if _show_lagen  else 1
        _eff_meters = st.session_state.get("calc_meters", 0) if _show_meters else 0
        # Houtwerk: type + hoeveelheid gaan mee de engine in (die rekent naar schilderoppervlak).
        _eff_houttype        = st.session_state.get("calc_houttype", "Kozijnen") if _show_houtwerk else None
        _eff_houttype_waarde = st.session_state.get("calc_houttype_waarde", 0.0) if _show_houtwerk else 0.0
        _eff_houtwerk_lagen  = int(st.session_state.get("calc_houtwerk_lagen", HOUTWERK_LAGEN)) if _show_houtwerk else HOUTWERK_LAGEN
        _eff_hout_m2 = houtwerk_effectief_m2(_eff_houttype, _eff_houttype_waarde) if _show_houtwerk else 0
        _uren_m2    = _eff_hout_m2 if _show_houtwerk else _eff_m2
        _uren_lagen = _eff_houtwerk_lagen if _show_houtwerk else _eff_lagen
        _auto_uren_now = round(auto_arbeidsuren(calc_wz, _uren_m2, _uren_lagen, _eff_meters, houtwerk_m2=_uren_m2), 1)

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    # ── Berekening ── (de engine rekent houtwerk om via het type; de effectieve
    # dimensies passen bij de zichtbare velden.)
    onderdeel_calc = {
        "m2": _eff_m2, "lagen": _eff_lagen, "meters": _eff_meters, "werkzaamheden": calc_wz,
        "houttype": _eff_houttype, "houttype_waarde": _eff_houttype_waarde,
        "houtwerk_lagen": _eff_houtwerk_lagen,
        "toeslag_hoogte": ct_hoogte, "toeslag_spoed": ct_spoed, "toeslag_buiten": ct_buiten,
        "toeslag_weekend": ct_weekend, "toeslag_avond": ct_avond, "toeslag_winter": ct_winter,
        # Arbeidsuren-override: alleen leidend als de gebruiker afwijkt van de auto-berekening.
        "arbeid_uren_override": (
            float(calc_uren) if abs(float(calc_uren) - _auto_uren_now) > 1e-6 else None
        ),
    }
    result = bereken_onderdeel(onderdeel_calc, calc_marge, calc_btw)
    totaal_ref = result["incl_btw"] if result["incl_btw"] > 0 else 1

    # Centrale invoervalidatie (beta-blocker): beperk onrealistische m²/meters/lagen.
    # Geldige invoer verandert niets aan de berekening; alleen onzin wordt geblokkeerd.
    _calc_fout = eerste_validatiefout(
        valideer_getal(_eff_m2, "m2", "Oppervlakte (m²)"),
        valideer_getal(_eff_meters, "meters", "Lengte (m)"),
        valideer_getal(_eff_lagen, "lagen", "Aantal lagen", toestaan_nul=False),
        valideer_getal(_eff_hout_m2, "m2", "Houtwerk schilderoppervlak"),
    )
    if (not _calc_fout and float(_eff_m2 or 0) <= 0 and float(_eff_meters or 0) <= 0
            and float(_eff_hout_m2 or 0) <= 0):
        _calc_fout = "Vul een oppervlakte (m²), lengte (m) óf houtwerk-hoeveelheid groter dan 0 in."

    if calc_wz and _calc_fout:
        ui_alert(_calc_fout, "error")
    elif calc_wz:
        # ── Resultaat cards ──
        st.markdown("""
        <div style="font-size:16px;font-weight:700;color:#0F172A;letter-spacing:-0.2px;margin-bottom:14px;">
            Resultaat
        </div>
        """, unsafe_allow_html=True)

        r1, r2, r3, r4 = st.columns(4)

        mat_pct  = result['materiaal']  / totaal_ref * 100
        arb_pct  = result['arbeid']     / totaal_ref * 100
        toes_pct = result['toeslagen']  / totaal_ref * 100

        with r1:
            st.markdown(f"""
            <div class="calc-result-card">
                <div class="calc-rc-label"><i class="bi bi-droplet" style="margin-right:5px;"></i>Materiaalkosten</div>
                <div class="calc-rc-amount">{format_eur(result['materiaal'])}</div>
                <div class="calc-rc-sub">{mat_pct:.1f}% van totaal</div>
            </div>
            """, unsafe_allow_html=True)
        with r2:
            st.markdown(f"""
            <div class="calc-result-card">
                <div class="calc-rc-label"><i class="bi bi-person-badge" style="margin-right:5px;"></i>Arbeidskosten</div>
                <div class="calc-rc-amount">{format_eur(result['arbeid'])}</div>
                <div class="calc-rc-sub {'blue' if arb_pct > 50 else ''}">{result['uren']} uur &nbsp;·&nbsp; {arb_pct:.1f}% van totaal</div>
            </div>
            """, unsafe_allow_html=True)
        with r3:
            st.markdown(f"""
            <div class="calc-result-card">
                <div class="calc-rc-label"><i class="bi bi-plus-circle" style="margin-right:5px;"></i>Toeslagen</div>
                <div class="calc-rc-amount">{format_eur(result['toeslagen'])}</div>
                <div class="calc-rc-sub">{toes_pct:.1f}% van totaal</div>
            </div>
            """, unsafe_allow_html=True)
        with r4:
            st.markdown(f"""
            <div class="calc-result-card totaal">
                <div class="calc-rc-label"><i class="bi bi-bar-chart-fill" style="margin-right:5px;"></i>Totaal excl. BTW</div>
                <div class="calc-rc-amount blue">{format_eur(result['excl_btw'])}</div>
                <div class="calc-rc-sub blue">incl. BTW: {format_eur(result['incl_btw'])}</div>
            </div>
            """, unsafe_allow_html=True)

        # ── Kostenbreakdown ──
        st.markdown("""
        <div style="font-size:16px;font-weight:700;color:#0F172A;letter-spacing:-0.2px;
                    margin-top:28px;margin-bottom:14px;">
            Kostenbreakdown
        </div>
        """, unsafe_allow_html=True)

        # Gedeelde breakdown — exact dezelfde kaart als op de Project-details.
        render_kosten_breakdown(result, calc_marge, calc_btw)

        # ── Totaal balk ──
        st.markdown(f"""
        <div class="calc-totaal-balk">
            <div>
                <div class="calc-totaal-label">Totaal inclusief BTW</div>
            </div>
            <div class="calc-totaal-amount">{format_eur(result['incl_btw'])}</div>
        </div>
        <div class="calc-disclaimer">
            <i class="bi bi-info-circle" style="font-size:13px;flex-shrink:0;"></i> Deze calculatie is een indicatie. Werkelijke kosten kunnen afwijken.
        </div>
        """, unsafe_allow_html=True)

    else:
        st.markdown("""
        <div style="text-align:center;padding:48px 24px;background:white;border:1px solid #E8EFF5;
                    border-radius:14px;box-shadow:0 1px 3px rgba(0,0,0,0.04);margin-top:8px;">
            <i class="bi bi-calculator" style="font-size:36px;color:#CBD5E1;"></i>
            <div style="font-size:14px;font-weight:600;color:#64748B;margin-top:12px;">Kies werkzaamheden om een calculatie te zien.</div>
        </div>
        """, unsafe_allow_html=True)

# =====================================================
# KLANTEN
# =====================================================

elif selected == "Klanten":

    # Avatar kleur per klant (consistent op basis van id)
    AVATAR_KLEUREN = [
        ("#2563EB", "#DBEAFE"), ("#059669", "#D1FAE5"), ("#7C3AED", "#EDE9FE"),
        ("#DC2626", "#FEE2E2"), ("#D97706", "#FEF3C7"), ("#0891B2", "#CFFAFE"),
    ]

    def get_initialen(naam):
        delen = naam.strip().split()
        if len(delen) >= 2:
            return (delen[0][0] + delen[-1][0]).upper()
        return naam[:2].upper()

    def get_avatar_stijl(klant_id):
        fg, bg = AVATAR_KLEUREN[klant_id % len(AVATAR_KLEUREN)]
        return fg, bg

    ITEMS_PER_PAGINA = 10

    _inject_keyed_css("klanten_page", """
    /* Klant opslaan knop donker blauw */
    div[data-testid="stForm"] div[data-testid="stFormSubmitButton"]:last-child > button[kind="primaryFormSubmit"] {
        background: #081A36 !important;
        border-color: #081A36 !important;
        color: white !important;
    }
    div[data-testid="stForm"] div[data-testid="stFormSubmitButton"]:last-child > button[kind="primaryFormSubmit"]:hover {
        background: #041124 !important;
        border-color: #041124 !important;
    }
    /* Verwijder-bevestiging: Verwijderen (wit, rode rand + trash-icoon) — gelijk aan Producten/Personeel */
    div[data-testid="stColumn"]:has(span.kl-del-confirm-mk) .stButton > button {
        background: white !important; color: #DC2626 !important; border: 1.5px solid #FEE2E2 !important; box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.kl-del-confirm-mk) .stButton > button:hover {
        background: #FFF5F5 !important; border-color: #FECACA !important; transform: none !important; box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.kl-del-confirm-mk) .stButton > button::before {
        font-family:"bootstrap-icons"; content:"\\f5de"; margin-right:7px; font-size:14px; vertical-align:-0.1em; font-style:normal; font-weight:400;
    }
    /* Verwijder-bevestiging: Annuleren wit/neutraal */
    div[data-testid="stColumn"]:has(span.kl-del-cancel-mk) .stButton > button {
        background: white !important; color: #475569 !important; border: 1px solid #E2E8F0 !important; box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.kl-del-cancel-mk) .stButton > button:hover {
        background: #F8FAFC !important; border-color: #CBD5E1 !important; transform: none !important; box-shadow: none !important;
    }
    /* Zoekbalk gelijk hoog als dropdowns */
    div[data-testid="stTextInput"] input {
        height: 38px !important;
        min-height: 38px !important;
        font-size: 14px !important;
        padding: 0 12px !important;
    }
    div[data-testid="stTextInput"] > div {
        height: 38px !important;
    }
    /* Verwijder-bevestiging: Annuleren wit/neutraal */
    div[data-testid="stColumn"]:has(span.kl-del-cancel-mk) .stButton > button {
        background: white !important;
        color: #475569 !important;
        border: 1px solid #E2E8F0 !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.kl-del-cancel-mk) .stButton > button:hover {
        background: #F8FAFC !important;
        border-color: #CBD5E1 !important;
        box-shadow: none !important;
        transform: none !important;
    }
    /* ── Overlay onzichtbare actie-knoppen ── */
    div[data-testid="stLayoutWrapper"]:has(span.kl-ovl-mk){height:0 !important;overflow:visible !important;margin:-16px 0 !important;padding:0 !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk){margin-top:-62px !important;height:62px !important;background:transparent !important;gap:0 !important;position:relative !important;z-index:50 !important;pointer-events:none !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) div[data-testid="stColumn"]{background:transparent !important;padding:0 !important;min-width:0 !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) div[data-testid="stVerticalBlock"]{background:transparent !important;gap:0 !important;height:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) div[data-testid="stElementContainer"]{margin:0 !important;padding:0 !important;height:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) div[data-testid="stMarkdownContainer"]{display:none !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) div[data-testid="stColumn"]:not(:first-child){pointer-events:auto !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) [data-testid="stElementContainer"]{width:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) [data-testid="stButton"]{width:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) button{opacity:0 !important;width:100% !important;height:62px !important;cursor:pointer !important;pointer-events:auto !important;background:transparent !important;border:none !important;box-shadow:none !important;margin:0 !important;padding:0 !important;transform:none !important;}
    /* Acties-klikzones absoluut, vast vanaf de rechterrand → vallen exact over de (rechts uitgelijnde) iconen op elke schermbreedte */
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) > div[data-testid="stColumn"]:nth-child(2){position:absolute !important;top:0 !important;right:72px !important;width:33px !important;min-width:33px !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) > div[data-testid="stColumn"]:nth-child(3){position:absolute !important;top:0 !important;right:39px !important;width:33px !important;min-width:33px !important;}
    div[data-testid="stHorizontalBlock"]:has(span.kl-ovl-mk) > div[data-testid="stColumn"]:nth-child(4){position:absolute !important;top:0 !important;right:6px !important;width:33px !important;min-width:33px !important;}
    /* ── Inline edit formulier ── */
    [data-testid="stForm"]:has(span.kl-inline-edit-mk){background:#FFFFFF !important;border:1px solid #BFDBFE !important;border-radius:14px !important;box-shadow:0 2px 8px rgba(37,99,235,0.07) !important;}
    [data-testid="stForm"]:has(span.kl-inline-edit-mk) [data-testid="stVerticalBlock"],[data-testid="stForm"]:has(span.kl-inline-edit-mk) [data-testid="stHorizontalBlock"],[data-testid="stForm"]:has(span.kl-inline-edit-mk) [data-testid="stMarkdownContainer"],[data-testid="stForm"]:has(span.kl-inline-edit-mk) [data-testid="stColumn"]{background:#FFFFFF !important;}
    [data-testid="stForm"]:has(span.kl-inline-edit-mk) button [data-testid="stMarkdownContainer"]{background:transparent !important;}
    /* ── Nieuwe klant form: grote form-border weg (Streamlit 1.55: stForm heeft zelf de border) ── */
    div[data-testid="stForm"]:has(span.kl-card-m),
    div[data-testid="stForm"]:has(span.kl-card-m) > div[data-testid="stLayoutWrapper"] {
        background: transparent !important;
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
    }
    /* Witte cards — Streamlit 1.55: card = stColumn > stVerticalBlock > stLayoutWrapper > stVerticalBlock */
    div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.kl-card-m) {
        background: #FFFFFF !important;
        border: 1px solid #E8EFF5 !important;
        border-radius: 14px !important;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05) !important;
    }
    /* Descendants ook wit zodat er geen grijze plekken zijn */
    div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.kl-card-m) div[data-testid="stMarkdownContainer"],
    div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.kl-card-m) div[data-testid="stHorizontalBlock"],
    div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.kl-card-m) div[data-testid="stColumn"] {
        background: #FFFFFF !important;
    }
    """)

    # ── Pagina titel — zelfde structuur als Personeel: titel → navigatiebalk (tabs) → inhoud ──
    ht1, ht2 = st.columns([5, 2])
    with ht1:
        st.markdown(
            '<div style="margin-bottom:20px;">'
            '<div style="font-size:26px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;line-height:1.2;">Klanten</div>'
            '<div style="font-size:12.5px;color:#94A3B8;font-weight:400;margin-top:3px;">Beheer al je klanten en contactgegevens.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with ht2:
        pass  # ruimte voor knop in tab

    tab_ovz, tab_nieuw = st.tabs(["Overzicht", "+ Nieuwe klant"])

    # UX: na het toevoegen van een klant automatisch terug naar de Overzicht-tab
    # (zelfde patroon als Personeel). st.tabs kent geen programmatische selectie; we
    # klikken de Overzicht-tab éénmalig via JS (frontend-only) zodra de vlag staat.
    if st.session_state.pop("kl_goto_overzicht", False):
        ga_naar_tab("Overzicht")

    # ──────────────────────────────────────────────────
    # OVERZICHT TAB
    # ──────────────────────────────────────────────────
    with tab_ovz:

        # Succesmelding na toevoegen (flash) — op het overzicht, niet op het gesloten formulier.
        _kl_flash = st.session_state.pop("kl_flash", None)
        if _kl_flash:
            ui_alert(_kl_flash, "success")

        # ── Filters inline zonder card ──
        alle_plaatsen = sorted(set(k.get("stad","") for k in st.session_state.klanten if k.get("stad","")))

        fc1, fc2, fc3, fc4 = st.columns([3, 1.6, 1.6, 1.6])
        with fc1:
            zoek_klant = st.text_input("Zoeken", placeholder="Zoek klant…",
                                       key="klant_zoek_input")
        with fc2:
            filter_status = st.selectbox("Status",
                                         ["Alle statussen", "Actief", "Inactief"],
                                         key="klant_filter_status")
        with fc3:
            filter_plaats = st.selectbox("Plaats",
                                         ["Alle plaatsen"] + alle_plaatsen,
                                         key="klant_filter_plaats")
        with fc4:
            filter_datum = st.date_input("Aangemaakt op", value=None,
                                         key="klant_filter_datum",
                                         format="DD-MM-YYYY")

        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

        # ── Filter logica ──
        klanten_gefilterd = st.session_state.klanten
        if zoek_klant:
            q = zoek_klant.lower()
            klanten_gefilterd = [
                k for k in klanten_gefilterd
                if q in k["naam"].lower()
                or q in k.get("bedrijf", "").lower()
                or q in k.get("email", "").lower()
                or q in k.get("stad", "").lower()
            ]
        if filter_plaats != "Alle plaatsen":
            klanten_gefilterd = [k for k in klanten_gefilterd if k.get("stad","") == filter_plaats]
        if filter_datum:
            filter_datum_str = filter_datum.strftime("%Y-%m-%d")
            klanten_gefilterd = [k for k in klanten_gefilterd
                                 if k.get("aangemaakt","") == filter_datum_str]
        if filter_status != "Alle statussen":
            klanten_gefilterd = [k for k in klanten_gefilterd
                                 if ("Actief" if k.get("actief", True) else "Inactief") == filter_status]

        # ── Sorteren: nieuwste klanten bovenaan ──
        klanten_gefilterd = sorted(klanten_gefilterd, key=lambda k: k.get("id", 0), reverse=True)

        totaal = len(klanten_gefilterd)
        pagina = st.session_state.klanten_pagina
        max_pagina = max(1, (totaal + ITEMS_PER_PAGINA - 1) // ITEMS_PER_PAGINA)
        pagina = min(pagina, max_pagina)
        start = (pagina - 1) * ITEMS_PER_PAGINA
        einde = min(start + ITEMS_PER_PAGINA, totaal)
        pagina_klanten = klanten_gefilterd[start:einde]

        st.markdown(
            f'<div style="font-size:13px;color:#64748B;margin-bottom:10px;font-weight:500;">'
            f'{totaal} klant{"en" if totaal != 1 else ""}</div>',
            unsafe_allow_html=True,
        )

        # ── Tabel ──
        if not klanten_gefilterd:
            ui_alert("Geen klanten gevonden. Pas je zoekopdracht aan of voeg een nieuwe klant toe.", "info")
        else:
            # Header (HTML — ongewijzigd; cf-tbl-head verbergt 'm op mobiel, Fase 4)
            st.markdown(
                '<div class="cf-tbl-head" style="display:flex;padding:9px 12px;background:#F8FAFC;'
                'border:1px solid #E2E8F0;border-radius:12px 12px 0 0;">'
                '<div style="flex:2.5;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Klant</div>'
                '<div style="flex:1.1;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Telefoon</div>'
                '<div style="flex:1.3;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Email</div>'
                '<div style="flex:0.8;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Locatie</div>'
                '<div style="flex:0.9;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Aangemaakt</div>'
                '<div style="flex:0.4;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Proj.</div>'
                '<div style="flex:0.7;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Status</div>'
                '<div style="flex:0.75;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Acties</div>'
                '</div>',
                unsafe_allow_html=True,
            )

            for i, klant in enumerate(pagina_klanten):
                fg, bg = get_avatar_stijl(klant["id"])
                init   = get_initialen(klant["naam"])
                proj_count = sum(1 for p in st.session_state.projecten if p["klant_id"] == klant["id"])
                rij_bg = "#FAFBFC" if i % 2 == 1 else "white"
                is_last = i == len(pagina_klanten) - 1
                bot_r  = "0 0 12px 12px" if is_last else "0"

                kl_actief = klant.get("actief", True)
                kl_badge  = ('<span style="background:#D1FAE5;color:#065F46;padding:3px 9px;border-radius:99px;font-size:11px;font-weight:600;white-space:nowrap;">✓ Actief</span>'
                             if kl_actief else
                             '<span style="background:#F3F4F6;color:#6B7280;padding:3px 9px;border-radius:99px;font-size:11px;font-weight:600;white-space:nowrap;">○ Inactief</span>')
                bedrijf_str = klant.get("bedrijf","")
                adres_str   = ", ".join(filter(None,[
                    klant.get("adres",""),
                    klant.get("postcode",""),
                    klant.get("stad","")
                ]))
                sub = ""
                if bedrijf_str:
                    sub += f'<div style="font-size:11px;color:#94A3B8;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{h(bedrijf_str)}</div>'
                if adres_str:
                    sub += f'<div style="font-size:11px;color:#94A3B8;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{h(adres_str)}</div>'

                tel   = klant.get("telefoon") or "—"
                email = klant.get("email")    or "—"
                stad  = klant.get("stad")     or "—"
                naam  = klant["naam"]
                aangemaakt = klant.get("aangemaakt", "—")

                st.markdown(f"""
<div class="cf-tbl-row" style="display:flex;align-items:center;background:{rij_bg};
            border:1px solid #E2E8F0;border-top:none;
            border-radius:{bot_r};padding:14px 12px 17px 12px;min-height:63px;">
  <div style="flex:2.5;display:flex;align-items:center;gap:9px;overflow:hidden;padding-right:8px;">
    <div style="width:34px;height:34px;min-width:34px;border-radius:9px;
                background:{bg};color:{fg};display:flex;align-items:center;
                justify-content:center;font-size:12px;font-weight:700;">{h(init)}</div>
    <div style="overflow:hidden;">
      <div style="font-size:13px;font-weight:600;color:#0F172A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{h(naam)}</div>{sub}
    </div>
  </div>
  <div style="flex:1.1;font-size:13px;color:#2563EB;font-family:'DM Mono',monospace;white-space:nowrap;overflow:hidden;padding-right:8px;">{h(tel)}</div>
  <div style="flex:1.3;font-size:13px;color:#2563EB;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:8px;">{h(email)}</div>
  <div style="flex:0.8;font-size:12.5px;color:#374151;white-space:nowrap;padding-right:8px;">{h(stad)}</div>
  <div style="flex:0.9;font-size:12px;color:#94A3B8;white-space:nowrap;padding-right:8px;">{h(format_datum(aangemaakt))}</div>
  <div style="flex:0.4;font-size:13px;font-weight:600;color:#0F172A;padding-right:8px;">{proj_count}</div>
  <div style="flex:0.7;padding-right:8px;">{kl_badge}</div>
  <div style="flex:0.75;display:flex;align-items:center;justify-content:flex-end;gap:5px;">
    <div style="width:28px;height:28px;min-width:28px;border-radius:7px;background:#F8FAFC;
                border:1px solid #E2E8F0;display:flex;align-items:center;
                justify-content:center;font-size:12px;color:#94A3B8;" title="Klant bekijken">
      <i class="bi bi-eye"></i></div>
    <div style="width:28px;height:28px;min-width:28px;border-radius:7px;background:#F8FAFC;
                border:1px solid #E2E8F0;display:flex;align-items:center;
                justify-content:center;font-size:12px;color:#94A3B8;" title="Klant bewerken">
      <i class="bi bi-pencil"></i></div>
    <div style="width:28px;height:28px;min-width:28px;border-radius:7px;background:#F8FAFC;
                border:1px solid #E2E8F0;display:flex;align-items:center;
                justify-content:center;font-size:12px;color:#94A3B8;" title="Klant verwijderen">
      <i class="bi bi-trash3"></i></div>
  </div>
</div>
""", unsafe_allow_html=True)
                # ── Onzichtbare overlay-knoppen (opacity:0, z-index over HTML icoontjes) ──
                # Posities komen uit CSS (absoluut, vast rechts uitgelijnd) → schaalt op elke breedte.
                _s, _v, _e, _d = st.columns([1, 0.1, 0.1, 0.1])
                with _v:
                    st.markdown('<span class="kl-ovl-mk" style="display:none;"></span>', unsafe_allow_html=True)
                    if st.button("v", key=f"kl_v_{klant['id']}"):
                        st.session_state.kl_view_id = klant['id'] if st.session_state.kl_view_id != klant['id'] else None
                        st.session_state.kl_edit_id = None
                        st.session_state.kl_del_id = None
                        st.rerun()
                with _e:
                    if st.button("e", key=f"kl_e_{klant['id']}"):
                        st.session_state.kl_edit_id = klant['id'] if st.session_state.kl_edit_id != klant['id'] else None
                        st.session_state.kl_del_id = None
                        st.session_state.kl_view_id = None
                        st.rerun()
                with _d:
                    if st.button("d", key=f"kl_d_{klant['id']}"):
                        st.session_state.kl_del_id = klant['id'] if st.session_state.kl_del_id != klant['id'] else None
                        st.session_state.kl_edit_id = None
                        st.session_state.kl_view_id = None
                        st.rerun()

            # ── Inline edit formulier ──
            if st.session_state.kl_edit_id is not None:
                edit_klant = next((k for k in st.session_state.klanten
                                   if k["id"] == st.session_state.kl_edit_id), None)
                if edit_klant:
                    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
                    with st.form("kl_edit_inline_form"):
                        st.markdown('<span class="kl-inline-edit-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                            f'<i class="bi bi-pencil-square" style="font-size:15px;color:#2563EB;"></i>'
                            f'<span style="font-size:14px;font-weight:700;color:#0F172A;">Klant bewerken — {h(edit_klant["naam"])}</span>'
                            f'</div>',
                            unsafe_allow_html=True)
                        ec1, ec2 = st.columns(2)
                        with ec1:
                            en  = st.text_input("Naam", value=edit_klant["naam"])
                            eb  = st.text_input("Bedrijfsnaam (optioneel)", value=edit_klant.get("bedrijf",""))
                            ea  = st.text_input("Adres", value=edit_klant.get("adres",""))
                            ep1, ep2 = st.columns(2)
                            with ep1:
                                epc = st.text_input("Postcode", value=edit_klant.get("postcode",""))
                            with ep2:
                                est = st.text_input("Stad", value=edit_klant.get("stad",""))
                        with ec2:
                            et  = st.text_input("Telefoon", value=edit_klant.get("telefoon",""))
                            ee  = st.text_input("Email", value=edit_klant.get("email",""))
                            eno = st.text_area("Notities", value=edit_klant.get("notities",""), height=80)
                        save_c, cancel_c, _ = st.columns([1, 1, 5])
                        with save_c:
                            st.markdown('<span class="cf-ico-mk cf-ico-save-mk"></span>', unsafe_allow_html=True)
                            if st.form_submit_button("Opslaan", use_container_width=True, type="primary"):
                                # Zelfde centrale validatie als bij toevoegen → geen ongeldige
                                # klant opslaan (lost ook het '</div>'-renderprobleem op, want
                                # alle verplichte velden blijven gevuld).
                                _kle_fout = eerste_validatiefout(
                                    valideer_tekst(en, "Naam", min_len=2),
                                    valideer_tekst(ea, "Adres", min_len=3, anti_gibberish=False),
                                    valideer_postcode(epc, verplicht=True),
                                    valideer_tekst(est, "Stad", min_len=2),
                                    valideer_telefoon(et, verplicht=False),
                                    valideer_email(ee, verplicht=False),
                                )
                                if _kle_fout:
                                    ui_alert(_kle_fout, "error")
                                else:
                                    for k in st.session_state.klanten:
                                        if k["id"] == edit_klant["id"]:
                                            k.update({"naam":en,"bedrijf":eb,"adres":ea,
                                                      "postcode":epc,"stad":est,
                                                      "telefoon":et,"email":ee,"notities":eno})
                                            break
                                    st.session_state.kl_edit_id = None
                                    save_data()
                                    st.toast(f"Klant '{en}' bijgewerkt!")
                                    st.rerun()
                        with cancel_c:
                            st.markdown('<span class="cf-ico-mk"></span>', unsafe_allow_html=True)
                            if st.form_submit_button("Annuleren", use_container_width=True):
                                st.session_state.kl_edit_id = None
                                st.rerun()

            # ── Inline delete bevestiging ──
            if st.session_state.kl_del_id is not None:
                del_klant = next((k for k in st.session_state.klanten
                                  if k["id"] == st.session_state.kl_del_id), None)
                if del_klant:
                    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
                    del_proj = sum(1 for p in st.session_state.projecten if p["klant_id"] == del_klant["id"])
                    proj_warn = (f" Deze klant heeft <strong>{del_proj} gekoppeld project{'en' if del_proj != 1 else ''}</strong>"
                                 f" die ook {'worden' if del_proj != 1 else 'wordt'} verwijderd." if del_proj else "")
                    st.markdown(
                        f'<div style="background:#FFF5F5;border:1px solid #FECACA;border-radius:12px;padding:16px 18px;">'
                        f'<div style="font-size:14px;font-weight:700;color:#DC2626;margin-bottom:6px;">'
                        f'<i class="bi bi-exclamation-triangle" style="margin-right:6px;"></i>Klant verwijderen?</div>'
                        f'<div style="font-size:13px;color:#374151;">Weet je zeker dat je <strong>{h(del_klant["naam"])}</strong> wilt verwijderen?{proj_warn}</div>'
                        f'</div>',
                        unsafe_allow_html=True)
                    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
                    dc1, dc2, _ = st.columns([1, 1, 6])
                    with dc1:
                        st.markdown('<span class="kl-del-confirm-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Verwijderen", key="kl_del_confirm", type="primary", use_container_width=True):
                            _del_id = del_klant["id"]
                            # Reset geselecteerd_project als het een project van deze klant is
                            if st.session_state.geselecteerd_project is not None:
                                _sel = next((p for p in st.session_state.projecten
                                             if p["id"] == st.session_state.geselecteerd_project), None)
                                if _sel and _sel["klant_id"] == _del_id:
                                    st.session_state.geselecteerd_project = None
                            # Cascade delete: verwijder de klant én alle gekoppelde projecten
                            st.session_state.klanten   = [k for k in st.session_state.klanten   if k["id"]       != _del_id]
                            st.session_state.projecten = [p for p in st.session_state.projecten if p["klant_id"] != _del_id]
                            prune_personeel_projectkoppelingen()   # SP-005
                            st.session_state.kl_del_id = None
                            st.session_state.klanten_pagina = 1
                            save_data()
                            st.toast(f"Klant '{del_klant['naam']}' en gekoppelde projecten verwijderd.")
                            st.rerun()
                    with dc2:
                        st.markdown('<span class="kl-del-cancel-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Annuleren", key="kl_del_cancel", use_container_width=True):
                            st.session_state.kl_del_id = None
                            st.rerun()

            # ── Klant detailweergave (Bekijken) — direct onder de tabel, max. 1 tegelijk ──
            if st.session_state.kl_view_id is not None:
                _vk = next((k for k in st.session_state.klanten
                            if k["id"] == st.session_state.kl_view_id), None)
                if _vk:
                    # Gekoppelde projecten + omzet (excl. BTW — zelfde bron als Projecten-overzicht)
                    _vk_proj  = [p for p in st.session_state.projecten if p.get("klant_id") == _vk["id"]]
                    _vk_proj  = sorted(_vk_proj, key=lambda p: str(p.get("aangemaakt", "")), reverse=True)
                    _vk_omzet = sum(bereken_project_totaal(p)["excl_btw"] for p in _vk_proj)

                    st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

                    # Klantgegevens
                    def _vveld(lbl, val):
                        return (f'<div style="margin-bottom:12px;">'
                                f'<div style="font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:3px;">{lbl}</div>'
                                f'<div style="font-size:13.5px;color:#0F172A;word-break:break-word;">{val}</div></div>')
                    _gegevens_grid = (
                        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 20px;">'
                        + _vveld("Naam",     h(_vk["naam"]))
                        + _vveld("Telefoon", h(_vk.get("telefoon", "") or "—"))
                        + _vveld("Adres",    h(_vk.get("adres", "") or "—"))
                        + _vveld("Email",    h(_vk.get("email", "") or "—"))
                        + _vveld("Postcode", h(_vk.get("postcode", "") or "—"))
                        + _vveld("Stad",     h(_vk.get("stad", "") or "—"))
                        + '</div>')
                    _omzet_sub = "{} project{} · excl. BTW".format(len(_vk_proj), "" if len(_vk_proj) == 1 else "en")

                    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
                    st.markdown(
                        f'<div style="display:flex;gap:16px;margin-bottom:16px;align-items:stretch;">'
                        f'<div style="flex:2;background:white;border:1px solid #E8EFF5;border-radius:14px;box-shadow:0 1px 4px rgba(0,0,0,0.05);padding:18px 20px;">'
                        f'<div style="font-size:13px;font-weight:700;color:#0F172A;margin-bottom:14px;display:flex;align-items:center;gap:7px;">'
                        f'<i class="bi bi-person-vcard" style="color:#2563EB;"></i>Klantgegevens</div>{_gegevens_grid}</div>'
                        f'<div style="flex:1;background:linear-gradient(135deg,#EFF6FF 0%,#EEF2FF 100%);border:1px solid #BFDBFE;'
                        f'border-radius:14px;box-shadow:0 1px 4px rgba(0,0,0,0.05);padding:18px 20px;display:flex;flex-direction:column;justify-content:center;">'
                        f'<div style="font-size:10.5px;font-weight:700;color:#3B82F6;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Totale omzet</div>'
                        f'<div style="font-size:28px;font-weight:800;color:#0F172A;font-family:\'DM Mono\',monospace;letter-spacing:-0.5px;line-height:1.1;">{format_eur(_vk_omzet)}</div>'
                        f'<div style="font-size:12px;color:#64748B;margin-top:6px;">{_omzet_sub}</div></div></div>'
                        f'<div style="font-size:13px;font-weight:700;color:#0F172A;margin-bottom:8px;display:flex;align-items:center;gap:7px;">'
                        f'<i class="bi bi-clock-history" style="color:#2563EB;"></i>Klusgeschiedenis</div>',
                        unsafe_allow_html=True)

                    # Klusgeschiedenis tabel
                    if _vk_proj:
                        _klus = (
                            '<div style="display:flex;padding:9px 16px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:12px 12px 0 0;">'
                            '<div style="flex:2.4;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Project</div>'
                            '<div style="flex:1.4;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Status</div>'
                            '<div style="flex:1.1;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Datum</div>'
                            '<div style="flex:1.0;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;text-align:right;">Bedrag</div>'
                            '</div>')
                        for _idx, _p in enumerate(_vk_proj):
                            _pbedrag = bereken_project_totaal(_p)["excl_btw"]
                            _pdatum  = format_datum(_p.get("aangemaakt", "")) or "—"
                            _rbg     = "#FAFBFC" if _idx % 2 == 1 else "white"
                            _rrad    = "0 0 12px 12px" if _idx == len(_vk_proj) - 1 else "0"
                            _klus += (
                                f'<div style="display:flex;align-items:center;background:{_rbg};border:1px solid #E2E8F0;border-top:none;border-radius:{_rrad};padding:12px 16px;">'
                                f'<div style="flex:2.4;font-size:13px;font-weight:600;color:#0F172A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:10px;">{h(_p["naam"])}</div>'
                                f'<div style="flex:1.4;padding-right:10px;">{status_badge(_p["status"])}</div>'
                                f'<div style="flex:1.1;font-size:12.5px;color:#94A3B8;padding-right:10px;">{h(_pdatum)}</div>'
                                f'<div style="flex:1.0;font-size:13px;font-weight:600;color:#0F172A;font-family:\'DM Mono\',monospace;text-align:right;">{format_eur(_pbedrag)}</div>'
                                f'</div>')
                        st.markdown(_klus, unsafe_allow_html=True)
                    else:
                        st.markdown(
                            '<div style="border:1px solid #E2E8F0;border-radius:12px;padding:24px;text-align:center;'
                            'color:#94A3B8;font-size:13px;background:white;">'
                            '<i class="bi bi-folder2-open" style="font-size:22px;display:block;margin-bottom:6px;color:#CBD5E1;"></i>'
                            'Nog geen projecten gekoppeld aan deze klant.</div>',
                            unsafe_allow_html=True)

                    # Sluiten-knop rechtsonderin
                    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
                    _, _kl_close_col = st.columns([6, 1])
                    with _kl_close_col:
                        if st.button("Sluiten", key="kl_view_close", use_container_width=True):
                            st.session_state.kl_view_id = None
                            st.rerun()

        # Paginatie
            st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
            pag_l, pag_m, pag_r = st.columns([3, 5, 2])
            with pag_l:
                st.markdown(
                    f'<div style="font-size:12px;color:#94A3B8;padding-top:6px;">Toont {start+1}–{einde} van {totaal} klanten</div>',
                    unsafe_allow_html=True)
            with pag_m:
                if max_pagina > 1:
                    pcols = st.columns([1.5] + [0.7]*max_pagina + [1.5])
                    with pcols[0]:
                        if st.button("← Vorige", key="pag_prev", disabled=(pagina<=1)):
                            st.session_state.klanten_pagina = pagina - 1
                            st.rerun()
                    for p in range(1, max_pagina+1):
                        with pcols[p]:
                            if st.button(f"**{p}**" if p==pagina else str(p), key=f"pag_{p}"):
                                st.session_state.klanten_pagina = p
                                st.rerun()
                    with pcols[max_pagina+1]:
                        if st.button("Volgende →", key="pag_next", disabled=(pagina>=max_pagina)):
                            st.session_state.klanten_pagina = pagina + 1
                            st.rerun()

    # ──────────────────────────────────────────────────
    # NIEUWE KLANT TAB
    # ──────────────────────────────────────────────────
    with tab_nieuw:
        st.markdown('<div style="font-size:22px;font-weight:700;color:#0F172A;letter-spacing:-0.3px;margin-bottom:2px;">Nieuwe klant toevoegen</div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:12.5px;color:#94A3B8;margin-bottom:20px;">Vul de gegevens van de nieuwe klant in.</div>', unsafe_allow_html=True)

        # Beide cards ALTIJD even hoog — robuust via CSS i.p.v. timing-afhankelijke JS-meting.
        # De kolommen stretchen gelijk; de card-keten vult die hoogte (height:100%).
        _inject_keyed_css("kl_card_hoogte", """
        [data-testid="stHorizontalBlock"]:has(span.kl-card-m){align-items:stretch !important;}
        [data-testid="stColumn"]:has(span.kl-card-m) > [data-testid="stVerticalBlock"]{height:100% !important;}
        [data-testid="stColumn"]:has(span.kl-card-m) > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]{height:100% !important;}
        [data-testid="stColumn"]:has(span.kl-card-m) > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"]{height:100% !important;}
        """)

        with st.form("nieuwe_klant_form", clear_on_submit=True):
            col_l, col_r = st.columns(2)

            with col_l:
                with st.container(border=True):
                    st.markdown('<span class="kl-card-m" style="display:none;"></span>', unsafe_allow_html=True)
                    st.markdown(
                        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                        '<i class="bi bi-people" style="font-size:16px;color:#2563EB;"></i>'
                        '<span style="font-size:14px;font-weight:700;color:#0F172A;">Algemene gegevens</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Naam <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                    k_naam    = st.text_input("Naam", placeholder="Bijv. Familie Jansen", label_visibility="collapsed")
                    k_bedrijf = st.text_input("Bedrijfsnaam (optioneel)", placeholder="Bijv. Jansen Schilderwerken")
                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Adres <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                    k_adres   = st.text_input("Adres", placeholder="Bijv. Kerkstraat 12", label_visibility="collapsed")
                    pc1, pc2  = st.columns(2)
                    with pc1:
                        st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Postcode <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                        k_postcode = st.text_input("Postcode", placeholder="5211 AB", label_visibility="collapsed")
                    with pc2:
                        st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Stad <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                        k_stad = st.text_input("Stad", placeholder="Den Bosch", label_visibility="collapsed")

            with col_r:
                with st.container(border=True):
                    st.markdown('<span class="kl-card-m" style="display:none;"></span>', unsafe_allow_html=True)
                    st.markdown(
                        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                        '<i class="bi bi-telephone" style="font-size:16px;color:#2563EB;"></i>'
                        '<span style="font-size:14px;font-weight:700;color:#0F172A;">Contactgegevens</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    k_tel      = st.text_input("Telefoon", placeholder="Bijv. 06-12345678")
                    k_email    = st.text_input("Email (optioneel)", placeholder="Bijv. naam@email.nl")
                    k_btw      = st.text_input("BTW-nummer (optioneel)", placeholder="Bijv. NL123456789B01")
                    st.markdown(
                        '<div style="display:flex;align-items:center;gap:8px;margin-top:12px;margin-bottom:8px;">'
                        '<i class="bi bi-journal-text" style="font-size:15px;color:#2563EB;"></i>'
                        '<span style="font-size:13px;font-weight:600;color:#374151;">Notities</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    k_notities = st.text_area("Notities (optioneel)", placeholder="Bijv. Speciale wensen…", height=100, label_visibility="collapsed")

            st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

            # Knoppen rechtsonder
            _, btn_ann, btn_opl = st.columns([6, 1.4, 2])
            with btn_ann:
                st.form_submit_button("Annuleren", use_container_width=True)
            with btn_opl:
                opslaan = st.form_submit_button("✓  Klant opslaan", use_container_width=True, type="primary")

            k_kvk = ""

            if opslaan:
                # Centrale invoervalidatie (beta-blocker): professionele controle i.p.v.
                # alleen "niet leeg". Verplichte velden = naam/adres/postcode/stad (zoals de
                # *-markering); telefoon/e-mail optioneel maar wél op formaat gecontroleerd.
                _kl_fout = eerste_validatiefout(
                    valideer_tekst(k_naam, "Naam", min_len=2),
                    valideer_tekst(k_adres, "Adres", min_len=3, anti_gibberish=False),
                    valideer_postcode(k_postcode, verplicht=True),
                    valideer_tekst(k_stad, "Stad", min_len=2),
                    valideer_telefoon(k_tel, verplicht=False),
                    valideer_email(k_email, verplicht=False),
                )
                if not _kl_fout:
                    st.session_state.klanten.append({
                        "id":         st.session_state.volgende_klant_id,
                        "naam":       k_naam,
                        "bedrijf":    k_bedrijf,
                        "adres":      k_adres,
                        "postcode":   k_postcode,
                        "stad":       k_stad,
                        "telefoon":   k_tel,
                        "email":      k_email,
                        "btw_nummer": k_btw,
                        "kvk":        k_kvk,
                        "notities":   k_notities,
                        "aangemaakt": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                    })
                    st.session_state.volgende_klant_id += 1
                    save_data()
                    # UX: succesmelding via flash + automatisch terug naar het overzicht
                    # (zelfde gedrag als Personeel). Formulier sluit (clear_on_submit +
                    # tabwissel), nieuwe klant direct zichtbaar, geen refresh/dubbele opslag.
                    st.session_state["kl_flash"] = f"Klant '{k_naam}' succesvol toegevoegd!"
                    st.session_state["kl_goto_overzicht"] = True
                    st.rerun()
                else:
                    ui_alert(_kl_fout, "error")

        # Witte cards + form-border weg + gelijke hoogte: volledig via CSS hierboven
        # (:has(span.kl-card-m) — zelfde kleuren/rand/radius/schaduw). Het oude JS-vangnet
        # (MutationObserver op de hele document.body + 4× setTimeout) is verwijderd: de
        # CSS-tweeling doet exact hetzelfde, maar zonder client-side herbouw-overhead per klik.

# PRODUCTEN
# =====================================================

elif selected == "Producten":

    # ── Session state ──
    if "producten_pagina" not in st.session_state:
        st.session_state.producten_pagina = 1
    if "pr_edit_id" not in st.session_state:
        st.session_state.pr_edit_id = None
    if "pr_del_id" not in st.session_state:
        st.session_state.pr_del_id = None

    PROD_PER_PAG = 10

    # ── Categorie kleur & icoon helpers ──
    CAT_KLEUR = {
        "Verf":        ("#DBEAFE", "#1D4ED8"),
        "Primer":      ("#FEF3C7", "#92400E"),
        "Kit":         ("#EDE9FE", "#5B21B6"),
        "Gereedschap": ("#D1FAE5", "#065F46"),
        "Overig":      ("#F3F4F6", "#374151"),
    }
    CAT_ICON = {
        "Verf":        "droplet",
        "Primer":      "layers",
        "Kit":         "wrench-adjustable",
        "Gereedschap": "tools",
        "Overig":      "box",
    }

    def cat_badge(cat):
        bg, fg = CAT_KLEUR.get(cat, ("#F3F4F6", "#374151"))
        return (f'<span style="background:{bg};color:{fg};padding:3px 10px;'
                f'border-radius:99px;font-size:11px;font-weight:600;white-space:nowrap;">'
                f'{h(cat)}</span>')

    # ── CSS ──
    _inject_keyed_css("prod_page", """
    .prod-icon-btn{width:32px;height:32px;border-radius:8px;background:#F8FAFC;border:1px solid #E2E8F0;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;transition:all 0.12s;}
    .prod-icon-btn:hover{background:#EFF6FF;border-color:#BFDBFE;}
    .prod-icon-btn.del:hover{background:#FFF5F5;border-color:#FECACA;}
    """)
    _inject_keyed_css("prod_overlay", """
    div[data-testid="stLayoutWrapper"]:has(span.pr-ovl-mk){height:0 !important;overflow:visible !important;margin:-16px 0 !important;padding:0 !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk){margin-top:-62px !important;height:62px !important;background:transparent !important;gap:0 !important;position:relative !important;z-index:50 !important;pointer-events:none !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) div[data-testid="stColumn"]{background:transparent !important;padding:0 !important;min-width:0 !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) div[data-testid="stVerticalBlock"]{background:transparent !important;gap:0 !important;height:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) div[data-testid="stElementContainer"]{margin:0 !important;padding:0 !important;height:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) div[data-testid="stMarkdownContainer"]{display:none !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) div[data-testid="stColumn"]:not(:first-child){pointer-events:auto !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) [data-testid="stElementContainer"]{width:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) [data-testid="stButton"]{width:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) button{opacity:0 !important;width:100% !important;height:62px !important;cursor:pointer !important;pointer-events:auto !important;background:transparent !important;border:none !important;box-shadow:none !important;margin:0 !important;padding:0 !important;transform:none !important;}
    /* Bewerk/verwijder-klikzones absoluut vast vanaf de rechterrand → vallen exact over de (rechts uitgelijnde) iconen op elke schermbreedte */
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) > div[data-testid="stColumn"]:nth-child(3){position:absolute !important;top:0 !important;right:53px !important;width:34px !important;min-width:34px !important;}
    div[data-testid="stHorizontalBlock"]:has(span.pr-ovl-mk) > div[data-testid="stColumn"]:nth-child(4){position:absolute !important;top:0 !important;right:15px !important;width:34px !important;min-width:34px !important;}
    [data-testid="stForm"]:has(span.pr-inline-edit-mk){background:#FFFFFF !important;border:1px solid #BFDBFE !important;border-radius:14px !important;box-shadow:0 2px 8px rgba(37,99,235,0.07) !important;}
    [data-testid="stForm"]:has(span.pr-inline-edit-mk) [data-testid="stVerticalBlock"],[data-testid="stForm"]:has(span.pr-inline-edit-mk) [data-testid="stHorizontalBlock"],[data-testid="stForm"]:has(span.pr-inline-edit-mk) [data-testid="stMarkdownContainer"],[data-testid="stForm"]:has(span.pr-inline-edit-mk) [data-testid="stColumn"]{background:#FFFFFF !important;}
    [data-testid="stForm"]:has(span.pr-inline-edit-mk) button [data-testid="stMarkdownContainer"]{background:transparent !important;}
    """)

    # ── Pagina header ──
    st.markdown(
        '<div style="margin-bottom:4px;">'
        '<div style="font-size:24px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;line-height:1.2;">Producten &amp; Materialen</div>'
        '<div style="font-size:13px;color:#64748B;margin-top:4px;">Beheer al je producten, materialen en verbruiksartikelen.</div>'
        '</div>',
        unsafe_allow_html=True)
    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)

    _inject_keyed_css("prod_del_stijl", """
    /* Verwijder-bevestiging: Verwijderen rood */
    div[data-testid="stColumn"]:has(span.pr-del-confirm-mk) .stButton > button {
        background: white !important;
        color: #DC2626 !important;
        border: 1.5px solid #FEE2E2 !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.pr-del-confirm-mk) .stButton > button:hover {
        background: #FFF5F5 !important;
        border-color: #FECACA !important;
        transform: none !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.pr-del-confirm-mk) .stButton > button::before {
        font-family:"bootstrap-icons"; content:"\\f5de"; margin-right:7px; font-size:14px;
        vertical-align:-0.1em; font-style:normal; font-weight:400;
    }
    /* Verwijder-bevestiging: Annuleren wit/neutraal */
    div[data-testid="stColumn"]:has(span.pr-del-cancel-mk) .stButton > button {
        background: white !important;
        color: #475569 !important;
        border: 1px solid #E2E8F0 !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.pr-del-cancel-mk) .stButton > button:hover {
        background: #F8FAFC !important;
        border-color: #CBD5E1 !important;
        transform: none !important;
        box-shadow: none !important;
    }
    """)

    # ── KPI stat-cards ──
    producten_all = st.session_state.producten
    totaal_prod   = len(producten_all)
    gem_prijs     = (sum(p["prijs"] for p in producten_all) / totaal_prod) if totaal_prod > 0 else 0.0
    cat_uniek     = len(set(p.get("categorie", "Overig") for p in producten_all))
    _laatste_naam = producten_all[-1]["naam"] if producten_all else "—"
    _laatste_kort = _laatste_naam[:16] + ("…" if len(_laatste_naam) > 16 else "")

    ks1, ks2, ks3, ks4 = st.columns(4)
    _stat_icon_clr = {"blue": "#2563EB", "green": "#059669", "amber": "#D97706", "indigo": "#4F46E5"}
    # _val_txt=True → tekstwaarde (productnaam): kleinere, single-line variant ipv groot getal
    for _col, _cls, _icon, _label, _val, _sub, _val_txt in [
        (ks1, "blue",  "box-seam",     "TOTAAL PRODUCTEN", str(totaal_prod),         "Producten &amp; materialen", False),
        (ks2, "green", "tag",           "GEMIDDELDE PRIJS",  format_eur(gem_prijs),   "Gemiddeld over alle producten", False),
        (ks3, "amber", "grid-3x3-gap", "CATEGORIEËN",        str(cat_uniek),          "Unieke categorieën", False),
        (ks4, "indigo","clock-history", "LAATST TOEGEVOEGD", h(_laatste_kort),        "Meest recent toegevoegd", True),
    ]:
        _val_cls = "db-stat-value db-stat-value-name" if _val_txt else "db-stat-value"
        with _col:
            st.markdown(
                f'<div class="db-stat-card {_cls}">'
                f'<div class="db-stat-icon {_cls}"><i class="bi bi-{_icon}" style="font-size:17px;color:{_stat_icon_clr.get(_cls, "#2563EB")};"></i></div>'
                f'<div class="db-stat-label">{_label}</div>'
                f'<div class="{_val_cls}">{_val}</div>'
                f'<div class="db-stat-sub">{_sub}</div>'
                f'</div>',
                unsafe_allow_html=True)

    st.markdown("<div style='height:20px;'></div>", unsafe_allow_html=True)

    # ── Tabs ──
    tab_ovz, tab_nieuw = st.tabs(["Overzicht", "+ Nieuw product"])

    # ══════════════════════════════════════════
    # OVERZICHT TAB
    # ══════════════════════════════════════════
    with tab_ovz:

        # Filter toolbar
        zf1, zf2, zf3 = st.columns([3, 1.5, 1.5])
        with zf1:
            pr_zoek = st.text_input("Zoek", placeholder="Zoek productnaam of werkzaamheid…", key="pr_zoek")
        with zf2:
            _CAT_VOLGORDE = ["Verf", "Primer", "Kit", "Gereedschap", "Schuurpapier", "Behang", "Overig"]
            _alle_cats_set = set(p.get("categorie", "Overig") for p in producten_all)
            _alle_cats = [c for c in _CAT_VOLGORDE if c in _alle_cats_set] + sorted(c for c in _alle_cats_set if c not in _CAT_VOLGORDE)
            pr_cat_filter = st.selectbox("Categorie", ["Alle categorieën"] + _alle_cats, key="pr_cat_filter")
        with zf3:
            pr_sort = st.selectbox("Sorteer op", ["Naam A–Z", "Naam Z–A", "Hoogste prijs", "Laagste prijs"], key="pr_sort")

        # Filteren
        prod_lijst = list(producten_all)
        if pr_zoek:
            _q = pr_zoek.lower()
            prod_lijst = [p for p in prod_lijst if _q in p["naam"].lower()
                          or any(_q in w.lower() for w in p.get("werkzaamheden", []))]
        if pr_cat_filter != "Alle categorieën":
            prod_lijst = [p for p in prod_lijst if p.get("categorie", "Overig") == pr_cat_filter]

        # Sorteren (categorie-volgorde als primaire sleutel)
        _CAT_IDX = {c: i for i, c in enumerate(["Verf", "Primer", "Kit", "Gereedschap", "Schuurpapier", "Behang", "Overig"])}
        def _cat_key(p): return _CAT_IDX.get(p.get("categorie", "Overig"), 99)
        if pr_sort == "Naam A–Z":
            prod_lijst = sorted(sorted(prod_lijst, key=lambda p: p["naam"]), key=_cat_key)
        elif pr_sort == "Naam Z–A":
            prod_lijst = sorted(sorted(prod_lijst, key=lambda p: p["naam"], reverse=True), key=_cat_key)
        elif pr_sort == "Hoogste prijs":
            prod_lijst = sorted(sorted(prod_lijst, key=lambda p: p["prijs"], reverse=True), key=_cat_key)
        elif pr_sort == "Laagste prijs":
            prod_lijst = sorted(sorted(prod_lijst, key=lambda p: p["prijs"]), key=_cat_key)

        # Paginatie berekening
        totaal_pr  = len(prod_lijst)
        pagina_pr  = st.session_state.producten_pagina
        max_pag_pr = max(1, (totaal_pr + PROD_PER_PAG - 1) // PROD_PER_PAG)
        pagina_pr  = min(pagina_pr, max_pag_pr)
        start_pr   = (pagina_pr - 1) * PROD_PER_PAG
        einde_pr   = min(start_pr + PROD_PER_PAG, totaal_pr)
        prod_pagina = prod_lijst[start_pr:einde_pr]

        st.markdown(
            f'<div style="font-size:13px;color:#64748B;margin-bottom:10px;font-weight:500;">'
            f'{totaal_pr} product{"en" if totaal_pr != 1 else ""}</div>',
            unsafe_allow_html=True)

        if not prod_lijst:
            ui_alert("Geen producten gevonden.", "info")
        else:
            # Tabel header  (kolommen iets naar links geschoven om ruimte te maken voor Status)
            st.markdown(
                '<div class="cf-tbl-head" style="display:flex;padding:10px 16px;background:#F8FAFC;'
                'border:1px solid #E2E8F0;border-radius:12px 12px 0 0;border-bottom:1px solid #E8EFF5;">'
                '<div style="flex:2.35;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Product</div>'
                '<div style="flex:1.0;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Categorie</div>'
                '<div style="flex:0.8;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Eenheid</div>'
                '<div style="flex:1.0;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Prijs</div>'
                '<div style="flex:1.0;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Verbruik</div>'
                '<div style="flex:0.95;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Inhoud</div>'
                '<div style="flex:1.0;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Status</div>'
                '<div style="flex:0.95;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Acties</div>'
                '</div>',
                unsafe_allow_html=True)

            _IBTN = ("width:32px;height:32px;border-radius:8px;background:#F8FAFC;"
                     "border:1px solid #E2E8F0;display:inline-flex;align-items:center;"
                     "justify-content:center;cursor:pointer;transition:all 0.12s;")

            for i, product in enumerate(prod_pagina):
                _cat         = product.get("categorie", "Overig")
                _bg_cat, _fg_cat = CAT_KLEUR.get(_cat, ("#F3F4F6", "#374151"))
                _icon_cat    = CAT_ICON.get(_cat, "box")
                _wz          = product.get("werkzaamheden", [])
                _wz_str      = ", ".join(_wz[:3]) + ("…" if len(_wz) > 3 else "")
                _rij_bg      = "#FAFBFC" if i % 2 == 1 else "white"
                _bot_r       = "0 0 12px 12px" if i == len(prod_pagina) - 1 else "0"
                # Status-badge — exact zelfde stijl als Personeel-pagina (klikbaar via overlay-knop)
                _pr_actief   = product.get("actief", True)
                _pr_badge    = ('<span style="background:#DCFCE7;color:#166534;padding:4px 11px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap;cursor:pointer;">● Actief</span>'
                                if _pr_actief else
                                '<span style="background:#F3F4F6;color:#6B7280;padding:4px 11px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap;cursor:pointer;">● Inactief</span>')

                st.markdown(
                    f'<div class="cf-tbl-row" style="display:flex;align-items:center;background:{_rij_bg};'
                    f'border:1px solid #E2E8F0;border-top:none;border-radius:{_bot_r};'
                    f'padding:13px 16px;min-height:62px;">'
                    f'<div style="flex:2.35;display:flex;align-items:center;gap:10px;overflow:hidden;padding-right:12px;">'
                    f'<div style="width:34px;height:34px;min-width:34px;border-radius:9px;background:{_bg_cat};'
                    f'display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;">'
                    f'<i class="bi bi-{_icon_cat}" style="font-size:14px;color:{_fg_cat};"></i></div>'
                    f'<div style="overflow:hidden;">'
                    f'<div style="font-size:14px;font-weight:600;color:#0F172A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{h(product["naam"])}</div>'
                    f'<div style="font-size:11px;color:#94A3B8;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{h(_wz_str) if _wz_str else "—"}</div>'
                    f'</div></div>'
                    f'<div style="flex:1.0;padding-right:12px;">{cat_badge(_cat)}</div>'
                    f'<div style="flex:0.8;font-size:14px;color:#374151;padding-right:12px;">{h(product["eenheid"])}</div>'
                    f'<div style="flex:1.0;font-size:14px;color:#0F172A;font-weight:500;font-family:\'DM Mono\',monospace;padding-right:12px;">{format_eur(product["prijs"])}</div>'
                    f'<div style="flex:1.0;font-size:14px;color:#374151;padding-right:12px;">{product["verbruik"]:.3f} /{h(product.get("verbruik_eenheid", "m²"))}</div>'
                    f'<div style="flex:0.95;font-size:14px;color:#374151;padding-right:12px;">{product.get("inhoud", 0):g} {h(product.get("inhoud_eenheid", ""))}</div>'
                    f'<div style="flex:1.0;padding-right:12px;">{_pr_badge}</div>'
                    f'<div style="flex:0.95;display:inline-flex;align-items:center;justify-content:flex-end;gap:6px;">'
                    f'<div style="{_IBTN}" title="Bewerken"><i class="bi bi-pencil" style="font-size:13px;color:#CBD5E1;"></i></div>'
                    f'<div style="{_IBTN}" title="Verwijderen"><i class="bi bi-trash3" style="font-size:13px;color:#CBD5E1;"></i></div>'
                    f'</div></div>',
                    unsafe_allow_html=True)

                # Onzichtbare overlay-knoppen (status-toggle + bewerken + verwijderen)
                # status blijft proportioneel (brede badge); bewerk/verwijder worden via CSS
                # absoluut rechts vastgezet → klikzone klopt op elke schermbreedte. De trailing
                # spacer houdt de status-kolom op zijn plek nu edit/delete uit de flow zijn.
                _ps, _pst, _pe, _pd, _ptr = st.columns([7.1, 0.9, 0.55, 0.5, 1.05])
                with _pst:
                    st.markdown('<span class="pr-ovl-mk" style="display:none;"></span>', unsafe_allow_html=True)
                    if st.button("s", key=f"pr_s_{product['id']}", help="Status wisselen (Actief / Inactief)"):
                        for _p in st.session_state.producten:
                            if _p["id"] == product["id"]:
                                _p["actief"] = not _p.get("actief", True)
                                break
                        save_data()
                        st.rerun()
                with _pe:
                    if st.button("e", key=f"pr_e_{product['id']}"):
                        st.session_state.pr_edit_id = product["id"] if st.session_state.pr_edit_id != product["id"] else None
                        st.session_state.pr_del_id = None
                        st.rerun()
                with _pd:
                    if st.button("d", key=f"pr_d_{product['id']}"):
                        st.session_state.pr_del_id = product["id"] if st.session_state.pr_del_id != product["id"] else None
                        st.session_state.pr_edit_id = None
                        st.rerun()

            # ── Inline edit formulier ──
            if st.session_state.pr_edit_id is not None:
                _edit_pr = next((p for p in st.session_state.producten if p["id"] == st.session_state.pr_edit_id), None)
                if _edit_pr:
                    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
                    with st.form("pr_edit_inline_form"):
                        st.markdown('<span class="pr-inline-edit-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                            f'<i class="bi bi-pencil-square" style="font-size:15px;color:#2563EB;"></i>'
                            f'<span style="font-size:14px;font-weight:700;color:#0F172A;">Product bewerken — {h(_edit_pr["naam"])}</span>'
                            f'</div>',
                            unsafe_allow_html=True)
                        _em1, _em2 = st.columns(2)
                        _ep_verbruik_lbl = "Verbruik per meter" if is_meter_product(_edit_pr) else "Verbruik per m²"
                        with _em1:
                            _ep_naam     = st.text_input("Productnaam *", value=_edit_pr["naam"])
                            _ep_prijs    = st.number_input("Prijs (€)", value=float(_edit_pr["prijs"]), min_value=0.0, step=0.5, format="%.2f")
                            _ep_verbruik = st.number_input(_ep_verbruik_lbl, value=float(_edit_pr["verbruik"]), min_value=0.001, step=0.01, format="%.3f",
                                                           help="Voor Kit en Afplakken geldt: per strekkende meter.")
                            _ep_inhoud   = st.number_input("Inhoud verpakking", value=float(_edit_pr.get("inhoud", 10.0)), min_value=0.001, step=0.5, format="%.3f",
                                                           help="Hoeveelheid per verpakking, bijv. 10 liter, 310 ml, 50 meter.")
                        with _em2:
                            _enh_opties = ["liter", "tube", "rol", "vel", "stuk", "m²", "kg"]
                            _enh_idx    = _enh_opties.index(_edit_pr["eenheid"]) if _edit_pr["eenheid"] in _enh_opties else 0
                            _ep_eenheid = st.selectbox("Eenheid", _enh_opties, index=_enh_idx)
                            _inh_eh_opties = ["liter", "ml", "meter", "vel", "stuk", "kg", "rol"]
                            _inh_eh_cur    = _edit_pr.get("inhoud_eenheid", "liter")
                            _inh_eh_idx    = _inh_eh_opties.index(_inh_eh_cur) if _inh_eh_cur in _inh_eh_opties else 0
                            _ep_inhoud_eh  = st.selectbox("Inhoud-eenheid", _inh_eh_opties, index=_inh_eh_idx)
                            _cat_opties = ["Verf", "Primer", "Kit", "Afplakken", "Gereedschap", "Schuurpapier", "Behang", "Overig"]
                            _cat_idx    = _cat_opties.index(_edit_pr.get("categorie", "Overig")) if _edit_pr.get("categorie", "Overig") in _cat_opties else 5
                            _ep_cat     = st.selectbox("Categorie", _cat_opties, index=_cat_idx)
                            _ep_wz      = st.multiselect("Werkzaamheden", WERKZAAMHEDEN_OPTIES, default=_edit_pr.get("werkzaamheden", []))
                        _sc, _cc, _ = st.columns([1, 1, 5])
                        with _sc:
                            st.markdown('<span class="cf-ico-mk cf-ico-save-mk"></span>', unsafe_allow_html=True)
                            if st.form_submit_button("Opslaan", use_container_width=True, type="primary"):
                                for _p in st.session_state.producten:
                                    if _p["id"] == _edit_pr["id"]:
                                        _p["naam"]          = _ep_naam
                                        _p["prijs"]         = _ep_prijs
                                        _p["verbruik"]      = _ep_verbruik
                                        _p["eenheid"]       = _ep_eenheid
                                        _p["categorie"]     = _ep_cat
                                        _p["werkzaamheden"] = _ep_wz
                                        _p["inhoud"]        = _ep_inhoud
                                        _p["inhoud_eenheid"] = _ep_inhoud_eh
                                        # verbruik-eenheid opnieuw afleiden (categorie kan gewijzigd zijn)
                                        _p["verbruik_eenheid"] = verbruik_eenheid_van(_p)
                                        break
                                st.session_state.pr_edit_id = None
                                save_data()
                                st.toast(f"'{_ep_naam}' bijgewerkt!")
                                st.rerun()
                        with _cc:
                            st.markdown('<span class="cf-ico-mk"></span>', unsafe_allow_html=True)
                            if st.form_submit_button("Annuleren", use_container_width=True):
                                st.session_state.pr_edit_id = None
                                st.rerun()

            # ── Inline delete bevestiging ──
            if st.session_state.pr_del_id is not None:
                _del_pr = next((p for p in st.session_state.producten if p["id"] == st.session_state.pr_del_id), None)
                if _del_pr:
                    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
                    st.markdown(
                        f'<div style="background:#FFF5F5;border:1px solid #FECACA;border-radius:12px;padding:16px 18px;">'
                        f'<div style="font-size:14px;font-weight:700;color:#DC2626;margin-bottom:6px;">'
                        f'<i class="bi bi-exclamation-triangle" style="margin-right:6px;"></i>Product verwijderen?</div>'
                        f'<div style="font-size:13px;color:#374151;">Weet je zeker dat je <strong>{h(_del_pr["naam"])}</strong> wilt verwijderen?</div>'
                        f'</div>',
                        unsafe_allow_html=True)
                    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
                    _dc1, _dc2, _ = st.columns([1, 1, 6])
                    with _dc1:
                        st.markdown('<span class="pr-del-confirm-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Verwijderen", key="pr_del_confirm", type="primary", use_container_width=True):
                            st.session_state.producten = [p for p in st.session_state.producten if p["id"] != _del_pr["id"]]
                            st.session_state.pr_del_id = None
                            st.session_state.producten_pagina = 1
                            save_data()
                            st.toast(f"'{_del_pr['naam']}' verwijderd.")
                            st.rerun()
                    with _dc2:
                        st.markdown('<span class="pr-del-cancel-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Annuleren", key="pr_del_cancel", use_container_width=True):
                            st.session_state.pr_del_id = None
                            st.rerun()

            # ── Paginatie ──
            st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)
            _pag_l, _pag_m = st.columns([3, 5])
            with _pag_l:
                st.markdown(
                    f'<div style="font-size:12px;color:#94A3B8;padding-top:6px;">'
                    f'Toont {start_pr + 1}–{einde_pr} van {totaal_pr} producten</div>',
                    unsafe_allow_html=True)
            with _pag_m:
                if max_pag_pr > 1:
                    _pcols = st.columns([1.2] + [0.6] * max_pag_pr + [1.4])
                    with _pcols[0]:
                        if st.button("← Vorige", key="prod_prev", disabled=(pagina_pr <= 1)):
                            st.session_state.producten_pagina = pagina_pr - 1
                            st.rerun()
                    for _pn in range(1, max_pag_pr + 1):
                        with _pcols[_pn]:
                            if st.button(f"**{_pn}**" if _pn == pagina_pr else str(_pn), key=f"prod_pag_{_pn}"):
                                st.session_state.producten_pagina = _pn
                                st.rerun()
                    with _pcols[max_pag_pr + 1]:
                        if st.button("Volgende →", key="prod_next", disabled=(pagina_pr >= max_pag_pr)):
                            st.session_state.producten_pagina = pagina_pr + 1
                            st.rerun()

    # ══════════════════════════════════════════
    # NIEUW PRODUCT TAB
    # ══════════════════════════════════════════
    with tab_nieuw:
        st.markdown(
            '<div style="font-size:22px;font-weight:700;color:#0F172A;letter-spacing:-0.3px;margin-bottom:2px;">Nieuw product toevoegen</div>',
            unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:12.5px;color:#94A3B8;margin-bottom:20px;">'
            'Voeg een nieuw product of materiaal toe dat gebruikt kan worden in calculaties en offertes.</div>',
            unsafe_allow_html=True)

        # ════════════════════════════════════════════════════════════
        # URL PRODUCT IMPORT — boven het formulier; vult de velden automatisch
        # ════════════════════════════════════════════════════════════
        # Reset-vlag: na 'Product toevoegen' of 'Annuleren' worden de form-velden
        # geleegd zodat het formulier schoon terugkomt (keyed widgets onthouden
        # anders hun laatste waarde).
        if st.session_state.pop("pn_reset", False):
            for _k in ("pn_naam", "pn_prijs", "pn_verbruik", "pn_inhoud", "pn_eenheid",
                       "pn_inhoud_eenheid", "pn_categorie", "pn_werkzaamheden", "pn_import_url",
                       "pn_project"):
                st.session_state.pop(_k, None)

        # Standaardwaarden voor de keyed form-velden (eenmalig), zodat de
        # URL-import ze vóór het renderen kan overschrijven.
        for _k, _v in {"pn_naam": "", "pn_prijs": 20.0, "pn_verbruik": 0.10, "pn_inhoud": 10.0,
                       "pn_eenheid": "liter", "pn_inhoud_eenheid": "liter",
                       "pn_categorie": "Verf", "pn_werkzaamheden": []}.items():
            st.session_state.setdefault(_k, _v)

        _inject_keyed_css("pn_import_card", """
        [data-testid="stLayoutWrapper"]:has(span.pn-import-m) > [data-testid="stVerticalBlock"]{
            background:#FFFFFF !important; border:1px solid #E8EFF5 !important;
            border-radius:16px !important; box-shadow:0 2px 8px rgba(0,0,0,0.05) !important;
            padding:18px 20px !important;
        }
        [data-testid="stLayoutWrapper"]:has(span.pn-import-m) [data-testid="stMarkdownContainer"]{background:transparent !important;}
        [data-testid="stLayoutWrapper"]:has(span.pn-import-m) [data-testid="stBaseButton-primary"]{
            background:#2563EB !important; border-color:#2563EB !important; color:#FFFFFF !important;
        }
        [data-testid="stLayoutWrapper"]:has(span.pn-import-m) [data-testid="stBaseButton-primary"]:hover{
            background:#1D4ED8 !important; border-color:#1D4ED8 !important;
        }
        [data-testid="stLayoutWrapper"]:has(span.pn-import-m) [data-testid="stBaseButton-primary"] p::before{
            font-family:"bootstrap-icons";content:"\\f52a";margin-right:7px;
            font-size:14px;vertical-align:-0.1em;font-style:normal;font-weight:400;
        }
        """)

        with st.container(border=True):
            st.markdown('<span class="pn-import-m" style="display:none;"></span>', unsafe_allow_html=True)
            st.markdown(
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:2px;flex-wrap:wrap;">'
                '<i class="bi bi-link-45deg" style="font-size:17px;color:#2563EB;"></i>'
                '<span style="font-size:14px;font-weight:700;color:#0F172A;">URL product import</span>'
                '<span style="font-size:10px;font-weight:600;color:#2563EB;background:#EFF6FF;'
                'padding:2px 8px;border-radius:99px;">Automatisch invullen</span></div>'
                '<div style="font-size:12px;color:#94A3B8;margin-bottom:12px;">'
                'Plak een productlink (Sigma, Sikkens, Wijzonol, Den Braven, verfwinkel…) en CoatFlow '
                'vult zoveel mogelijk gegevens automatisch in. Controleer ze daarna en sla op.</div>',
                unsafe_allow_html=True)

            st.text_input("Product URL", key="pn_import_url",
                          placeholder="https://www.voorbeeld.nl/product/…")
            if st.button("Product ophalen", key="pn_import_btn", type="primary"):
                _url = (st.session_state.get("pn_import_url") or "").strip()
                if not _PRODUCT_IMPORT_OK:
                    st.session_state["pn_import_flash"] = ("error",
                        "Productimport is niet beschikbaar op deze installatie.")
                elif not _url:
                    st.session_state["pn_import_flash"] = ("warning", "Plak eerst een product-URL.")
                else:
                    with st.spinner("Productinformatie ophalen…"):
                        try:
                            _data, _fout = product_uit_bron("url", _url)
                        except Exception:
                            _data, _fout = {}, ("Productinformatie kon niet automatisch worden "
                                                "opgehaald. Vul de gegevens handmatig in.")
                    if _fout:
                        st.session_state["pn_import_flash"] = ("error", _fout)
                    else:
                        # Baseline (leeg/neutraal), daarna de gevonden waarden eroverheen.
                        for _k, _v in {"pn_naam": "", "pn_prijs": 0.0, "pn_verbruik": 0.10,
                                       "pn_inhoud": 10.0,
                                       "pn_inhoud_eenheid": "liter", "pn_eenheid": "liter",
                                       "pn_categorie": "Overig", "pn_werkzaamheden": []}.items():
                            st.session_state[_k] = _v
                        _vk = {"naam": "pn_naam", "prijs": "pn_prijs", "verbruik": "pn_verbruik",
                               "inhoud": "pn_inhoud",
                               "inhoud_eenheid": "pn_inhoud_eenheid", "eenheid": "pn_eenheid",
                               "categorie": "pn_categorie", "werkzaamheden": "pn_werkzaamheden"}
                        _geldig = {
                            "pn_eenheid": ["liter", "tube", "rol", "vel", "stuk", "m²", "kg"],
                            "pn_inhoud_eenheid": ["liter", "ml", "meter", "vel", "stuk", "kg", "rol"],
                            "pn_categorie": ["Verf", "Primer", "Kit", "Afplakken", "Gereedschap", "Schuurpapier", "Behang", "Overig"],
                        }
                        for _veld, _key in _vk.items():
                            if _veld not in _data:
                                continue
                            _w = _data[_veld]
                            if _veld == "werkzaamheden":
                                _w = [x for x in _w if x in WERKZAAMHEDEN_OPTIES]
                            elif _key in _geldig and _w not in _geldig[_key]:
                                continue  # onbekende optie → overslaan (nooit crashen)
                            st.session_state[_key] = _w
                        _nm = (_data.get("naam") or "")[:55]
                        st.session_state["pn_import_flash"] = ("success",
                            f"Productgegevens opgehaald voor ‘{_nm}’. Controleer de velden "
                            f"hieronder en pas aan waar nodig.")
                st.rerun()

        _flash = st.session_state.pop("pn_import_flash", None)
        if _flash:
            ui_alert(_flash[1], _flash[0])

        _inject_keyed_css("prod_nieuw", """
        /* ── Witte hoofdcard ── */
        [data-testid="stForm"]:has(span.pn-card-m) {
            background: #FFFFFF !important;
            border: 1px solid #E8EFF5 !important;
            border-radius: 16px !important;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05) !important;
        }
        [data-testid="stForm"]:has(span.pn-card-m) [data-testid="stVerticalBlock"],
        [data-testid="stForm"]:has(span.pn-card-m) [data-testid="stHorizontalBlock"],
        [data-testid="stForm"]:has(span.pn-card-m) [data-testid="stMarkdownContainer"] {
            background: #FFFFFF !important;
        }
        /* stColumn: geen witte achtergrond (dropdown-bescherming), wel border/shadow weg */
        [data-testid="stForm"]:has(span.pn-card-m) [data-testid="stColumn"] {
            border: none !important;
            box-shadow: none !important;
        }
        [data-testid="stForm"]:has(span.pn-card-m) button [data-testid="stMarkdownContainer"] {
            background: transparent !important;
        }
        /* Primaire actieknop */
        [data-testid="stForm"]:has(span.pn-card-m) button[kind="primaryFormSubmit"] {
            background: #081A36 !important;
            border-color: #081A36 !important;
            color: white !important;
        }
        [data-testid="stForm"]:has(span.pn-card-m) button[kind="primaryFormSubmit"]:hover {
            background: #041124 !important;
            border-color: #041124 !important;
        }
        """)

        with st.form("nieuw_product_form"):
            st.markdown('<span class="pn-card-m" style="display:none;"></span>', unsafe_allow_html=True)

            # ── Sectie header ──
            st.markdown(
                '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
                '<i class="bi bi-box-seam" style="font-size:16px;color:#2563EB;"></i>'
                '<span style="font-size:14px;font-weight:700;color:#0F172A;">Productgegevens</span>'
                '</div>'
                '<div style="font-size:12px;color:#94A3B8;margin-bottom:16px;">Vul de basisinformatie van het product in.</div>'
                '<div style="height:1px;background:#F1F5F9;margin-bottom:20px;"></div>',
                unsafe_allow_html=True)

            pf1, pf2 = st.columns(2)

            with pf1:
                st.markdown(
                    '<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">'
                    'Productnaam <span style="color:#F59E0B;font-weight:700;">*</span></div>',
                    unsafe_allow_html=True)
                p_naam = st.text_input("Productnaam *", placeholder="Bijv. Muurverf Mat Wit",
                                       label_visibility="collapsed", key="pn_naam")

                st.markdown(
                    '<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:10px;">'
                    'Prijs (€) <span style="color:#F59E0B;font-weight:700;">*</span></div>',
                    unsafe_allow_html=True)
                p_prijs = st.number_input("Prijs (€)", min_value=0.0, step=0.5, format="%.2f",
                                          label_visibility="collapsed", key="pn_prijs")

                st.markdown(
                    '<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:10px;">'
                    'Verbruik (per m² / per meter) <span style="color:#F59E0B;font-weight:700;">*</span></div>',
                    unsafe_allow_html=True)
                p_verbruik = st.number_input("Verbruik", min_value=0.001,
                                             step=0.01, format="%.3f", label_visibility="collapsed", key="pn_verbruik",
                                             help="Voor Kit en Afplakken geldt: per strekkende meter. Overige producten: per m².")

                st.markdown(
                    '<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:10px;">'
                    'Inhoud verpakking <span style="color:#F59E0B;font-weight:700;">*</span></div>',
                    unsafe_allow_html=True)
                p_inhoud = st.number_input("Inhoud", min_value=0.001,
                                           step=0.5, format="%.3f", label_visibility="collapsed", key="pn_inhoud",
                                           help="Hoeveelheid per verpakking, bijv. 10 liter, 310 ml, 50 meter, 10 vellen.")

            with pf2:
                st.markdown(
                    '<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">'
                    'Eenheid <span style="color:#F59E0B;font-weight:700;">*</span></div>',
                    unsafe_allow_html=True)
                p_eenheid = st.selectbox("Eenheid", ["liter", "tube", "rol", "vel", "stuk", "m²", "kg"],
                                         label_visibility="collapsed", key="pn_eenheid")

                st.markdown(
                    '<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:10px;">'
                    'Categorie <span style="color:#F59E0B;font-weight:700;">*</span></div>',
                    unsafe_allow_html=True)
                p_categorie = st.selectbox("Categorie", ["Verf", "Primer", "Kit", "Afplakken", "Gereedschap", "Schuurpapier", "Behang", "Overig"],
                                           label_visibility="collapsed", key="pn_categorie")

                st.markdown(
                    '<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;margin-top:10px;">'
                    'Werkzaamheden <span style="color:#F59E0B;font-weight:700;">*</span></div>',
                    unsafe_allow_html=True)
                p_werkzaamheden = st.multiselect("Werkzaamheden", WERKZAAMHEDEN_OPTIES,
                                                  label_visibility="collapsed", key="pn_werkzaamheden")

                # ── Project selecteren — direct ónder Werkzaamheden. Koppelt het product aan
                #    één project (alleen daar beschikbaar in calculaties); "Algemeen" = globaal. ──
                _proj_lijst = st.session_state.get("projecten", [])
                _proj_opties = [None] + [p["id"] for p in _proj_lijst]
                st.markdown(
                    '<div style="font-size:13px;font-weight:500;color:#374151;margin-top:10px;margin-bottom:2px;">'
                    'Project selecteren</div>', unsafe_allow_html=True)
                p_project_id = st.selectbox(
                    "Project selecteren", _proj_opties,
                    format_func=lambda _pid: ("Algemeen — beschikbaar in alle projecten" if _pid is None
                                              else next((p["naam"] for p in _proj_lijst if p["id"] == _pid),
                                                        f"Project {_pid}")),
                    label_visibility="collapsed", key="pn_project",
                    help="Koppel dit product aan één project — het is dan alleen daar beschikbaar in "
                         "calculaties. Kies 'Algemeen' voor een product dat in alle projecten bruikbaar is.")

            # ── Actiebalk ──
            st.markdown('<div style="height:1px;background:#F1F5F9;margin-top:20px;margin-bottom:16px;"></div>',
                        unsafe_allow_html=True)
            _, _btn_ann, _btn_opl = st.columns([6, 1.4, 2])
            with _btn_ann:
                _annuleer = st.form_submit_button("Annuleren", use_container_width=True)
            with _btn_opl:
                _opslaan = st.form_submit_button("✓  Product toevoegen",
                                                 use_container_width=True, type="primary")

            if _opslaan:
                if p_naam and p_werkzaamheden and p_inhoud > 0:
                    nieuw_id = max((p["id"] for p in st.session_state.producten), default=0) + 1
                    _nieuw_prod = {
                        "id": nieuw_id, "naam": p_naam, "prijs": p_prijs,
                        "verbruik": p_verbruik, "eenheid": p_eenheid,
                        "categorie": p_categorie, "werkzaamheden": p_werkzaamheden,
                        "inhoud": p_inhoud,
                        # Projectkoppeling: None = globaal (alle projecten), anders alleen dit project.
                        "project_id": p_project_id,
                        "actief": True,
                    }
                    # Inhoud-eenheid is niet meer los invoerbaar → afgeleid uit eenheid/categorie
                    # (zelfde migratie-logica als voor bestaande producten).
                    _nieuw_prod["inhoud_eenheid"] = _default_inhoud(_nieuw_prod)[1]
                    # verbruik-eenheid (m²/meter) automatisch afleiden uit categorie/werkzaamheden
                    _nieuw_prod["verbruik_eenheid"] = verbruik_eenheid_van(_nieuw_prod)
                    st.session_state.producten.append(_nieuw_prod)
                    save_data()
                    st.session_state["pn_import_flash"] = ("success", f"Product '{p_naam}' toegevoegd!")
                    st.session_state["pn_reset"] = True   # formulier leegmaken na toevoegen
                    st.rerun()
                else:
                    ui_alert("Vul naam, werkzaamheden en inhoud in.", "error")
            elif _annuleer:
                st.session_state["pn_reset"] = True       # formulier leegmaken
                st.rerun()

# =====================================================
# PERSONEEL
# =====================================================

elif selected == "Personeel":

    # Paginatie state
    if "personeel_pagina" not in st.session_state:
        st.session_state.personeel_pagina = 1
    if "personeel_zoek" not in st.session_state:
        st.session_state.personeel_zoek = ""
    if "ps_edit_id" not in st.session_state:
        st.session_state.ps_edit_id = None
    if "ps_del_id" not in st.session_state:
        st.session_state.ps_del_id = None

    PERS_PER_PAG = 10

    AVATAR_KLEUREN_P = [
        ("#2563EB","#DBEAFE"), ("#059669","#D1FAE5"), ("#7C3AED","#EDE9FE"),
        ("#DC2626","#FEE2E2"), ("#D97706","#FEF3C7"), ("#0891B2","#CFFAFE"),
    ]

    def get_init_p(naam):
        d = naam.strip().split()
        return (d[0][0] + d[-1][0]).upper() if len(d) >= 2 else naam[:2].upper()

    # ── CSS ──
    _inject_page_css("""
    /* Header */
    .pers-header-grid div[data-testid="stHorizontalBlock"] {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        margin-bottom: 0 !important;
        transform: none !important;
    }
    /* Tabel header rij */
    .pers-tabel-header {
        display: flex;
        padding: 9px 16px;
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 12px 12px 0 0;
        border-bottom: 1px solid #E8EFF5;
        gap: 0;
    }
    .pers-th {
        font-size: 10.5px;
        font-weight: 700;
        color: #94A3B8;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    /* Medewerker rij data */
    .pers-row {
        display: flex;
        align-items: center;
        padding: 13px 16px;
        background: white;
        border: 1px solid #E2E8F0;
        border-top: none;
        gap: 0;
        min-height: 64px;
        transition: background 0.12s ease;
    }
    .pers-row:hover { background: #F8FBFF; }
    .pers-row.even { background: #FAFBFC; }
    .pers-row.even:hover { background: #F0F7FF; }
    /* Avatar */
    .pers-avatar {
        width: 38px; height: 38px; min-width: 38px;
        border-radius: 10px;
        display: flex; align-items: center; justify-content: center;
        font-size: 13px; font-weight: 700; flex-shrink: 0;
    }
    /* Status badges */
    .pers-badge-actief   { background:#DCFCE7; color:#166534; padding:3px 10px; border-radius:99px; font-size:11px; font-weight:600; white-space:nowrap; }
    .pers-badge-inactief { background:#F3F4F6; color:#6B7280; padding:3px 10px; border-radius:99px; font-size:11px; font-weight:600; white-space:nowrap; }
    .pers-badge-verlof   { background:#FEF3C7; color:#92400E; padding:3px 10px; border-radius:99px; font-size:11px; font-weight:600; white-space:nowrap; }
    .pers-badge-ziek     { background:#FEE2E2; color:#991B1B; padding:3px 10px; border-radius:99px; font-size:11px; font-weight:600; white-space:nowrap; }
    /* Actie icon knoppen */
    .pers-icon-btn {
        width: 32px; height: 32px; border-radius: 8px;
        background: #F8FAFC; border: 1px solid #E2E8F0;
        display: flex; align-items: center; justify-content: center;
        font-size: 13px; cursor: pointer; transition: all 0.13s ease;
    }
    .pers-icon-btn:hover { background: #EFF6FF; border-color: #BFDBFE; }
    .pers-icon-btn.del:hover { background: #FFF5F5; border-color: #FECACA; }
    /* Delete knop styling */
    .pers-del-btn div[data-testid="stButton"] > button {
        width: 32px !important; height: 32px !important; min-width: 32px !important;
        padding: 0 !important; border-radius: 8px !important; font-size: 13px !important;
        background: #F8FAFC !important; border: 1px solid #E2E8F0 !important;
        color: #64748B !important; box-shadow: none !important; transform: none !important;
        line-height: 1 !important;
    }
    .pers-del-btn div[data-testid="stButton"] > button:hover {
        background: #FFF5F5 !important; border-color: #FECACA !important;
        color: #EF4444 !important; box-shadow: none !important; transform: none !important;
    }
    /* Formulier cards */
    .pers-form-card {
        background: white;
        border: 1px solid #E2E8F0;
        border-radius: 14px;
        padding: 20px 22px;
        margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .pers-form-card-header {
        display: flex; align-items: center; gap: 8px;
        margin-bottom: 14px; padding-bottom: 11px;
        border-bottom: 1px solid #F1F5F9;
        font-size: 13px; font-weight: 600; color: #0F172A;
    }
    .pers-form-icon {
        width: 26px; height: 26px; border-radius: 7px;
        display: flex; align-items: center; justify-content: center; font-size: 13px;
    }
    /* Paginatie balk */
    .pers-pag-wrap {
        background: white;
        border: 1px solid #E2E8F0;
        border-top: none;
        border-radius: 0 0 12px 12px;
        padding: 11px 16px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    """)
    _inject_keyed_css("pers_del_stijl", """
    /* Verwijder-bevestiging: Verwijderen rood */
    div[data-testid="stColumn"]:has(span.ps-del-confirm-mk) .stButton > button {
        background: white !important;
        color: #DC2626 !important;
        border: 1.5px solid #FEE2E2 !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.ps-del-confirm-mk) .stButton > button:hover {
        background: #FFF5F5 !important;
        border-color: #FECACA !important;
        transform: none !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.ps-del-confirm-mk) .stButton > button::before {
        font-family:"bootstrap-icons"; content:"\\f5de"; margin-right:7px; font-size:14px;
        vertical-align:-0.1em; font-style:normal; font-weight:400;
    }
    /* Verwijder-bevestiging: Annuleren wit/neutraal */
    div[data-testid="stColumn"]:has(span.ps-del-cancel-mk) .stButton > button {
        background: white !important;
        color: #475569 !important;
        border: 1px solid #E2E8F0 !important;
        box-shadow: none !important;
    }
    div[data-testid="stColumn"]:has(span.ps-del-cancel-mk) .stButton > button:hover {
        background: #F8FAFC !important;
        border-color: #CBD5E1 !important;
        transform: none !important;
        box-shadow: none !important;
    }
    """)
    _inject_keyed_css("personeel_overlay", """
    /* ── Personeel overlay onzichtbare actie-knoppen ── */
    div[data-testid="stLayoutWrapper"]:has(span.ps-ovl-mk){height:0 !important;overflow:visible !important;margin:-16px 0 !important;padding:0 !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk){margin-top:-62px !important;height:62px !important;background:transparent !important;gap:0 !important;position:relative !important;z-index:50 !important;pointer-events:none !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk) div[data-testid="stColumn"]{background:transparent !important;padding:0 !important;min-width:0 !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk) div[data-testid="stVerticalBlock"]{background:transparent !important;gap:0 !important;height:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk) div[data-testid="stElementContainer"]{margin:0 !important;padding:0 !important;height:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk) div[data-testid="stMarkdownContainer"]{display:none !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk) div[data-testid="stColumn"]:not(:first-child){pointer-events:auto !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk) [data-testid="stElementContainer"]{width:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk) [data-testid="stButton"]{width:100% !important;}
    div[data-testid="stHorizontalBlock"]:has(span.ps-ovl-mk) button{opacity:0 !important;width:100% !important;height:62px !important;cursor:pointer !important;pointer-events:auto !important;background:transparent !important;border:none !important;box-shadow:none !important;margin:0 !important;padding:0 !important;transform:none !important;}
    /* ── Personeel inline edit formulier ── */
    [data-testid="stForm"]:has(span.ps-inline-edit-mk){background:#FFFFFF !important;border:1px solid #BFDBFE !important;border-radius:14px !important;box-shadow:0 2px 8px rgba(37,99,235,0.07) !important;}
    [data-testid="stForm"]:has(span.ps-inline-edit-mk) [data-testid="stVerticalBlock"],[data-testid="stForm"]:has(span.ps-inline-edit-mk) [data-testid="stHorizontalBlock"],[data-testid="stForm"]:has(span.ps-inline-edit-mk) [data-testid="stMarkdownContainer"],[data-testid="stForm"]:has(span.ps-inline-edit-mk) [data-testid="stColumn"]{background:#FFFFFF !important;}
    [data-testid="stForm"]:has(span.ps-inline-edit-mk) button [data-testid="stMarkdownContainer"]{background:transparent !important;}
    """)

    # ── Pagina titel ──
    ht1, ht2 = st.columns([5, 2])
    with ht1:
        st.markdown(
            '<div style="margin-bottom:20px;">'
            '<div style="font-size:26px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;line-height:1.2;">Personeel</div>'
            '<div style="font-size:12.5px;color:#94A3B8;font-weight:400;margin-top:3px;">Beheer al je medewerkers, functies en uurtarieven.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    with ht2:
        pass  # ruimte voor knop in tab

    tab_ovz, tab_nieuw = st.tabs(["Overzicht", "+ Nieuw personeelslid"])

    # UX: na het toevoegen van een personeelslid automatisch terug naar de Overzicht-tab.
    # st.tabs kent geen programmatische selectie; we klikken de Overzicht-tab éénmalig via
    # JS (frontend-only, geen rerun) zodra de vlag is gezet. Alleen op de Personeel-pagina
    # actief, dus geen invloed op de tabs van andere pagina's.
    if st.session_state.pop("ps_goto_overzicht", False):
        ga_naar_tab("Overzicht")

    # ══════════════════════════════════════════
    # OVERZICHT TAB
    # ══════════════════════════════════════════
    with tab_ovz:
        # Succesmelding na toevoegen (flash) — getoond op het overzicht, niet meer op het
        # (inmiddels gesloten) invoerformulier.
        _ps_flash = st.session_state.pop("ps_flash", None)
        if _ps_flash:
            ui_alert(_ps_flash, "success")

        # Filters met labels — zelfde stijl als klanten pagina
        zf1, zf2, zf3, zf4 = st.columns([3, 1.5, 1.5, 1.5])
        with zf1:
            zoek_mw = st.text_input("Zoek", placeholder="Zoek medewerker, functie of telefoonnummer…",
                                    key="pers_zoek")
        with zf2:
            filter_status = st.selectbox("Status", ["Alle statussen", "Actief", "Inactief", "Verlof", "Ziek"],
                                         key="pers_status_filter")
        with zf3:
            filter_functie = st.selectbox("Functie", ["Alle functies", "Uitvoerder", "Schilder", "Leerling", "ZZP'er"],
                                          key="pers_functie_filter")
        with zf4:
            sorteer_mw = st.selectbox("Sorteer op", ["Naam A–Z", "Naam Z–A", "Hoogste tarief", "Laagste tarief"],
                                      key="pers_sorteer")

        # Filteren
        mw_lijst = st.session_state.personeel
        if zoek_mw:
            q = zoek_mw.lower()
            mw_lijst = [m for m in mw_lijst if q in m["naam"].lower()
                        or q in m.get("functie","").lower()
                        or q in m.get("telefoon","").lower()]
        if filter_status != "Alle statussen":
            mw_lijst = [m for m in mw_lijst
                        if m.get("status", "Actief") == filter_status]
        if filter_functie != "Alle functies":
            mw_lijst = [m for m in mw_lijst if m.get("functie","") == filter_functie]

        # Sorteren
        if sorteer_mw == "Naam A–Z":
            mw_lijst = sorted(mw_lijst, key=lambda m: m["naam"])
        elif sorteer_mw == "Naam Z–A":
            mw_lijst = sorted(mw_lijst, key=lambda m: m["naam"], reverse=True)
        elif sorteer_mw == "Hoogste tarief":
            mw_lijst = sorted(mw_lijst, key=lambda m: m["uurtarief"], reverse=True)
        elif sorteer_mw == "Laagste tarief":
            mw_lijst = sorted(mw_lijst, key=lambda m: m["uurtarief"])

        totaal_mw = len(mw_lijst)
        pagina_mw = st.session_state.personeel_pagina
        max_pag_mw = max(1, (totaal_mw + PERS_PER_PAG - 1) // PERS_PER_PAG)
        pagina_mw = min(pagina_mw, max_pag_mw)
        start_mw = (pagina_mw - 1) * PERS_PER_PAG
        einde_mw = min(start_mw + PERS_PER_PAG, totaal_mw)
        mw_pagina = mw_lijst[start_mw:einde_mw]

        st.markdown(
            f'<div style="font-size:13px;color:#64748B;margin-bottom:10px;font-weight:500;">'
            f'{totaal_mw} medewerker{"s" if totaal_mw != 1 else ""}</div>',
            unsafe_allow_html=True,
        )

        if not mw_lijst:
            ui_alert("Geen medewerkers gevonden.", "info")
        else:
            # CSS — enkel voor delete expander knop stijl
            _inject_page_css("""
            div[data-testid="stExpander"] summary {
                font-size: 13px !important;
                color: #64748B !important;
            }
            """)

            # Header
            st.markdown("""
            <div class="cf-tbl-head" style="display:flex;padding:10px 16px;background:#F8FAFC;
                        border:1px solid #E2E8F0;border-radius:12px 12px 0 0;border-bottom:1px solid #E8EFF5;">
              <div style="flex:2.8;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Medewerker</div>
              <div style="flex:1.4;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Functie</div>
              <div style="flex:1.4;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Uurtarief</div>
              <div style="flex:1.4;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Telefoon</div>
              <div style="flex:1.1;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Status</div>
              <div style="flex:1.0;font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;">Acties</div>
            </div>
            """, unsafe_allow_html=True)

            # Rijen per stuk renderen (voor overlay knoppen)
            EDIT_BTN = "width:32px;height:32px;min-width:32px;border-radius:8px;background:#F8FAFC;border:1px solid #E2E8F0;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;transition:all 0.12s ease;"

            for i, mw in enumerate(mw_pagina):
                fg, bg  = AVATAR_KLEUREN_P[mw["id"] % len(AVATAR_KLEUREN_P)]
                init    = get_init_p(mw["naam"])
                tel_str = mw.get("telefoon","") or "—"
                ps_status = mw.get("status", "Actief")
                if ps_status == "Verlof":
                    badge = '<span style="background:#FEF9C3;color:#713F12;padding:4px 11px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap;">● Verlof</span>'
                elif ps_status == "Ziek":
                    badge = '<span style="background:#FEE2E2;color:#991B1B;padding:4px 11px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap;">● Ziek</span>'
                elif ps_status == "Inactief":
                    badge = '<span style="background:#F3F4F6;color:#6B7280;padding:4px 11px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap;">● Inactief</span>'
                else:
                    badge = '<span style="background:#DCFCE7;color:#166534;padding:4px 11px;border-radius:99px;font-size:12px;font-weight:600;white-space:nowrap;">● Actief</span>'
                rij_bg  = "#FAFBFC" if i % 2 == 1 else "white"
                is_last = i == len(mw_pagina) - 1
                bot_r   = "0 0 12px 12px" if is_last else "0"

                st.markdown(f"""
                <div class="cf-tbl-row" style="display:flex;align-items:center;background:{rij_bg};
                            border:1px solid #E2E8F0;border-top:none;
                            border-radius:{bot_r};padding:16px 16px 19px 16px;min-height:71px;">
                  <div style="flex:2.8;display:flex;align-items:center;gap:10px;overflow:hidden;padding-right:12px;">
                    <div style="width:36px;height:36px;min-width:36px;border-radius:9px;background:{bg};color:{fg};
                                display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;">{h(init)}</div>
                    <div style="overflow:hidden;">
                      <div style="font-size:14px;font-weight:600;color:#0F172A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{h(mw['naam'])}</div>
                      <div style="font-size:11px;color:#94A3B8;margin-top:2px;">{h(mw.get('email','') or '')}</div>
                    </div>
                  </div>
                  <div style="flex:1.4;font-size:14px;color:#374151;padding-right:12px;">{h(mw.get('functie','—'))}</div>
                  <div style="flex:1.4;font-size:14px;color:#0F172A;font-weight:500;font-family:'DM Mono',monospace;padding-right:12px;">€ {mw['uurtarief']:.2f}/uur</div>
                  <div style="flex:1.4;font-size:14px;color:#374151;padding-right:12px;">{h(tel_str)}</div>
                  <div style="flex:1.1;padding-right:12px;">{badge}</div>
                  <div style="flex:1.0;display:inline-flex;align-items:center;gap:6px;">
                    <div style="{EDIT_BTN}" title="Medewerker bewerken"><i class="bi bi-pencil" style="font-size:13px;color:#CBD5E1;"></i></div>
                    <div style="{EDIT_BTN}" title="Medewerker verwijderen"><i class="bi bi-trash3" style="font-size:13px;color:#CBD5E1;"></i></div>
                  </div>
                </div>""", unsafe_allow_html=True)
                # ── Onzichtbare overlay-knoppen ──
                _s, _e, _d = st.columns([14.8, 0.6, 1.4])
                with _e:
                    st.markdown('<span class="ps-ovl-mk" style="display:none;"></span>', unsafe_allow_html=True)
                    if st.button("e", key=f"ps_e_{mw['id']}"):
                        st.session_state.ps_edit_id = mw['id'] if st.session_state.ps_edit_id != mw['id'] else None
                        st.session_state.ps_del_id = None
                        st.rerun()
                with _d:
                    if st.button("d", key=f"ps_d_{mw['id']}"):
                        st.session_state.ps_del_id = mw['id'] if st.session_state.ps_del_id != mw['id'] else None
                        st.session_state.ps_edit_id = None
                        st.rerun()

            # ── Inline edit formulier ──
            if st.session_state.ps_edit_id is not None:
                edit_mw = next((m for m in st.session_state.personeel
                                if m["id"] == st.session_state.ps_edit_id), None)
                if edit_mw:
                    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
                    with st.form("ps_edit_inline_form"):
                        st.markdown('<span class="ps-inline-edit-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                            f'<i class="bi bi-pencil-square" style="font-size:15px;color:#2563EB;"></i>'
                            f'<span style="font-size:14px;font-weight:700;color:#0F172A;">Medewerker bewerken — {h(edit_mw["naam"])}</span>'
                            f'</div>',
                            unsafe_allow_html=True)
                        em1, em2 = st.columns(2)
                        with em1:
                            emn = st.text_input("Naam *", value=edit_mw["naam"])
                            emf = st.selectbox("Functie",
                                               ["Uitvoerder","Schilder","Leerling","ZZP'er"],
                                               index=["Uitvoerder","Schilder","Leerling","ZZP'er"].index(edit_mw.get("functie","Schilder"))
                                               if edit_mw.get("functie","Schilder") in ["Uitvoerder","Schilder","Leerling","ZZP'er"] else 0)
                            emu = st.number_input("Uurtarief (€)", value=float(edit_mw["uurtarief"]),
                                                  min_value=0.0, step=0.5, format="%.2f")
                        with em2:
                            emt  = st.text_input("Telefoon", value=edit_mw.get("telefoon",""))
                            eme  = st.text_input("Email", value=edit_mw.get("email",""))
                            _ps_opties = ["Actief", "Inactief", "Verlof", "Ziek"]
                            _ps_huidig = edit_mw.get("status", "Actief")
                            _ps_huidig = _ps_huidig if _ps_huidig in _ps_opties else "Actief"
                            emas = st.selectbox("Status", _ps_opties,
                                                index=_ps_opties.index(_ps_huidig))
                        st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-top:10px;margin-bottom:4px;">Gekoppelde projecten</div>', unsafe_allow_html=True)
                        _proj_opties = [p["id"] for p in st.session_state.projecten]
                        # "Algemeen" (ZZP) staat als eerste optie IN het dropdown — zelfde werking
                        # als voorheen het aparte vinkje: selecteren = telt op elk project mee.
                        _em_default = ([_ALGEMEEN_OPT] if edit_mw.get("algemeen")
                                       # SP-005: ids van verwijderde projecten wegfilteren —
                                       # een default buiten de options crasht (StreamlitAPIException)
                                       else [pid for pid in edit_mw.get("project_ids", []) if pid in _proj_opties])
                        em_koppeling = st.multiselect(
                            "Gekoppelde projecten",
                            options=[_ALGEMEEN_OPT] + _proj_opties,
                            format_func=_koppel_label,
                            default=_em_default,
                            placeholder="Selecteer projecten of 'Algemeen'…",
                            label_visibility="collapsed",
                        )
                        em_algemeen = _ALGEMEEN_OPT in em_koppeling
                        em_proj_ids = [] if em_algemeen else em_koppeling
                        save_c, cancel_c, _ = st.columns([1, 1, 5])
                        with save_c:
                            st.markdown('<span class="cf-ico-mk cf-ico-save-mk"></span>', unsafe_allow_html=True)
                            if st.form_submit_button("Opslaan", use_container_width=True, type="primary"):
                                # Zelfde centrale validatie als bij toevoegen → geen ongeldige
                                # medewerker opslaan.
                                _mwe_fout = eerste_validatiefout(
                                    valideer_tekst(emn, "Naam", min_len=2),
                                    valideer_telefoon(emt, verplicht=False),
                                    valideer_email(eme, verplicht=False),
                                    valideer_getal(emu, "uurtarief", "Uurtarief", toestaan_nul=False),
                                )
                                if _mwe_fout:
                                    ui_alert(_mwe_fout, "error")
                                else:
                                    for m in st.session_state.personeel:
                                        if m["id"] == edit_mw["id"]:
                                            m["naam"]        = emn
                                            m["functie"]     = emf
                                            m["uurtarief"]   = emu
                                            m["telefoon"]    = emt
                                            m["email"]       = eme
                                            m["actief"]      = (emas == "Actief")
                                            m["status"]      = emas
                                            m["algemeen"]    = em_algemeen
                                            # Algemeen → losse koppelingen niet nodig (telt overal mee).
                                            m["project_ids"] = [] if em_algemeen else em_proj_ids
                                            break
                                    st.session_state.ps_edit_id = None
                                    save_data()
                                    st.toast(f"'{emn}' bijgewerkt!")
                                    st.rerun()
                        with cancel_c:
                            st.markdown('<span class="cf-ico-mk"></span>', unsafe_allow_html=True)
                            if st.form_submit_button("Annuleren", use_container_width=True):
                                st.session_state.ps_edit_id = None
                                st.rerun()

            # ── Inline delete bevestiging ──
            if st.session_state.ps_del_id is not None:
                del_mw = next((m for m in st.session_state.personeel
                               if m["id"] == st.session_state.ps_del_id), None)
                if del_mw:
                    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
                    st.markdown(
                        f'<div style="background:#FFF5F5;border:1px solid #FECACA;border-radius:12px;padding:16px 18px;">'
                        f'<div style="font-size:14px;font-weight:700;color:#DC2626;margin-bottom:6px;">'
                        f'<i class="bi bi-exclamation-triangle" style="margin-right:6px;"></i>Medewerker verwijderen?</div>'
                        f'<div style="font-size:13px;color:#374151;">Weet je zeker dat je <strong>{h(del_mw["naam"])}</strong> wilt verwijderen?</div>'
                        f'</div>',
                        unsafe_allow_html=True)
                    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
                    dc1, dc2, _ = st.columns([1, 1, 6])
                    with dc1:
                        st.markdown('<span class="ps-del-confirm-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Verwijderen", key="ps_del_confirm", type="primary", use_container_width=True):
                            del_idx = next((j for j, m in enumerate(st.session_state.personeel) if m["id"] == del_mw["id"]), None)
                            if del_idx is not None:
                                st.session_state.personeel.pop(del_idx)
                            st.session_state.ps_del_id = None
                            st.session_state.personeel_pagina = 1
                            save_data()
                            st.toast(f"'{del_mw['naam']}' verwijderd.")
                            st.rerun()
                    with dc2:
                        st.markdown('<span class="ps-del-cancel-mk" style="display:none;"></span>', unsafe_allow_html=True)
                        if st.button("Annuleren", key="ps_del_cancel", use_container_width=True):
                            st.session_state.ps_del_id = None
                            st.rerun()

            # Paginatie
            st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)
            pag_l, pag_m = st.columns([3, 5])
            with pag_l:
                st.markdown(
                    f'<div style="font-size:12px;color:#94A3B8;padding-top:6px;">Toont {start_mw+1}–{einde_mw} van {totaal_mw} medewerkers</div>',
                    unsafe_allow_html=True)
            with pag_m:
                if max_pag_mw > 1:
                    pcols = st.columns([1.2] + [0.6]*max_pag_mw + [1.4])
                    with pcols[0]:
                        if st.button("← Vorige", key="pers_prev", disabled=(pagina_mw<=1)):
                            st.session_state.personeel_pagina = pagina_mw - 1
                            st.rerun()
                    for p in range(1, max_pag_mw+1):
                        with pcols[p]:
                            if st.button(f"**{p}**" if p==pagina_mw else str(p), key=f"pers_pag_{p}"):
                                st.session_state.personeel_pagina = p
                                st.rerun()
                    with pcols[max_pag_mw+1]:
                        if st.button("Volgende →", key="pers_next", disabled=(pagina_mw>=max_pag_mw)):
                            st.session_state.personeel_pagina = pagina_mw + 1
                            st.rerun()

    # ══════════════════════════════════════════
    # NIEUW PERSONEELSLID TAB
    # ══════════════════════════════════════════
    with tab_nieuw:
        st.markdown('<div style="font-size:22px;font-weight:700;color:#0F172A;letter-spacing:-0.3px;margin-bottom:2px;">Nieuw personeelslid toevoegen</div>', unsafe_allow_html=True)
        st.markdown('<div style="font-size:12.5px;color:#94A3B8;margin-bottom:20px;">Vul de gegevens van de nieuwe medewerker in.</div>', unsafe_allow_html=True)

        _inject_keyed_css("pers_nieuw", """
        /* ── Form: grote buitenste border weg (Streamlit 1.55) ── */
        div[data-testid="stForm"]:has(span.ps-card-m) {
            border: none !important;
            box-shadow: none !important;
            background: transparent !important;
        }
        /* ── Witte cards — Streamlit 1.55: stColumn > stVerticalBlock > stLayoutWrapper > stVerticalBlock ── */
        div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.ps-card-m) {
            background: #FFFFFF !important;
            border: 1px solid #E8EFF5 !important;
            border-radius: 14px !important;
            box-shadow: 0 1px 4px rgba(0,0,0,0.05) !important;
        }
        div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.ps-card-m) div[data-testid="stMarkdownContainer"],
        div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.ps-card-m) div[data-testid="stHorizontalBlock"],
        div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.ps-card-m) div[data-testid="stColumn"] {
            background: #FFFFFF !important;
        }
        /* ── Donkerblauwe opslaan knop ── */
        div[data-testid="stForm"]:has(span.ps-card-m) button[kind="primaryFormSubmit"] {
            background: #081A36 !important;
            border-color: #081A36 !important;
            color: white !important;
        }
        div[data-testid="stForm"]:has(span.ps-card-m) button[kind="primaryFormSubmit"]:hover {
            background: #041124 !important;
            border-color: #041124 !important;
        }
        """)

        with st.form("nieuw_personeel_form", clear_on_submit=True):

            rij1_l, rij1_r = st.columns(2)
            rij2_l, rij2_r = st.columns(2)

            with rij1_l:
                with st.container(border=True):
                    st.markdown(
                        '<span class="ps-card-m" style="display:none;"></span>'
                        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                        '<i class="bi bi-person" style="font-size:16px;color:#2563EB;"></i>'
                        '<span style="font-size:14px;font-weight:700;color:#0F172A;">Contactgegevens</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Naam <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                    m_naam  = st.text_input("Naam", placeholder="Bijv. Jan de Vries", label_visibility="collapsed")
                    m_tel   = st.text_input("Telefoonnummer", placeholder="Bijv. 06-12345678")
                    m_email = st.text_input("E-mailadres (optioneel)", placeholder="Bijv. jan@bedrijf.nl")

            with rij1_r:
                with st.container(border=True):
                    st.markdown(
                        '<span class="ps-card-m" style="display:none;"></span>'
                        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                        '<i class="bi bi-pencil" style="font-size:16px;color:#2563EB;"></i>'
                        '<span style="font-size:14px;font-weight:700;color:#0F172A;">Functie &amp; Tarief</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Functie <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                    m_functie = st.selectbox("Functie", ["Uitvoerder", "Schilder", "Leerling", "ZZP'er"], label_visibility="collapsed")
                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Uurtarief (€) <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                    m_uur     = st.number_input("Uurtarief (€)", min_value=0.0, value=0.0, step=0.50, format="%.2f", label_visibility="collapsed")

            with rij2_l:
                with st.container(border=True):
                    st.markdown(
                        '<span class="ps-card-m" style="display:none;"></span>'
                        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                        '<i class="bi bi-paperclip" style="font-size:16px;color:#2563EB;"></i>'
                        '<span style="font-size:14px;font-weight:700;color:#0F172A;">Overige informatie</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown('<div style="font-size:13px;font-weight:500;color:#374151;margin-bottom:2px;">Status <span style="color:#F59E0B;font-weight:700;">*</span></div>', unsafe_allow_html=True)
                    m_status = st.selectbox("Status", ["Actief", "Inactief", "Verlof", "Ziek"], label_visibility="collapsed")
                    m_start  = st.date_input("Startdatum", value=None)

            with rij2_r:
                with st.container(border=True):
                    st.markdown(
                        '<span class="ps-card-m" style="display:none;"></span>'
                        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;">'
                        '<i class="bi bi-journal-text" style="font-size:16px;color:#2563EB;"></i>'
                        '<span style="font-size:14px;font-weight:700;color:#0F172A;">Notities</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    m_notities = st.text_area("Notities", placeholder="Bijv. Opmerkingen…", height=80)
                    m_intern   = st.text_area("Interne opmerkingen", placeholder="Interne notities…", height=80)

            with st.container(border=True):
                st.markdown(
                    '<span class="ps-card-m" style="display:none;"></span>'
                    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
                    '<i class="bi bi-diagram-3" style="font-size:16px;color:#2563EB;"></i>'
                    '<span style="font-size:14px;font-weight:700;color:#0F172A;">Projectkoppeling</span>'
                    '</div>'
                    '<div style="font-size:12.5px;color:#94A3B8;margin-bottom:10px;">Koppel dit personeelslid aan één of meer projecten, of kies <strong>Algemeen</strong> zodat deze op álle projecten meetelt (handig voor ZZP-ers). De uurtarieven van gekoppeld personeel worden gebruikt in de projectcalculaties.</div>',
                    unsafe_allow_html=True,
                )
                m_koppeling = st.multiselect(
                    "Gekoppelde projecten",
                    # "Algemeen" (ZZP) als eerste optie in het dropdown → telt op elk project mee.
                    options=[_ALGEMEEN_OPT] + [p["id"] for p in st.session_state.projecten],
                    format_func=_koppel_label,
                    default=[],
                    placeholder="Selecteer projecten of 'Algemeen'…",
                    label_visibility="collapsed",
                )
                m_algemeen = _ALGEMEEN_OPT in m_koppeling
                m_project_ids = [] if m_algemeen else m_koppeling

            st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)
            _, btn_ann, btn_opl = st.columns([6, 1.4, 2])
            with btn_ann:
                st.form_submit_button("Annuleren", use_container_width=True)
            with btn_opl:
                opslaan = st.form_submit_button("✓  Personeelslid opslaan",
                                                use_container_width=True, type="primary")

            if opslaan:
                # Centrale invoervalidatie (beta-blocker): dezelfde validators als bij
                # Klanten. Verplicht: naam + uurtarief (>0); telefoon/e-mail optioneel maar
                # wél op formaat gecontroleerd.
                _mw_fout = eerste_validatiefout(
                    valideer_tekst(m_naam, "Naam", min_len=2),
                    valideer_telefoon(m_tel, verplicht=False),
                    valideer_email(m_email, verplicht=False),
                    valideer_getal(m_uur, "uurtarief", "Uurtarief", toestaan_nul=False),
                )
                if not _mw_fout:
                    nieuw_id = max((m["id"] for m in st.session_state.personeel), default=0) + 1
                    st.session_state.personeel.append({
                        "id":          nieuw_id,
                        "naam":        m_naam,
                        "functie":     m_functie,
                        "uurtarief":   m_uur,
                        "telefoon":    m_tel,
                        "email":       m_email,
                        "actief":      m_status == "Actief",
                        "status":      m_status,
                        "notities":    m_notities,
                        "algemeen":    m_algemeen,
                        # Algemeen → geen losse koppelingen nodig (telt overal mee).
                        "project_ids": [] if m_algemeen else m_project_ids,
                    })
                    save_data()
                    # UX: succesmelding via flash + automatisch terug naar het overzicht.
                    # Het formulier sluit (clear_on_submit + tabwissel) en het nieuwe lid is
                    # direct zichtbaar op het overzicht — geen refresh of paginawissel nodig.
                    st.session_state["ps_flash"] = f"'{m_naam}' succesvol toegevoegd!"
                    st.session_state["ps_goto_overzicht"] = True
                    st.rerun()
                else:
                    ui_alert(_mw_fout, "error")

        # JS: witte cards + gelijke hoogte rij-cards + form-border weg
        # Streamlit 1.55: card = stLayoutWrapper > stVerticalBlock (al dan niet via stColumn)
        _html_component("""<script>(function(){
function fix(){
    var p=window.parent.document;
    /* Form-border weg */
    p.querySelectorAll('[data-testid="stForm"]').forEach(function(f){
        if(f.querySelector('span.ps-card-m')){
            f.style.setProperty('border','none','important');
            f.style.setProperty('background','transparent','important');
            f.style.setProperty('box-shadow','none','important');
        }
    });
    /* Alle ps-card-m containers: ook full-width kaarten buiten stColumn */
    var allCards=Array.from(p.querySelectorAll(
        'div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]'
    )).filter(function(el){return el.querySelector('span.ps-card-m');});
    if(allCards.length<1)return;
    /* Reset min-height voor herberekening */
    allCards.forEach(function(c){c.style.minHeight='';});
    /* Wit + nette stijl voor alle kaarten */
    allCards.forEach(function(c){
        c.style.setProperty('background','#FFFFFF','important');
        c.style.setProperty('background-color','#FFFFFF','important');
        c.style.setProperty('border','1px solid #E8EFF5','important');
        c.style.setProperty('border-radius','14px','important');
        c.style.setProperty('box-shadow','0 1px 4px rgba(0,0,0,0.05)','important');
        c.querySelectorAll('[data-testid="stMarkdownContainer"],[data-testid="stHorizontalBlock"],[data-testid="stColumn"]')
         .forEach(function(el){el.style.setProperty('background','#FFFFFF','important');});
    });
    /* Gelijke hoogte: alleen de 4 rij-cards (die wél via stColumn lopen) */
    var colCards=Array.from(p.querySelectorAll(
        'div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]'
    )).filter(function(el){return el.querySelector('span.ps-card-m');});
    if(colCards.length>=2){
        var maxH=Math.max.apply(null,colCards.map(function(c){return c.getBoundingClientRect().height;}));
        if(maxH>50){colCards.forEach(function(c){c.style.setProperty('min-height',maxH+'px','important');});}
    }
}
fix();
[80,200,500,1000].forEach(function(t){setTimeout(fix,t);});
var obs=new MutationObserver(function(){clearTimeout(window._psCT);window._psCT=setTimeout(fix,60);});
obs.observe(window.parent.document.body,{childList:true,subtree:true});
})();</script>""", height=0, scrolling=False)

# =====================================================
# INSTELLINGEN
# =====================================================

elif selected == "Instellingen":

    inst = st.session_state.instellingen

    # ── Migratie: voeg alle ontbrekende velden toe (backwards-compatible) ──
    _inst_defaults = {
        # Tab 1 — Bedrijfsgegevens
        "kvk": "", "website": "", "postcode": "", "plaats": "",
        "contactpersoon": "", "mobiel": "",
        "email_offertes": "", "email_facturen": "",
        "facebook": "", "instagram": "", "linkedin": "",
        "bedrijfskleur": "#2563EB", "logo_b64": "",
        # Tab 2 — Financieel
        "standaard_uurloon": 45.0, "km_vergoeding": 0.23,
        "min_projectprijs": 0.0, "aanbetaling_pct": 0,
        "factuurtermijn": 30,
        "herinnering1_dagen": 14, "herinnering2_dagen": 7, "herinnering3_dagen": 3,
        # Tab 3 — Offertes
        "offerte_prefix": "OFF", "offerte_startnummer": 1,
        "bedanktekst": "Bedankt voor uw opdracht.",
        "afsluittekst": "Wij kijken uit naar een goede samenwerking.",
        "handtekening": "",
        "pdf_logo_tonen": True, "pdf_handtekening_tonen": True,
        "pdf_btw_tonen": True, "pdf_detailprijzen_tonen": True,
        "pdf_arbeidskosten_tonen": True, "pdf_materiaalkosten_tonen": True,
        "pdf_paginanummers_tonen": True,
        # Klant-offerte standaard zonder interne prijsopbouw; secties aan (punt 3/9/10)
        "pdf_intern_tonen": False, "pdf_inbegrepen_tonen": True,
        "pdf_voorwaarden_volledig_tonen": False,
        # Tab 4 — Facturen
        "factuur_prefix": "FACT", "factuur_startnummer": 1,
        "factuur_tekst": "Bedankt voor uw opdracht. Wij verzoeken u vriendelijk het openstaande bedrag te voldoen.",
        "factuur_voettekst": "Op al onze werkzaamheden zijn onze algemene voorwaarden van toepassing. Bij vragen over deze factuur kunt u contact met ons opnemen.",
        "factuur_herinnering1": "Wij attenderen u erop dat de betalingstermijn van bovenstaande factuur is verstreken.",
        "factuur_herinnering2": "Ondanks onze eerdere herinnering hebben wij uw betaling nog niet ontvangen.",
        "factuur_herinnering3": "Dit is onze laatste aanmaning. Bij uitblijven van betaling zien wij ons genoodzaakt verdere stappen te ondernemen.",
        "factuur_automatische_herinneringen": False,
        # Tab 5 — Toeslagen
        "toeslag_weekend_pct": 50, "toeslag_avond_pct": 25,
        "toeslag_winter_pct": 10, "toeslag_reis_pct": 5,
        # Tab 6 — Voorkeuren
        "taal": "Nederlands", "datumweergave": "DD-MM-JJJJ", "valuta": "Euro (€)",
        "dashboard_periode": "Huidige maand", "dashboard_filter": "Alle projecten",
        "std_project_status": "Concept", "std_sorteervolgorde": "Nieuwste eerst",
        "startpagina": "Dashboard", "thema": "Licht",
        "decimalen": 2, "compacte_weergave": False,
    }
    for _k, _v in _inst_defaults.items():
        if _k not in inst:
            inst[_k] = _v

    # ── Premium header ──
    st.markdown(
        '<div style="margin-bottom:24px;">'
        '<div style="font-size:26px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;">Instellingen</div>'
        '<div style="font-size:13px;color:#94A3B8;margin-top:3px;">Beheer uw bedrijfsprofiel, tarieven, offerte-teksten en app-voorkeuren.</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Witte cards CSS (keyed → geen accumulatie bij hot-reload) ──
    _inject_keyed_css("inst_cards", """
    /* Overbodige buitenste form-border verwijderen (voorkomt dubbel-card effect) */
    div[data-testid="stForm"]:has(span.inst-card-marker) {
        border: none !important;
        box-shadow: none !important;
        background: transparent !important;
    }
    /* Inhoud-cards wit — Streamlit 1.55: stLayoutWrapper > stVerticalBlock
       (zelfde patroon als Klanten/Personeel; stVerticalBlockBorderWrapper bestaat
        niet in deze versie) */
    div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.inst-card-marker) {
        background: #FFFFFF !important;
        border: 1px solid #E8EFF5 !important;
        border-radius: 14px !important;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05) !important;
        margin-bottom: 16px !important;
    }
    /* Alle elementen binnen de card wit */
    div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.inst-card-marker) div[data-testid="stVerticalBlock"],
    div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.inst-card-marker) div[data-testid="stHorizontalBlock"],
    div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.inst-card-marker) div[data-testid="stColumn"],
    div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.inst-card-marker) div[data-testid="stMarkdownContainer"] {
        background: #FFFFFF !important;
    }
    /* Fallback voor stVerticalBlockBorderWrapper (toekomstige Streamlit versies) */
    div[data-testid="stVerticalBlockBorderWrapper"]:has(span.inst-card-marker) {
        background: #FFFFFF !important;
        border: 1px solid #E8EFF5 !important;
        border-radius: 14px !important;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05) !important;
        margin-bottom: 16px !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:has(span.inst-card-marker) > div[data-testid="stVerticalBlock"],
    div[data-testid="stVerticalBlockBorderWrapper"]:has(span.inst-card-marker) div[data-testid="stHorizontalBlock"],
    div[data-testid="stVerticalBlockBorderWrapper"]:has(span.inst-card-marker) div[data-testid="stColumn"],
    div[data-testid="stVerticalBlockBorderWrapper"]:has(span.inst-card-marker) div[data-testid="stMarkdownContainer"] {
        background: #FFFFFF !important;
    }
    /* FIX witte blokken IN knoppen: het label van een knop mag NIET wit gemaakt
       worden door de regels hierboven (anders ontstaat een wit blok in donkere
       knoppen en is de witte tekst onleesbaar). Het knoplabel transparant houden. */
    div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.inst-card-marker) button div[data-testid="stMarkdownContainer"],
    div[data-testid="stVerticalBlockBorderWrapper"]:has(span.inst-card-marker) button div[data-testid="stMarkdownContainer"] {
        background: transparent !important;
    }
    """)

    # ── Helpers ──
    def inst_card_marker():
        st.markdown('<span class="inst-card-marker" style="display:none;"></span>', unsafe_allow_html=True)

    def _sec(icon_bg, icon, title, subtitle=""):
        _sub_html = (f'<div style="font-size:11.5px;color:#94A3B8;margin-top:1px;">{subtitle}</div>'
                     if subtitle else "")
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:18px;'
            f'padding-bottom:12px;border-bottom:1px solid #F1F5F9;">'
            f'<div style="width:34px;height:34px;border-radius:10px;background:{icon_bg};'
            f'display:flex;align-items:center;justify-content:center;flex-shrink:0;">'
            f'<i class="bi bi-{icon}" style="font-size:16px;color:#374151;"></i></div>'
            f'<div><div style="font-size:14px;font-weight:700;color:#0F172A;">{title}</div>{_sub_html}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    def _save(k):
        """Opslaan-balk onderaan een form. Geeft True terug als op Opslaan is geklikt."""
        # Knoppen hoger plaatsen — de ruimte erboven flink verkleind voor een strakkere
        # uitlijning met de rest van het formulier.
        st.markdown("<div style='height:0;margin-top:-20px;'></div>", unsafe_allow_html=True)
        _c1, _, _c2 = st.columns([2, 6, 2])
        with _c1:
            _rst = st.form_submit_button("↺  Reset", use_container_width=True, key=f"{k}_rst")
        with _c2:
            _sav = st.form_submit_button("Opslaan", use_container_width=True,
                                         type="primary", key=f"{k}_sav")
        if _rst:
            if DATA_PATH.exists():
                try:
                    _disk = json.loads(DATA_PATH.read_text(encoding="utf-8"))
                    if "instellingen" in _disk:
                        st.session_state.instellingen = _disk["instellingen"]
                except Exception:
                    pass
            st.rerun()
        return _sav

    # ── Tabs ──
    (tab_bedrijf, tab_fin, tab_offerte,
     tab_factuur, tab_toeslagen, tab_voorkeuren, tab_backup) = st.tabs([
        "Bedrijfsgegevens", "Financieel", "Offertes",
        "Facturen", "Toeslagen", "Voorkeuren", "Back-up & Data",
    ])

    # ══════════════════════════════════════════════════════
    # TAB 1 — BEDRIJFSGEGEVENS
    # ══════════════════════════════════════════════════════
    with tab_bedrijf:
        with st.form("inst_bedrijf"):

            # Sectie: Bedrijfsinformatie
            with st.container(border=True):
                inst_card_marker()
                _sec("#EFF6FF", "building", "Bedrijfsinformatie", "Naam, logo, accentkleur en identiteit")
                bi1, bi2 = st.columns(2)
                with bi1:
                    inst["bedrijfsnaam"]   = st.text_input("Bedrijfsnaam *", value=inst["bedrijfsnaam"], placeholder="Bijv. SchilderPro BV")
                    inst["contactpersoon"] = st.text_input("Contactpersoon", value=inst.get("contactpersoon",""), placeholder="Bijv. Jan de Vries")
                    inst["kvk"]            = st.text_input("KVK-nummer", value=inst.get("kvk",""), placeholder="12345678")
                    inst["btw_nummer"]     = st.text_input("BTW-nummer", value=inst["btw_nummer"], placeholder="NL999888777B01")
                with bi2:
                    inst["bedrijfskleur"] = st.color_picker("Bedrijfskleur / accentkleur", value=inst.get("bedrijfskleur","#2563EB"))
                    _logo_up = st.file_uploader("Bedrijfslogo (PNG/JPG)", type=["png","jpg","jpeg"], key="logo_upload")
                    if _logo_up is not None:
                        import base64 as _b64
                        inst["logo_b64"] = _b64.b64encode(_logo_up.read()).decode()
                    if inst.get("logo_b64"):
                        st.markdown('<div style="font-size:11px;color:#059669;margin-top:4px;">✓ Logo opgeslagen in sessie</div>', unsafe_allow_html=True)

            # Sectie: Adresgegevens
            with st.container(border=True):
                inst_card_marker()
                _sec("#F0FDF4", "geo-alt", "Adresgegevens", "Vestigingsadres van het bedrijf")
                inst["adres"] = st.text_input("Adres", value=inst["adres"], placeholder="Straat + huisnummer")
                _adr1, _adr2 = st.columns(2)
                with _adr1:
                    inst["postcode"] = st.text_input("Postcode", value=inst.get("postcode",""), placeholder="5000 AA")
                with _adr2:
                    inst["plaats"] = st.text_input("Plaats", value=inst.get("plaats",""), placeholder="Tilburg")

            # Sectie: Contact
            with st.container(border=True):
                inst_card_marker()
                _sec("#FFFBEB", "telephone", "Contact", "Telefoon, e-mail en website")
                _ct1, _ct2 = st.columns(2)
                with _ct1:
                    inst["telefoon"] = st.text_input("Telefoon", value=inst["telefoon"], placeholder="013-1234567")
                    inst["mobiel"]   = st.text_input("Mobiel", value=inst.get("mobiel",""), placeholder="06-12345678")
                with _ct2:
                    inst["email"]   = st.text_input("Algemeen e-mailadres", value=inst["email"], placeholder="info@bedrijf.nl")
                    inst["website"] = st.text_input("Website", value=inst.get("website",""), placeholder="www.bedrijf.nl")

            # Sectie: E-mailadressen
            with st.container(border=True):
                inst_card_marker()
                _sec("#F5F3FF", "envelope-at", "E-mailadressen", "Specifieke adressen per documenttype")
                _em1, _em2 = st.columns(2)
                with _em1:
                    inst["email_offertes"] = st.text_input("E-mail offertes", value=inst.get("email_offertes",""), placeholder="offertes@bedrijf.nl",
                                                           help="Wordt als contactadres in de kop van de PDF-offerte gebruikt.")
                with _em2:
                    inst["email_facturen"] = st.text_input("E-mail facturen", value=inst.get("email_facturen",""), placeholder="facturen@bedrijf.nl",
                                                           help="Wordt gebruikt door de factuurmodule (toekomstige versie).")

            # Sectie: Bankgegevens
            with st.container(border=True):
                inst_card_marker()
                _sec("#FEF3C7", "bank2", "Bankgegevens", "IBAN voor betalingen op offertes en facturen")
                inst["iban"] = st.text_input("IBAN", value=inst["iban"], placeholder="NL12 ABCD 0123 4567 89")

            # Sectie: Online aanwezigheid
            with st.container(border=True):
                inst_card_marker()
                _sec("#EDE9FE", "share", "Online aanwezigheid", "Social media profielen")
                _sm1, _sm2, _sm3 = st.columns(3)
                with _sm1:
                    inst["facebook"]  = st.text_input("Facebook", value=inst.get("facebook",""), placeholder="facebook.com/bedrijf")
                with _sm2:
                    inst["instagram"] = st.text_input("Instagram", value=inst.get("instagram",""), placeholder="@bedrijfsnaam")
                with _sm3:
                    inst["linkedin"]  = st.text_input("LinkedIn", value=inst.get("linkedin",""), placeholder="linkedin.com/company/…")

            if _save("b"):
                save_data()
                st.toast("Bedrijfsgegevens opgeslagen!")

    # ══════════════════════════════════════════════════════
    # TAB 2 — FINANCIEEL
    # ══════════════════════════════════════════════════════
    with tab_fin:
        with st.form("inst_financieel"):

            with st.container(border=True):
                inst_card_marker()
                _sec("#FFFBEB", "cash-stack", "Standaard instellingen", "Marge, BTW, uurloon en vergoedingen")
                _f1, _f2 = st.columns(2)
                with _f1:
                    inst["standaard_marge"]   = st.slider("Standaard winstmarge %", 0, 60, int(inst["standaard_marge"]))
                    inst["standaard_btw"]     = st.selectbox("Standaard BTW %", [0, 9, 21], index=[0, 9, 21].index(inst["standaard_btw"]) if inst["standaard_btw"] in [0, 9, 21] else 2)
                    inst["standaard_uurloon"] = st.number_input("Standaard uurloon (€)", value=_inst_getal(inst, "standaard_uurloon", 45.0, float), min_value=0.0, step=0.5, format="%.2f")
                with _f2:
                    inst["km_vergoeding"]    = st.number_input("Kilometervergoeding (€/km)", value=_inst_getal(inst, "km_vergoeding", 0.23, float), min_value=0.0, step=0.01, format="%.2f",
                                                               help="Wordt gebruikt door de factuur-/reiskostenmodule (toekomstige versie).")
                    inst["min_projectprijs"] = st.number_input("Minimale projectprijs (€)", value=_inst_getal(inst, "min_projectprijs", 0.0, float), min_value=0.0, step=50.0,
                                                               help="Projecten onder dit bedrag tonen een waarschuwing in de projectdetails.")
                    inst["aanbetaling_pct"]  = st.number_input("Aanbetaling %", value=_inst_getal(inst, "aanbetaling_pct", 0, int), min_value=0, max_value=100,
                                                               help="Wordt als betaalvoorwaarde op de PDF-offerte vermeld.")

            with st.container(border=True):
                inst_card_marker()
                _sec("#EFF6FF", "calendar-check", "Betalingen & termijnen", "Standaard termijnen en herinneringsschema")
                _p1, _p2 = st.columns(2)
                with _p1:
                    inst["betalingstermijn"] = st.number_input("Betalingstermijn (dagen)", value=_inst_getal(inst, "betalingstermijn", 14, int), min_value=0,
                                                               help="Wordt vermeld in de betaalvoorwaarden op de PDF-offerte.")
                    inst["factuurtermijn"]   = st.number_input("Factuurtermijn (dagen)", value=_inst_getal(inst, "factuurtermijn", 30, int), min_value=1,
                                                               help="Wordt gebruikt door de factuurmodule (toekomstige versie).")
                with _p2:
                    inst["herinnering1_dagen"] = st.number_input("1e herinnering na (dagen)", value=_inst_getal(inst, "herinnering1_dagen", 14, int), min_value=1,
                                                                 help="Wordt gebruikt door de factuurmodule (toekomstige versie).")
                    inst["herinnering2_dagen"] = st.number_input("2e herinnering na (dagen)", value=_inst_getal(inst, "herinnering2_dagen", 7, int), min_value=1,
                                                                 help="Wordt gebruikt door de factuurmodule (toekomstige versie).")
                    inst["herinnering3_dagen"] = st.number_input("Laatste herinnering na (dagen)", value=_inst_getal(inst, "herinnering3_dagen", 3, int), min_value=1,
                                                                 help="Wordt gebruikt door de factuurmodule (toekomstige versie).")

            if _save("f"):
                save_data()
                st.toast("Financiële instellingen opgeslagen!")

    # ══════════════════════════════════════════════════════
    # TAB 3 — OFFERTES
    # ══════════════════════════════════════════════════════
    with tab_offerte:
        with st.form("inst_offerte"):

            with st.container(border=True):
                inst_card_marker()
                _sec("#EFF6FF", "hash", "Nummering", "Prefix en startnummer voor offertes")
                _n1, _n2 = st.columns(2)
                with _n1:
                    inst["offerte_prefix"] = st.text_input("Offerte prefix", value=inst.get("offerte_prefix","OFF"), placeholder="OFF")
                with _n2:
                    inst["offerte_startnummer"] = st.number_input("Startnummer", value=_inst_getal(inst, "offerte_startnummer", 1, int), min_value=1)

            with st.container(border=True):
                inst_card_marker()
                _sec("#F0FDF4", "sliders", "Standaarden", "Standaard waarden voor nieuwe offertes")
                inst["offerte_geldigheid"] = st.number_input("Geldigheid (dagen)", value=_inst_getal(inst, "offerte_geldigheid", 30, int), min_value=1)

            with st.container(border=True):
                inst_card_marker()
                _sec("#FFFBEB", "file-text", "Teksten", "Standaard teksten die in offertes verschijnen")
                _ot1, _ot2 = st.columns(2)
                with _ot1:
                    inst["offerte_tekst"] = st.text_area("Intro tekst", value=inst["offerte_tekst"], height=100, placeholder="Bedankt voor uw interesse…")
                    inst["bedanktekst"]   = st.text_area("Bedanktekst", value=inst.get("bedanktekst",""), height=90, placeholder="Bedankt voor uw opdracht…")
                    inst["handtekening"]  = st.text_input("Handtekening", value=inst.get("handtekening",""), placeholder="Met vriendelijke groet, …")
                with _ot2:
                    inst["voorwaarden"]  = st.text_area("Algemene voorwaarden", value=inst["voorwaarden"], height=110, placeholder="Betaling binnen 14 dagen…")
                    inst["afsluittekst"] = st.text_area("Afsluittekst", value=inst.get("afsluittekst",""), height=90, placeholder="Wij kijken uit naar een goede samenwerking…")

            with st.container(border=True):
                inst_card_marker()
                _sec("#F5F3FF", "file-earmark-pdf", "PDF-opmaak", "Wat wordt getoond in de PDF-offerte")
                _po1, _po2 = st.columns(2)
                with _po1:
                    inst["pdf_logo_tonen"]          = st.checkbox("Logo tonen",          value=bool(inst.get("pdf_logo_tonen",True)),          key="pdf_logo")
                    inst["pdf_handtekening_tonen"]  = st.checkbox("Akkoord/handtekening tonen", value=bool(inst.get("pdf_handtekening_tonen",True)), key="pdf_hts")
                    inst["pdf_btw_tonen"]           = st.checkbox("BTW tonen",           value=bool(inst.get("pdf_btw_tonen",True)),           key="pdf_btw")
                    inst["pdf_paginanummers_tonen"] = st.checkbox("Paginanummers tonen", value=bool(inst.get("pdf_paginanummers_tonen",True)), key="pdf_pag")
                    inst["pdf_inbegrepen_tonen"]    = st.checkbox("Inbegrepen-secties tonen", value=bool(inst.get("pdf_inbegrepen_tonen",True)), key="pdf_inb")
                with _po2:
                    # Master-toggle: interne prijsopbouw (materiaal/arbeid) op de klant-offerte.
                    # Standaard UIT voor een professionele, klantvriendelijke offerte (punt 3).
                    inst["pdf_intern_tonen"]          = st.checkbox("Interne prijzen tonen (materiaal/arbeid)", value=bool(inst.get("pdf_intern_tonen",False)), key="pdf_int")
                    inst["pdf_materiaalkosten_tonen"] = st.checkbox("• Materiaalkosten",  value=bool(inst.get("pdf_materiaalkosten_tonen",True)), key="pdf_mat", disabled=not inst.get("pdf_intern_tonen",False))
                    inst["pdf_arbeidskosten_tonen"]   = st.checkbox("• Arbeidskosten",    value=bool(inst.get("pdf_arbeidskosten_tonen",True)),   key="pdf_arb", disabled=not inst.get("pdf_intern_tonen",False))
                    inst["pdf_voorwaarden_volledig_tonen"] = st.checkbox("Volledige voorwaarden tonen", value=bool(inst.get("pdf_voorwaarden_volledig_tonen",False)), key="pdf_vw")

            if _save("o"):
                save_data()
                st.toast("Offerte-instellingen opgeslagen!")

    # ══════════════════════════════════════════════════════
    # TAB 4 — FACTUREN
    # ══════════════════════════════════════════════════════
    with tab_factuur:
        st.markdown(
            '<div style="font-size:12px;color:#64748B;padding:8px 12px;background:#F8FAFC;'
            'border-radius:8px;border:1px solid #E2E8F0;margin-bottom:14px;">'
            'Facturen gebruiken automatisch je <b>bedrijfsgegevens</b> (naam, adres, postcode/plaats, '
            'telefoon, e-mail, website, IBAN, KVK, BTW) uit de tab <b>Bedrijfsgegevens</b>. '
            'Hieronder stel je de factuur-specifieke zaken in.</div>',
            unsafe_allow_html=True,
        )
        with st.form("inst_factuur"):

            with st.container(border=True):
                inst_card_marker()
                _sec("#EFF6FF", "hash", "Nummering", "Prefix en startnummer voor facturen")
                _fn1, _fn2 = st.columns(2)
                with _fn1:
                    inst["factuur_prefix"]      = st.text_input("Factuur prefix", value=inst.get("factuur_prefix","FACT"), placeholder="FACT", key="fact_pfx")
                with _fn2:
                    inst["factuur_startnummer"] = st.number_input("Startnummer", value=_inst_getal(inst, "factuur_startnummer", 1, int), min_value=1, key="fact_snr")

            with st.container(border=True):
                inst_card_marker()
                _sec("#F0FDF4", "calendar-check", "Betalingen", "Betaaltermijn en herinneringen")
                _fb1, _fb2 = st.columns(2)
                with _fb1:
                    inst["factuurtermijn"] = st.number_input(
                        "Betaaltermijn (dagen)", value=int(inst.get("factuurtermijn", 30) or 30),
                        min_value=1, key="fact_termijn",
                        help="Aantal dagen tot de vervaldatum die op de factuur wordt getoond.")
                with _fb2:
                    st.markdown('<div style="height:1.7rem;"></div>', unsafe_allow_html=True)
                    inst["factuur_automatische_herinneringen"] = st.checkbox(
                        "Automatische herinneringen inschakelen",
                        value=bool(inst.get("factuur_automatische_herinneringen",False)),
                        key="fact_auto_herin",
                        help="Verstuurt herinneringen automatisch op basis van het schema in de Financieel-tab.",
                    )

            with st.container(border=True):
                inst_card_marker()
                _sec("#FFFBEB", "file-text", "Teksten", "Standaard teksten voor facturen en herinneringen")
                inst["factuur_tekst"] = st.text_area("Factuurtekst (intro)", value=inst.get("factuur_tekst",""), height=80, placeholder="Bedankt voor uw opdracht…", key="fact_txt")
                inst["factuur_voettekst"] = st.text_area("Factuurvoettekst", value=inst.get("factuur_voettekst",""), height=70,
                    placeholder="Bijv. Op al onze werkzaamheden zijn onze algemene voorwaarden van toepassing.", key="fact_voet",
                    help="Verschijnt onderaan elke factuur (onder de betaalgegevens).")
                _fh1, _fh2 = st.columns(2)
                with _fh1:
                    inst["factuur_herinnering1"] = st.text_area("1e herinnering", value=inst.get("factuur_herinnering1",""), height=90, placeholder="Wij attenderen u erop…", key="fh1")
                    inst["factuur_herinnering2"] = st.text_area("2e herinnering", value=inst.get("factuur_herinnering2",""), height=90, placeholder="Ondanks eerdere herinnering…", key="fh2")
                with _fh2:
                    inst["factuur_herinnering3"] = st.text_area("Laatste herinnering / aanmaning", value=inst.get("factuur_herinnering3",""), height=90, placeholder="Dit is onze laatste aanmaning…", key="fh3")

            if _save("fac"):
                save_data()
                st.toast("Factuur-instellingen opgeslagen!")

    # ══════════════════════════════════════════════════════
    # TAB 5 — TOESLAGEN
    # ══════════════════════════════════════════════════════
    with tab_toeslagen:
        with st.form("inst_toeslagen"):

            with st.container(border=True):
                inst_card_marker()
                _sec("#FEF3C7", "plus-circle", "Toeslagpercentages", "Percentages worden opgeteld bij de basisprijs van een project")

                _TOESLAGEN = [
                    ("toeslag_hoogte_pct",  "rulers",           "Hoogte toeslag",      "Van toepassing bij werken op hoogte (>2,5m)",        "#EFF6FF", "#2563EB"),
                    ("toeslag_spoed_pct",   "lightning-charge", "Spoed toeslag",       "Toeslag voor spoedopdrachten buiten kantooruren",    "#FFFBEB", "#D97706"),
                    ("toeslag_buiten_pct",  "cloud-rain",       "Buitenwerk toeslag",  "Extra kosten voor buitenschilderwerk",               "#F0FDF4", "#059669"),
                    ("toeslag_steiger_pct", "ladder",           "Steiger toeslag",     "Kosten voor steiger plaatsing en huur",              "#F5F3FF", "#7C3AED"),
                    ("toeslag_weekend_pct", "calendar-week",    "Weekendtoeslag",      "Toeslag voor werkzaamheden in het weekend",          "#FEF3C7", "#B45309"),
                    ("toeslag_avond_pct",   "moon-stars",       "Avondtoeslag",        "Toeslag voor avond- en nachtwerk",                  "#EEF2FF", "#4338CA"),
                    ("toeslag_winter_pct",  "thermometer-low",  "Wintertoeslag",       "Toeslag voor werkzaamheden bij vorst of sneeuw",    "#F0F9FF", "#0284C7"),
                    ("toeslag_reis_pct",    "car-front",        "Reiskostentoeslag",   "Toeslag voor reisafstand boven normaal",             "#FFF7ED", "#EA580C"),
                ]
                for _tkey, _tico, _tlbl, _tdsc, _tbg, _tclr in _TOESLAGEN:
                    _tc1, _tc2 = st.columns([4, 1])
                    with _tc1:
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:12px;padding:8px 0;">'
                            f'<div style="width:36px;height:36px;border-radius:9px;background:{_tbg};'
                            f'display:flex;align-items:center;justify-content:center;flex-shrink:0;">'
                            f'<i class="bi bi-{_tico}" style="font-size:16px;color:{_tclr};"></i></div>'
                            f'<div><div style="font-size:13px;font-weight:600;color:#0F172A;">{_tlbl}</div>'
                            f'<div style="font-size:11px;color:#94A3B8;">{_tdsc}</div></div></div>',
                            unsafe_allow_html=True,
                        )
                    with _tc2:
                        inst[_tkey] = st.number_input("%", value=_inst_getal(inst, _tkey, 0, int),
                                                       min_value=0, max_value=200, key=f"toesl_{_tkey}")

            if _save("t"):
                save_data()
                st.toast("Toeslagen opgeslagen!")

    # ══════════════════════════════════════════════════════
    # TAB 6 — VOORKEUREN
    # ══════════════════════════════════════════════════════
    with tab_voorkeuren:
        with st.form("inst_voorkeuren"):

            with st.container(border=True):
                inst_card_marker()
                _sec("#EFF6FF", "globe", "Taal & Regio", "Taal en regionale weergave-instellingen")
                _vk1, _vk2 = st.columns(2)
                with _vk1:
                    inst["taal"] = st.selectbox("Taal", ["Nederlands", "English"],
                        index=0 if inst.get("taal","Nederlands")=="Nederlands" else 1)
                    st.caption("De Engelse interface wordt in een toekomstige versie geactiveerd; Frans en Duits volgen daarna.")
                    inst["datumweergave"] = st.radio("Datumweergave",
                        ["DD-MM-JJJJ (Europees)", "MM/DD/YYYY (Amerikaans)"],
                        index=0 if inst.get("datumweergave","DD-MM-JJJJ").startswith("DD") else 1)
                with _vk2:
                    inst["valuta"] = st.selectbox("Valuta", ["Euro (€)"], index=0)
                    ui_alert("Meer valuta worden in een toekomstige versie toegevoegd.", "info")

            with st.container(border=True):
                inst_card_marker()
                _sec("#F0FDF4", "speedometer2", "Dashboard", "Standaard weergave bij het openen van het dashboard")
                _dv1, _dv2 = st.columns(2)
                _dp_opties  = ["Huidige maand", "Afgelopen 3 maanden", "Huidig jaar", "Alles"]
                _df_opties  = ["Alle projecten", "In uitvoering", "Offertes uitstaand", "Afgerond"]
                with _dv1:
                    inst["dashboard_periode"] = st.selectbox("Standaard periode", _dp_opties,
                        index=_dp_opties.index(inst.get("dashboard_periode","Huidige maand")))
                with _dv2:
                    inst["dashboard_filter"] = st.selectbox("Standaard filter", _df_opties,
                        index=_df_opties.index(inst.get("dashboard_filter","Alle projecten")))

            with st.container(border=True):
                inst_card_marker()
                _sec("#FFFBEB", "folder2", "Projecten", "Standaard weergave in de projectenlijst")
                _pv1, _pv2 = st.columns(2)
                _ps_opties  = ["Concept","Offerte verzonden","Geaccepteerd","In uitvoering","Afgerond","Geannuleerd"]
                _so_opties  = ["Nieuwste eerst","Oudste eerst","Naam A-Z","Naam Z-A","Hoogste bedrag"]
                with _pv1:
                    inst["std_project_status"] = st.selectbox("Standaard status nieuw project", _ps_opties,
                        index=_ps_opties.index(inst.get("std_project_status","Concept")))
                with _pv2:
                    inst["std_sorteervolgorde"] = st.selectbox("Standaard sortering", _so_opties,
                        index=_so_opties.index(inst.get("std_sorteervolgorde","Nieuwste eerst")))

            with st.container(border=True):
                inst_card_marker()
                _sec("#F5F3FF", "laptop", "Applicatie", "Gedrag en weergave van de applicatie")
                _av1, _av2 = st.columns(2)
                _sp_opties = ["Dashboard","Projecten","Offertes","Calculaties","Klanten"]
                with _av1:
                    inst["startpagina"] = st.selectbox("Startpagina bij openen", _sp_opties,
                        index=_sp_opties.index(inst.get("startpagina","Dashboard")))
                    inst["decimalen"]   = st.number_input("Decimalen in bedragen", value=_inst_getal(inst, "decimalen", 2, int), min_value=0, max_value=4)
                with _av2:
                    inst["thema"]           = st.selectbox("Thema", ["Licht", "Donker (binnenkort)"], index=0)
                    inst["compacte_weergave"] = st.checkbox("Compacte weergave",
                        value=bool(inst.get("compacte_weergave",False)),
                        help="Verkleint de padding van kaarten en lijsten.")

            if _save("v"):
                save_data()
                st.toast("Voorkeuren opgeslagen!")

    # ══════════════════════════════════════════════════════
    # TAB 7 — BACK-UP & DATA
    # ══════════════════════════════════════════════════════
    with tab_backup:
        _inject_keyed_css("backup_dl_btns", """
        /* Witte blokken rondom download-knoppen verwijderen.
           Hogere specificiteit dan de globale .stDownloadButton > button !important regel:
           4 attr/class selectors vs 1 → wint altijd. */
        div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.inst-card-marker)
            .stDownloadButton > button {
            background: transparent !important;
            border: 1.5px solid #CBD5E1 !important;
            box-shadow: none !important;
            color: #374151 !important;
        }
        div[data-testid="stLayoutWrapper"] > div[data-testid="stVerticalBlock"]:has(span.inst-card-marker)
            .stDownloadButton > button:hover {
            background: #F1F5F9 !important;
            border-color: #94A3B8 !important;
            box-shadow: none !important;
        }
        """)
        import json
        from datetime import datetime as dt

        _ts      = dt.now().strftime('%Y%m%d')
        _ts_full = dt.now().strftime('%Y%m%d_%H%M')

        # ── Export ──
        with st.container(border=True):
            inst_card_marker()
            _sec("#EFF6FF", "upload", "Data exporteren", "Download specifieke gegevens of een volledige back-up")

            _ex1, _ex2, _ex3, _ex4 = st.columns(4)
            with _ex1:
                st.download_button("Klanten", data=json.dumps(st.session_state.klanten, ensure_ascii=False, indent=2),
                    file_name=f"klanten_{_ts}.json", mime="application/json", use_container_width=True)
                st.caption(f"{len(st.session_state.klanten)} klanten")
            with _ex2:
                st.download_button("Projecten", data=json.dumps(st.session_state.projecten, ensure_ascii=False, indent=2),
                    file_name=f"projecten_{_ts}.json", mime="application/json", use_container_width=True)
                st.caption(f"{len(st.session_state.projecten)} projecten")
            with _ex3:
                st.download_button("Personeel", data=json.dumps(st.session_state.personeel, ensure_ascii=False, indent=2),
                    file_name=f"personeel_{_ts}.json", mime="application/json", use_container_width=True)
                st.caption(f"{len(st.session_state.personeel)} medewerkers")
            with _ex4:
                st.download_button("Producten", data=json.dumps(st.session_state.producten, ensure_ascii=False, indent=2),
                    file_name=f"producten_{_ts}.json", mime="application/json", use_container_width=True)
                st.caption(f"{len(st.session_state.producten)} producten")

            st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
            _ex5, _ex6 = st.columns(2)
            with _ex5:
                st.download_button("Alleen instellingen", data=json.dumps(st.session_state.instellingen, ensure_ascii=False, indent=2),
                    file_name=f"instellingen_{_ts}.json", mime="application/json", use_container_width=True)
                st.caption("App-instellingen")
            with _ex6:
                _backup_data = json.dumps({
                    "klanten":    st.session_state.klanten,
                    "projecten":  st.session_state.projecten,
                    "personeel":  st.session_state.personeel,
                    "producten":  st.session_state.producten,
                    "instellingen": st.session_state.instellingen,
                    "volgende_project_id": st.session_state.volgende_project_id,
                    "volgende_klant_id":   st.session_state.volgende_klant_id,
                    "backup_datum": dt.now().isoformat(),
                }, ensure_ascii=False, indent=2)
                st.download_button("Volledige back-up (alle data)", data=_backup_data,
                    file_name=f"schilderpro_backup_{_ts_full}.json", mime="application/json",
                    type="primary", use_container_width=True)
                st.caption("Klanten + projecten + personeel + producten + instellingen")

        # ── Import ──
        with st.container(border=True):
            inst_card_marker()
            _sec("#F0FDF4", "download", "Data importeren", "Herstel een eerdere back-up of importeer losse bestanden")

            _uploaded = st.file_uploader("Upload een back-up bestand (.json)", type=["json"], key="backup_upload")

            def _detecteer_lijst_type(_records, _bestandsnaam):
                """Bepaal het datatype van een los exportbestand (kale JSON-lijst).
                Eerst op recordstructuur, daarna op bestandsnaam als fallback."""
                if _records and isinstance(_records[0], dict):
                    _keys = set(_records[0])
                    if "onderdelen" in _keys or "klant_id" in _keys:
                        return "projecten"
                    if "uurtarief" in _keys:
                        return "personeel"
                    if "verbruik" in _keys:
                        return "producten"
                    if "naam" in _keys and ("adres" in _keys or "stad" in _keys or "email" in _keys):
                        return "klanten"
                _naam = (_bestandsnaam or "").lower()
                for _t in ("klanten", "projecten", "personeel", "producten", "instellingen"):
                    if _t in _naam:
                        return _t
                return None

            if _uploaded is not None:
                try:
                    _imp_data = json.load(_uploaded)

                    # ── SP-006: los exportbestand (kale lijst) herkennen en verpakken ──
                    if isinstance(_imp_data, list):
                        _lijst_type = _detecteer_lijst_type(_imp_data, _uploaded.name)
                        if _lijst_type is None or _lijst_type == "instellingen":
                            ui_alert("Import geweigerd — dit is een los exportbestand waarvan het type "
                                     "(klanten/projecten/personeel/producten) niet kon worden bepaald. "
                                     "Gebruik een volledige back-up of hernoem het bestand.", "error")
                            _imp_data = None
                        else:
                            _imp_data = {_lijst_type: _imp_data}
                    elif not isinstance(_imp_data, dict):
                        ui_alert("Import geweigerd — onbekend bestandsformaat.", "error")
                        _imp_data = None

                    _fouten = []
                    if _imp_data is not None:
                        for _ik in ["klanten","projecten","personeel","producten"]:
                            if _ik in _imp_data and not isinstance(_imp_data[_ik], list):
                                _fouten.append(f"'{_ik}' moet een lijst zijn")
                        if "instellingen" in _imp_data and not isinstance(_imp_data["instellingen"], dict):
                            _fouten.append("'instellingen' moet een object zijn")
                        # ── SP-006: nooit een no-op import aanbieden ──
                        _import_keys = [k for k in ("klanten","projecten","personeel","producten","instellingen")
                                        if k in _imp_data]
                        if not _fouten and not _import_keys:
                            _fouten.append("geen importeerbare gegevens (klanten/projecten/personeel/producten/instellingen) gevonden")

                    if _imp_data is None:
                        pass
                    elif _fouten:
                        ui_alert("Import geweigerd — ongeldig bestandsformaat:\n" + "\n".join(_fouten), "error")
                    else:
                        # Records overzicht
                        st.markdown(
                            '<div style="font-size:13px;font-weight:600;color:#0F172A;margin-bottom:10px;">'
                            'Gevonden in dit bestand:</div>',
                            unsafe_allow_html=True,
                        )
                        _rec_cols = st.columns(5)
                        _rec_info = [("klanten","Klanten"),("projecten","Projecten"),
                                     ("personeel","Personeel"),("producten","Producten"),("instellingen","Instellingen")]
                        for _ri, (_rk, _rl) in enumerate(_rec_info):
                            with _rec_cols[_ri]:
                                if _rk in _imp_data:
                                    _cnt = len(_imp_data[_rk]) if isinstance(_imp_data[_rk], list) else "✓"
                                    st.markdown(
                                        f'<div style="text-align:center;padding:10px 4px;background:#F0FDF4;'
                                        f'border-radius:10px;border:1px solid #86EFAC;">'
                                        f'<div style="font-size:18px;font-weight:700;color:#059669;">{_cnt}</div>'
                                        f'<div style="font-size:11px;color:#374151;">{_rl}</div></div>',
                                        unsafe_allow_html=True,
                                    )
                                else:
                                    st.markdown(
                                        f'<div style="text-align:center;padding:10px 4px;background:#F8FAFC;'
                                        f'border-radius:10px;border:1px solid #E2E8F0;">'
                                        f'<div style="font-size:18px;font-weight:700;color:#CBD5E1;">—</div>'
                                        f'<div style="font-size:11px;color:#94A3B8;">{_rl}</div></div>',
                                        unsafe_allow_html=True,
                                    )

                        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)

                        # ── BUG-06: relationele integriteit controleren vóór import ──
                        # Bepaal welke klant-ids er ná de import bestaan (geïmporteerde klanten
                        # indien aanwezig, anders de huidige) en welke projecten dan gelden.
                        # Projecten die naar een niet-bestaande klant verwijzen, zijn verweesd.
                        _res_klant_ids = {k.get("id") for k in
                                          (_imp_data["klanten"] if "klanten" in _import_keys
                                           else st.session_state.klanten) if isinstance(k, dict)}
                        _res_projecten = (_imp_data["projecten"] if "projecten" in _import_keys
                                          else st.session_state.projecten)
                        _wees = [p for p in _res_projecten if isinstance(p, dict)
                                 and p.get("klant_id") is not None
                                 and p.get("klant_id") not in _res_klant_ids]
                        if _wees:
                            _namen = ", ".join(str(p.get("naam", p.get("id", "?"))) for p in _wees[:5])
                            _meer = f" + {len(_wees) - 5} andere" if len(_wees) > 5 else ""
                            ui_alert(
                                f"Verweesde relaties gedetecteerd: {len(_wees)} project(en) verwijzen "
                                f"naar een klant die ná deze import niet bestaat ({_namen}{_meer}). "
                                f"Importeer óók de bijbehorende klanten (of een volledige back-up) om "
                                f"dit te voorkomen; anders worden deze projecten als 'Onbekende klant' getoond.",
                                "warning")

                        ui_alert("Let op: Importeren overschrijft alle huidige gegevens. Download eerst een back-up.", "warning")
                        if st.button("Importeer back-up", type="primary", key="do_import"):
                            for _ikey in _import_keys:
                                st.session_state[_ikey] = _imp_data[_ikey]
                            # ── SP-002: ID-tellers herstellen na import ──
                            for _tk in ("volgende_project_id", "volgende_klant_id"):
                                if _tk in _imp_data:
                                    try:
                                        st.session_state[_tk] = max(int(_imp_data[_tk]), 1)
                                    except (TypeError, ValueError):
                                        pass
                            _max_pid = max((p.get("id", 0) for p in st.session_state.projecten
                                            if isinstance(p.get("id"), int)), default=0)
                            if st.session_state.get("volgende_project_id", 1) <= _max_pid:
                                st.session_state.volgende_project_id = _max_pid + 1
                            _max_kid = max((k.get("id", 0) for k in st.session_state.klanten
                                            if isinstance(k.get("id"), int)), default=0)
                            if st.session_state.get("volgende_klant_id", 1) <= _max_kid:
                                st.session_state.volgende_klant_id = _max_kid + 1
                            save_data()
                            st.toast("Geïmporteerd: " + ", ".join(_import_keys))
                            st.rerun()
                except Exception as _ie:
                    ui_alert(f"Ongeldig bestand: {_ie}", "error")

        # ── Gevaarlijke acties ──
        with st.container(border=True):
            inst_card_marker()
            _sec("#FEE2E2", "exclamation-triangle-fill", "Gevaarlijke acties", "Onomkeerbare bewerkingen — maak eerst een back-up")
            st.markdown(
                '<div style="background:#FEF2F2;border:1px solid #FECACA;border-radius:10px;'
                'padding:14px 18px;margin-bottom:16px;">'
                '<div style="font-size:13px;font-weight:600;color:#991B1B;margin-bottom:4px;">Waarschuwing</div>'
                '<div style="font-size:12.5px;color:#7F1D1D;">Onderstaande acties kunnen <strong>niet ongedaan</strong> worden gemaakt. '
                'Download eerst een volledige back-up via de Export-sectie hierboven.</div></div>',
                unsafe_allow_html=True,
            )

            # Losse verwijderacties
            _GEVAAR = [
                ("reset_klanten_confirm", "Verwijder alle klanten",   "klanten",  "Alle klanten verwijderd."),
                ("reset_proj_confirm",    "Verwijder alle projecten", "projecten","Alle projecten verwijderd."),
                ("reset_pers_confirm",    "Verwijder alle personeel", "personeel","Alle personeel verwijderd."),
                ("reset_prod_confirm",    "Verwijder alle producten", "producten","Alle producten verwijderd."),
            ]
            _gv_cols = st.columns(4)
            for _gi, (_gsk, _glbl, _gdk, _gtst) in enumerate(_GEVAAR):
                with _gv_cols[_gi]:
                    if st.button(_glbl, use_container_width=True, key=f"gbtn_{_gsk}"):
                        st.session_state[_gsk] = True
                    if st.session_state.get(_gsk):
                        ui_alert("Weet je het zeker?", "warning")
                        if st.button("Bevestig", key=f"gconf_{_gsk}", type="primary"):
                            st.session_state[_gdk] = []
                            if _gdk == "projecten":
                                prune_personeel_projectkoppelingen()   # SP-005
                            st.session_state[_gsk] = False
                            save_data()
                            st.toast(_gtst)
                            st.rerun()

            st.markdown('<div style="height:1px;background:#FEE2E2;margin:16px 0 12px;"></div>', unsafe_allow_html=True)
            _, _fc = st.columns([3, 1])
            with _fc:
                if st.button("↺ Fabrieksreset (alles)", use_container_width=True, key="btn_factory_reset"):
                    st.session_state.reset_all_confirm = True
            if st.session_state.get("reset_all_confirm"):
                ui_alert("Dit verwijdert ALLE data inclusief klanten, projecten en instellingen.", "warning")
                if st.button("Bevestig volledige reset", key="confirm_reset_all", type="primary"):
                    try:
                        if DATA_PATH.exists():
                            DATA_PATH.unlink()
                    except Exception:
                        pass
                    for _rk in ["klanten","projecten","personeel","producten","instellingen","taken",
                                "geselecteerd_project","volgende_project_id","volgende_klant_id"]:
                        if _rk in st.session_state:
                            del st.session_state[_rk]
                    st.session_state.reset_all_confirm = False
                    st.toast("App gereset naar fabrieksinstellingen.")
                    st.rerun()


# =====================================================
# ADMIN DASHBOARD  (/admin) — uitsluitend voor platform-admins
# =====================================================
elif selected == "Admin":
    import logging as _logging
    _adm_log = _logging.getLogger("coatflow.admin")

    # ── SERVER-SIDE GUARD ───────────────────────────────────────────────────
    # require_auth() heeft de hele app al achter login gezet. Hier komt de
    # platform-admincheck bovenop: server-side via de service_role in db.py — niet
    # te omzeilen vanuit de browser. Niet-admin / niet-ingelogd / JSON-modus →
    # géén admincontent + st.stop(). De pagina is dus NOOIT toegankelijk voor
    # niet-admins, ongeacht URL-manipulatie, refresh of handmatige navigatie.
    if not (_AUTH_OK and st.session_state.get("authenticated") and _auth.is_platform_admin()):
        st.markdown(
            '<div style="max-width:560px;margin:48px auto;text-align:center;padding:44px 28px;'
            'background:white;border:1px solid #E8EFF5;border-radius:14px;box-shadow:0 1px 4px rgba(0,0,0,0.05);">'
            '<i class="bi bi-shield-lock-fill" style="font-size:34px;color:#DC2626;"></i>'
            '<div style="font-size:21px;font-weight:800;color:#0F172A;margin-top:14px;letter-spacing:-0.3px;">Geen toegang</div>'
            '<div style="font-size:13.5px;color:#64748B;margin-top:7px;line-height:1.5;">'
            'Het beheerdersdashboard is uitsluitend toegankelijk voor platformbeheerders.</div>'
            '</div>', unsafe_allow_html=True)
        _dn1, _dn2, _dn3 = st.columns([2, 2, 2])
        with _dn2:
            if st.button("Terug naar dashboard", use_container_width=True, key="admin_denied_back"):
                st.session_state["nav_doel"] = "Dashboard"
                st.rerun()
        st.stop()

    # ── ADMIN-PAGINA CSS (CoatFlow-tokens: radius 14, #E8EFF5, DM Sans/Mono) ──
    _inject_keyed_css("admin_page", """
    .adm-title{font-size:26px;font-weight:800;color:#0F172A;letter-spacing:-0.5px;line-height:1.2;
        display:flex;align-items:center;gap:10px;}
    .adm-sub{font-size:12.5px;color:#94A3B8;font-weight:400;margin-top:3px;}
    .adm-th{font-size:10.5px;font-weight:700;color:#94A3B8;text-transform:uppercase;letter-spacing:0.06em;padding:2px 0 6px;}
    .adm-email{font-size:13px;font-weight:600;color:#0F172A;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .adm-company{font-size:11.5px;color:#94A3B8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px;}
    .adm-date{font-size:12.5px;color:#475569;font-family:'DM Mono',monospace;}
    .adm-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:99px;
        font-size:11px;font-weight:600;white-space:nowrap;}
    .adm-badge .dot{width:5px;height:5px;border-radius:99px;flex-shrink:0;}
    .adm-badge.trial{background:#FEF3C7;color:#92400E;}.adm-badge.trial .dot{background:#F59E0B;}
    .adm-badge.active{background:#DCFCE7;color:#166534;}.adm-badge.active .dot{background:#16A34A;}
    .adm-badge.suspended{background:#FEE2E2;color:#991B1B;}.adm-badge.suspended .dot{background:#EF4444;}
    .adm-badge.neutral{background:#F1F5F9;color:#475569;}.adm-badge.neutral .dot{background:#94A3B8;}
    .adm-tag{display:inline-block;margin-left:6px;padding:1px 7px;border-radius:99px;background:#EEF2FF;
        color:#4F46E5;font-size:10px;font-weight:700;vertical-align:middle;}
    .adm-trialend{font-size:10.5px;color:#94A3B8;margin-top:2px;}
    .adm-cell{padding-top:5px;}
    /* rij-scheiding + compacte actieknoppen (gemarkeerd via adm-row-mk) */
    [data-testid="stHorizontalBlock"]:has(span.adm-row-mk){border-bottom:1px solid #F1F5F9;padding:7px 0 3px;}
    [data-testid="stHorizontalBlock"]:has(span.adm-row-mk) .stButton>button{padding:5px 8px !important;
        font-size:11.5px !important;height:32px !important;white-space:nowrap !important;min-height:0 !important;}
    """)

    # ── DATA (gecachet; live refresh = .clear() + rerun na elke mutatie) ─────
    try:
        _adata = _admin_fetch_data()
        _astats, _ausers = _adata["stats"], _adata["users"]
    except Exception as _e:
        _adm_log.error("Admin-data laden mislukte: %s", _e)
        st.markdown('<div class="adm-title"><i class="bi bi-shield-lock-fill" style="color:#2563EB;"></i>Platformbeheer</div>', unsafe_allow_html=True)
        ui_alert("Kon de beheergegevens niet laden uit de database. Controleer de "
                 "verbinding en probeer het opnieuw.", "warning")
        if st.button("Opnieuw proberen", key="admin_retry"):
            _admin_fetch_data.clear()
            st.rerun()
        st.stop()

    # ── KOP + handmatige verversknop ─────────────────────────────────────────
    _hk, _hr = st.columns([6, 1.4])
    with _hk:
        st.markdown(
            '<div class="adm-title"><i class="bi bi-shield-lock-fill" style="color:#2563EB;"></i>Platformbeheer</div>'
            '<div class="adm-sub">Beheer alle CoatFlow-accounts, proefperiodes en platformstatistieken.</div>',
            unsafe_allow_html=True)
    with _hr:
        if st.button("↻ Verversen", use_container_width=True, key="admin_refresh"):
            _admin_fetch_data.clear()
            st.rerun()

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

    # ── STATISTIEKKAARTEN (.metric-card — globale CoatFlow-stijl) ────────────
    _ac1, _ac2, _ac3 = st.columns(3)
    with _ac1:
        st.markdown(
            '<div class="metric-card blue">'
            '<div class="mc-icon blue"><i class="bi bi-person-badge-fill" style="color:#2563EB;"></i></div>'
            '<div class="mc-label">Totaal aantal schilders</div>'
            f'<div class="mc-value">{_astats["gebruikers"]}</div>'
            '<div class="mc-sub">Geregistreerde gebruikers</div>'
            '</div>', unsafe_allow_html=True)
    with _ac2:
        st.markdown(
            '<div class="metric-card green">'
            '<div class="mc-icon green"><i class="bi bi-calculator-fill" style="color:#059669;"></i></div>'
            '<div class="mc-label">Calculaties / offertes</div>'
            f'<div class="mc-value">{_astats["projecten"]}</div>'
            '<div class="mc-sub">Totaal aantal projecten</div>'
            '</div>', unsafe_allow_html=True)
    with _ac3:
        st.markdown(
            '<div class="metric-card indigo">'
            '<div class="mc-icon indigo"><i class="bi bi-building-fill" style="color:#4F46E5;"></i></div>'
            '<div class="mc-label">Bedrijven</div>'
            f'<div class="mc-value">{_astats["bedrijven"]}</div>'
            '<div class="mc-sub">Geregistreerde tenants</div>'
            '</div>', unsafe_allow_html=True)

    st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)

    # ── GEBRUIKERSTABEL ──────────────────────────────────────────────────────
    st.markdown(
        f'<div style="font-size:16px;font-weight:700;color:#0F172A;letter-spacing:-0.2px;margin-bottom:10px;">'
        f'Gebruikers <span style="color:#94A3B8;font-weight:500;font-size:13px;">· {len(_ausers)}</span></div>',
        unsafe_allow_html=True)

    if not _ausers:
        st.markdown(
            '<div style="text-align:center;padding:40px 24px;background:white;border:1px solid #E8EFF5;'
            'border-radius:14px;color:#94A3B8;font-size:13.5px;">Nog geen geregistreerde gebruikers.</div>',
            unsafe_allow_html=True)
    else:
        # Koprij
        _h1, _h2, _h3, _h4 = st.columns([2.7, 1.4, 1.8, 3.5])
        _h1.markdown('<div class="adm-th">E-mailadres</div>', unsafe_allow_html=True)
        _h2.markdown('<div class="adm-th">Geregistreerd</div>', unsafe_allow_html=True)
        _h3.markdown('<div class="adm-th">Status</div>', unsafe_allow_html=True)
        _h4.markdown('<div class="adm-th">Acties</div>', unsafe_allow_html=True)

        _STATUS_LBL = {"trial": "Proefperiode", "active": "Actief", "suspended": "Gedeactiveerd"}
        _STATUS_CLS = {"trial": "trial", "active": "active", "suspended": "suspended"}
        _uid = st.session_state.get("user_id")

        for _u in _ausers:
            _st = (_u.get("subscription_status") or "—").lower()
            _badge_cls = _STATUS_CLS.get(_st, "neutral")
            _badge_lbl = _STATUS_LBL.get(_st, "Onbekend")
            _cid = _u.get("company_id")

            _c1, _c2, _c3, _b1, _b2, _b3 = st.columns([2.7, 1.4, 1.8, 1.2, 1.1, 1.2])
            with _c1:
                st.markdown('<span class="adm-row-mk" style="display:none;"></span>', unsafe_allow_html=True)
                _admin_tag = '<span class="adm-tag">ADMIN</span>' if _u.get("is_admin") else ""
                st.markdown(
                    f'<div class="adm-cell"><div class="adm-email">{h(_u.get("email") or "—")}{_admin_tag}</div>'
                    f'<div class="adm-company">{h(_u.get("company_naam") or "—")}</div></div>',
                    unsafe_allow_html=True)
            with _c2:
                st.markdown(f'<div class="adm-cell adm-date">{_fmt_reg_datum(_u.get("created_at"))}</div>',
                            unsafe_allow_html=True)
            with _c3:
                _trial_html = ""
                if _st == "trial" and _u.get("trial_ends_at"):
                    _trial_html = f'<div class="adm-trialend">t/m {_fmt_reg_datum(_u.get("trial_ends_at"))}</div>'
                st.markdown(
                    f'<div class="adm-cell"><span class="adm-badge {_badge_cls}"><span class="dot"></span>{_badge_lbl}</span>{_trial_html}</div>',
                    unsafe_allow_html=True)

            def _do_admin_action(_fn, _ok_msg):
                """Voer een admin-mutatie uit met nette foutafhandeling + live refresh.
                De mutatie her-verifieert server-side de adminrol (db._assert_admin)."""
                try:
                    _res = _fn()
                    _admin_fetch_data.clear()        # echte re-fetch bij de volgende render
                    st.toast(_ok_msg(_res) if callable(_ok_msg) else _ok_msg)
                    st.rerun()
                except _db.DbError as _de:
                    _adm_log.error("Admin-actie mislukt (%s): %s", _u.get("email"), _de)
                    st.toast(f"Mislukt: {_de}")
                except Exception as _ex:
                    _adm_log.error("Admin-actie onverwachte fout (%s): %s", _u.get("email"), _ex)
                    st.toast("Er ging iets mis. Probeer het opnieuw.")

            with _b1:
                if st.button("Proef +14d", key=f"adm_trial_{_u['id']}", use_container_width=True,
                             help="Proefperiode met 14 dagen verlengen", disabled=not _cid):
                    _do_admin_action(
                        lambda: _db.admin_extend_trial(_cid, _uid),
                        lambda end: f"Proefperiode verlengd t/m {_fmt_reg_datum(end)}.")
            with _b2:
                if st.button("Activeren", key=f"adm_act_{_u['id']}", use_container_width=True,
                             help="Account activeren", disabled=(not _cid or _st == "active")):
                    _do_admin_action(
                        lambda: _db.admin_set_status(_cid, "active", _uid),
                        "Account geactiveerd.")
            with _b3:
                if st.button("Deactiveren", key=f"adm_deact_{_u['id']}", use_container_width=True,
                             help="Account deactiveren", disabled=(not _cid or _st == "suspended")):
                    _do_admin_action(
                        lambda: _db.admin_set_status(_cid, "suspended", _uid),
                        "Account gedeactiveerd.")

    st.markdown(
        '<div style="font-size:11.5px;color:#94A3B8;margin-top:18px;display:flex;align-items:center;gap:6px;">'
        '<i class="bi bi-shield-check"></i>Server-side beveiligd · cross-tenant via service_role · '
        'wijzigingen worden direct in Supabase opgeslagen.</div>', unsafe_allow_html=True)