# -*- coding: utf-8 -*-
"""
product_import.py — CoatFlow productimport (modulaire extractielaag).

Publieke ingang:
    product_uit_bron(bron_type, waarde, **opts) -> (data: dict, fout: str | None)

'bron_type' is nu uitsluitend "url". De functie is bewust als dispatcher
opgezet zodat later "barcode", "foto" of "ai" kan worden toegevoegd ZONDER de
aanroepende UI of de formulier-invullogica te wijzigen: elke bron levert
hetzelfde genormaliseerde 'data'-dict met sleutels uit PRODUCT_IMPORT_VELDEN.
Velden die niet betrouwbaar zijn vastgesteld ontbreken in 'data' — er worden
nooit waarden verzonnen of geschat.

Geen externe dependencies vereist: gebruikt 'requests' indien aanwezig, anders
urllib (stdlib). HTML-extractie gebeurt via regex + json (schema.org JSON-LD,
Open-Graph/meta-tags, <title>) en lichte tekst-heuristiek. Werkt daardoor op
Windows, Linux, Docker en cloud-hosting zonder extra packages.
"""

import re
import json
import html as _html

# Welke velden een importbron mag invullen (subset van het productformulier).
PRODUCT_IMPORT_VELDEN = ("naam", "prijs", "inhoud", "inhoud_eenheid",
                         "eenheid", "categorie", "werkzaamheden", "verbruik")

# Canonieke opties — moeten gelijk blijven aan het Producten-formulier in
# SchilderTool1.py. De UI filtert defensief nogmaals, dus drift leidt nooit
# tot een crash, hooguit tot een niet-toegepaste suggestie.
CATEGORIE_OPTIES     = ("Verf", "Primer", "Kit", "Afplakken", "Gereedschap", "Schuurpapier", "Behang", "Overig")
EENHEID_OPTIES       = ("liter", "tube", "rol", "vel", "stuk", "m²", "kg")
INHOUD_EH_OPTIES     = ("liter", "ml", "meter", "vel", "stuk", "kg", "rol")
WERKZAAMHEDEN_OPTIES = ("Muren schilderen", "Plafond schilderen", "Houtwerk schilderen",
                        "Gronden", "Afplakken", "Kitwerk", "Schuren", "Behang verwijderen",
                        "Behangen")

# Bekende NL/BE verf- & bouwmarktmerken + gereedschap-/lijm-/schuurmerken. Gebruikt om
# (a) de productnaam merk-bewust te houden en bouwmarkt-suffixen weg te knippen en
# (b) de herkenning op Nederlandse webshops te versterken. Uitbreidbaar zonder de
# parserlogica te wijzigen.
_BEKENDE_MERKEN = (
    "Flexa", "Histor", "Sikkens", "Sigma", "Sigmacoatings", "Wijzonol", "Rambo",
    "Alabastine", "Alpina", "Levis", "Ralston", "Boonstoppel", "Hermadix", "Trimetal",
    "Drenth", "Frenchic", "Farrow & Ball", "Little Greene", "Painting the Past",
    "Pufas", "Knauf", "Bison", "Griffon", "Soudal", "Bostik", "Den Braven", "Zwaluw",
    "Tec7", "3M", "Tesa", "Bosch", "Makita", "Festool", "Mirka", "Storch", "Anza",
    "Linea", "OASE", "Wallified", "Boomerang",
)
# Bouwmarkt-/webshopnamen die als SEO-suffix in titels staan en uit de naam mogen.
_WEBSHOP_SUFFIX = ("gamma", "karwei", "praxis", "hornbach", "hubo", "bauhaus", "intratuin",
                   "verfwebwinkel", "verfwinkel", "verfshop", "verfbestellen", "verf.nl",
                   "bol.com", "bol", "toolstation", "verfgigant", "verfcompleet")

_FOUT_GENERIEK = ("Productinformatie kon niet automatisch worden opgehaald. "
                  "Controleer de URL of vul de gegevens handmatig in.")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 CoatFlow/1.0")


# ============================================================
# 1) Netwerk — HTML ophalen (requests of stdlib-urllib)
# ============================================================
def _haal_html(url, timeout=12, max_bytes=3_000_000):
    """Returnt (html_text, fout|None). Vangt alle netwerk-/SSL-/timeoutfouten."""
    headers = {"User-Agent": _UA, "Accept-Language": "nl,en;q=0.8",
               "Accept": "text/html,application/xhtml+xml"}
    try:
        try:
            import requests
            resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            if resp.status_code >= 400:
                return None, _FOUT_GENERIEK
            resp.encoding = resp.encoding or resp.apparent_encoding or "utf-8"
            return (resp.text or "")[: max_bytes * 2], None
        except ImportError:
            import urllib.request
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(max_bytes)
            return raw.decode("utf-8", "replace"), None
    except Exception:
        return None, _FOUT_GENERIEK


# ============================================================
# 2) Kleine parse-helpers
# ============================================================
def _unescape(s):
    try:
        return _html.unescape(str(s or "")).strip()
    except Exception:
        return str(s or "").strip()


def _parse_getal(s):
    """'2,5' -> 2.5 ; '1.234' -> 1234 (duizendtallen) ; '34.95' -> 34.95 ; '310' -> 310."""
    s = str(s).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.match(r"^\d{1,3}(\.\d{3})+$", s):   # 1.234 / 12.345.678 -> duizendtallen
        s = s.replace(".", "")
    try:
        return float(s)
    except Exception:
        return None


def _parse_prijs(val):
    """Tolerante prijs-parser -> float > 0, of None. Begrijpt '€ 1.234,56', '34,95', '34.95'."""
    if val in (None, ""):
        return None
    if isinstance(val, (int, float)):
        return round(float(val), 2) if val > 0 else None
    m = re.search(r"\d[\d.,  ]*", str(val))
    if not m:
        return None
    g = _parse_getal(m.group(0))
    return round(g, 2) if (g and g > 0) else None


# ============================================================
# 3) Structuur-extractie (JSON-LD, meta, title)
# ============================================================
def _jsonld_nodes(obj):
    """Yield alle dict-nodes uit een (genest) JSON-LD object, incl. @graph/lijsten."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)


def _prijs_uit_offers(offers):
    for off in (offers if isinstance(offers, list) else [offers]):
        if not isinstance(off, dict):
            continue
        for k in ("price", "lowPrice", "highPrice"):
            if off.get(k) not in (None, ""):
                return off.get(k)
        ps = off.get("priceSpecification")
        if isinstance(ps, dict) and ps.get("price") not in (None, ""):
            return ps.get("price")
    return None


def _extract_jsonld(html_txt):
    """schema.org Product-velden uit alle <script type=ld+json> blokken."""
    out = {}
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html_txt, re.S | re.I):
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        for node in _jsonld_nodes(obj):
            t = node.get("@type")
            types = [str(x).lower() for x in (t if isinstance(t, list) else [t]) if x]
            if "product" not in types:
                continue
            if node.get("name") and "naam" not in out:
                out["naam"] = _unescape(node["name"])
            if node.get("description") and "omschrijving" not in out:
                out["omschrijving"] = _unescape(node["description"])
            cat = node.get("category")
            if cat and "categorie_bron" not in out:
                out["categorie_bron"] = _unescape(cat.get("name", "") if isinstance(cat, dict) else cat)
            br = node.get("brand")
            if br and "merk" not in out:
                out["merk"] = _unescape(br.get("name", "") if isinstance(br, dict) else br)
            p = _prijs_uit_offers(node.get("offers"))
            if p not in (None, "") and "prijs" not in out:
                out["prijs"] = p
    return out


def _extract_meta(html_txt):
    """OG/Twitter/itemprop meta-tags als {sleutel_kleingeschreven: content}."""
    out = {}
    for m in re.finditer(r"<meta\b[^>]*>", html_txt, re.I):
        tag = m.group(0)
        key = re.search(r'(?:property|name|itemprop)\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        val = re.search(r'content\s*=\s*["\']([^"\']*)["\']', tag, re.I)
        if key and val:
            out.setdefault(key.group(1).strip().lower(), _unescape(val.group(1)))
    return out


def _extract_title(html_txt):
    m = re.search(r"<title[^>]*>(.*?)</title>", html_txt, re.S | re.I)
    return _unescape(re.sub(r"\s+", " ", m.group(1))) if m else ""


def _prijs_uit_tekst(html_txt):
    """Eerste plausibele €-bedrag met 2 decimalen in de pagina-tekst."""
    for m in re.finditer(r"(?:€|&euro;|EUR)\s*([0-9][0-9.  ]*[.,][0-9]{2})\b", html_txt):
        p = _parse_prijs(m.group(1))
        if p:
            return p
    return None


# ============================================================
# 4) Heuristiek — naam, inhoud/eenheid, categorie, werkzaamheden
# ============================================================
def _schoon_naam(naam):
    if not naam:
        return ""
    naam = re.sub(r"\s+", " ", _unescape(naam)).strip()
    naam = re.split(r"\s[|–—·»]\s", naam)[0].strip()      # SEO-suffix na | – — · »
    # Ruis ná een scheidingsteken (-, |, –): bouwmarkt-/webshopnaam of marketingwoord.
    _suffix = "|".join([re.escape(w) for w in _WEBSHOP_SUFFIX]
                       + ["kopen", "bestellen", "online", "webshop", "aanbieding", "actie", "prijs"])
    naam = re.sub(r"\s*[-|–]\s*(?:" + _suffix + r")\b.*$", "", naam, flags=re.I).strip()
    return naam[:80].strip()


def _strip_tags(html_txt):
    """Zichtbare paginatekst (zonder script/style/tags) — voor dekkings-/verbruikdetectie
    die vaak in de productomschrijving of specificatietabel staat, niet in meta-tags."""
    txt = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html_txt, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return _unescape(re.sub(r"\s+", " ", txt))[:40000]


def _raad_merk(blob):
    """Detecteer een bekend merk in naam/omschrijving (alleen bij hoge zekerheid).
    Verbetert de herkenning op NL webshops; verandert geen bestaande velden."""
    b = " " + (blob or "").lower() + " "
    for merk in _BEKENDE_MERKEN:
        if re.search(r"(?<![a-z0-9])" + re.escape(merk.lower()) + r"(?![a-z0-9])", b):
            return merk
    return None


def _parse_verbruik(tekst):
    """Leid 'verbruik per m²' (liter/m² per laag) af uit dekkings-/rendementtekst.
    Herkent o.a. '8-10 m² per liter', 'tot 12 m²/l', 'rendement 10 m²/liter',
    '1 liter per 8 m²'. Alleen bij een plausibele verfdekking (2–25 m²/l → 0,04–0,5
    l/m²); daarbuiten None (nooit een gok). Bij een bereik telt de LAAGSTE dekking
    (= hoogste, veiligste materiaalinschatting)."""
    if not tekst:
        return None
    t = tekst.lower().replace("\xa0", " ")
    dekkingen = []  # m² per liter
    # 'X (- Y) m² per/​/ liter'
    for m in re.finditer(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:[-–]|tot)?\s*(\d{1,3}(?:[.,]\d{1,2})?)?\s*"
                         r"m[²2]\s*(?:per|/)\s*l(?:iter)?\b", t):
        a = _parse_getal(m.group(1))
        b2 = _parse_getal(m.group(2)) if m.group(2) else None
        vals = [x for x in (a, b2) if x and x > 0]
        if vals:
            dekkingen.append(min(vals))
    # inverse: 'X liter per Y m²'
    for m in re.finditer(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*l(?:iter)?\s*per\s*"
                         r"(\d{1,3}(?:[.,]\d{1,2})?)\s*m[²2]\b", t):
        liter = _parse_getal(m.group(1))
        opp = _parse_getal(m.group(2))
        if liter and opp and liter > 0 and opp > 0:
            dekkingen.append(opp / liter)
    for dek in dekkingen:
        if 2.0 <= dek <= 25.0:
            return round(1.0 / dek, 3)
    return None


# eenheid-token -> (inhoud_eenheid, verkoop-eenheid-hint)
_EH_MAP = {
    "milliliter": ("ml", "tube"), "millilitre": ("ml", "tube"), "ml": ("ml", "tube"),
    "cl": ("cl", "tube"),
    "liter": ("liter", "liter"), "litre": ("liter", "liter"), "ltr": ("liter", "liter"), "l": ("liter", "liter"),
    "kilogram": ("kg", "kg"), "kilo": ("kg", "kg"), "kg": ("kg", "kg"),
    "gram": ("gram", "stuk"), "gr": ("gram", "stuk"),
    "meter": ("meter", "rol"), "metre": ("meter", "rol"), "mtr": ("meter", "rol"),
    "m²": ("meter", "rol"), "m2": ("meter", "rol"), "m": ("meter", "rol"),
    "vellen": ("vel", "vel"), "vel": ("vel", "vel"),
    "rollen": ("rol", "rol"), "rol": ("rol", "rol"),
    "stuks": ("stuk", "stuk"), "stuk": ("stuk", "stuk"), "stk": ("stuk", "stuk"),
}
# Langste tokens eerst, zodat 'liter' vóór 'l' en 'ml' vóór 'm' matcht.
_EH_PATROON = re.compile(
    r"(?<![\w.,])(\d{1,4}(?:[.,]\d{1,3})?)\s*"
    r"(milliliter|millilitre|kilogram|liter|litre|vellen|rollen|stuks|meter|metre|"
    r"kilo|gram|mtr|ltr|stk|ml|cl|kg|gr|m²|m2|vel|rol|stuk|l|m)\b",
    re.I)


def _parse_inhoud(tekst):
    """Zoek 'hoeveelheid + eenheid' (bv. '2,5 liter', '310 ml', '50 m', '25 vel').
    Returnt (waarde:float, inhoud_eenheid, eenheid_hint) of (None, None, None)."""
    if not tekst:
        return None, None, None
    kandidaten = []
    for m in _EH_PATROON.finditer(" " + tekst + " "):
        waarde = _parse_getal(m.group(1))
        if not waarde or waarde <= 0:
            continue
        inh_eh, eh_hint = _EH_MAP.get(m.group(2).lower(), (None, None))
        if inh_eh is None:
            continue
        if inh_eh == "gram":                       # normaliseer binnen bestaande opties
            waarde, inh_eh = round(waarde / 1000.0, 3), "kg"
        elif inh_eh == "cl":
            waarde, inh_eh = round(waarde * 10.0, 1), "ml"
        kandidaten.append((waarde, inh_eh, eh_hint))
    if not kandidaten:
        return None, None, None
    # Voorkeur voor echte maat-eenheden boven teleenheden.
    prio = {"liter": 0, "ml": 1, "kg": 2, "meter": 3, "rol": 4, "vel": 5, "stuk": 6}
    kandidaten.sort(key=lambda k: prio.get(k[1], 9))
    return kandidaten[0]


def _raad_eenheid(inh_eh, blob):
    b = (blob or "").lower()
    if re.search(r"koker|tube", b):
        return "tube"
    if inh_eh == "liter":
        return "liter"
    if inh_eh == "ml":
        return "tube"          # ml in de verfwereld = kit-/lijmkoker
    if inh_eh == "kg":
        return "kg"
    if inh_eh == "meter" or re.search(r"\brol(len)?\b", b):
        return "rol"
    if inh_eh == "vel":
        return "vel"
    return "stuk"


def _raad_categorie(blob):
    b = " " + (blob or "").lower() + " "
    if re.search(r"afplak|maskeer|masking|schildertape|\btape\b", b):
        return "Afplakken"
    if re.search(r"\bkit\b|acrylaatkit|acryl-?w|siliconenkit|sealant|voegkit|montagekit|kitkoker", b):
        return "Kit"
    if re.search(r"primer|grondverf|voorstrijk|hechtprimer|grondlaag|isoleerprimer|sealer", b):
        return "Primer"
    if re.search(r"schuurpapier|schuurvel|schuurrol|schuurlinnen|schuurgaas|"
                 r"schuurschijf|schuurband|schuurdriehoek", b):
        return "Schuurpapier"
    if re.search(r"\bbehang\b|behangpapier|vliesbehang|fotobehang|renovlies", b):
        return "Behang"
    if re.search(r"kwast|roller|verfrol|verfborstel|schuurspons|schuurblok|"
                 r"plamuurmes|afbijt|verfbak|verfrooster|gereedschap|spaan|nivelleermes", b):
        return "Gereedschap"
    if re.search(r"verf\b|muurverf|plafondverf|lak\b|lakverf|grondlak|buitenlak|beits|coating|"
                 r"\bmatt?\b|zijdeglans|hoogglans|emulsie|latex|betonverf|vloerverf|muurcoating", b):
        return "Verf"
    return "Overig"


def _raad_werkzaamheden(blob, categorie):
    b = " " + (blob or "").lower() + " "
    wz = []
    if categorie == "Primer":
        wz = ["Gronden"]
    elif categorie == "Kit":
        wz = ["Kitwerk"]
    elif categorie == "Afplakken":
        wz = ["Afplakken"]
    elif categorie == "Schuurpapier":
        wz = ["Schuren"]
    elif categorie == "Behang":
        wz = ["Behangen"]
    elif categorie == "Gereedschap":
        if re.search(r"schuur", b):
            wz = ["Schuren"]
    elif categorie == "Verf":
        if re.search(r"plafondverf", b) or (re.search(r"plafond", b)
                                            and not re.search(r"muur|wand|gevel|hout|\blak\b", b)):
            wz = ["Plafond schilderen"]
        elif re.search(r"buitenlak|buitenverf|gevelverf|\bbuiten\b|\bgevel\b", b):
            # Buiten-/gevelwerk → Muren schilderen (de losse werkzaamheid
            # "Buitenwerk schilderen" bestaat niet meer in de app).
            wz = ["Muren schilderen"]
        elif re.search(r"muurverf|\bmuur\b|wand|latex|emulsie|betonverf|vloerverf", b):
            wz = ["Muren schilderen"]
        elif re.search(r"hout|houtwerk|\blak\b|lakverf|deur|kozijn|grondlak|traphek", b):
            wz = ["Houtwerk schilderen"]
        else:
            wz = ["Muren schilderen"]
    return [w for w in wz if w in WERKZAAMHEDEN_OPTIES]


# ============================================================
# 5) URL-bron — alles samenbrengen
# ============================================================
def product_uit_url(url, timeout=12):
    """Best-effort productextractie uit een URL. Returnt (data, fout|None).
    'data' bevat uitsluitend gevonden/afgeleide velden (⊂ PRODUCT_IMPORT_VELDEN);
    ontbrekende velden worden weggelaten (nooit verzonnen)."""
    url = (url or "").strip()
    if not url:
        return {}, "Plak eerst een product-URL."
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    html_txt, fout = _haal_html(url, timeout=timeout)
    if not html_txt:
        return {}, fout or _FOUT_GENERIEK

    try:
        jsonld = _extract_jsonld(html_txt)
        meta = _extract_meta(html_txt)
        titel = _extract_title(html_txt)
    except Exception:
        jsonld, meta, titel = {}, {}, ""

    data = {}

    # — Naam —
    naam = _schoon_naam(jsonld.get("naam") or meta.get("og:title")
                        or meta.get("twitter:title") or titel or "")
    if naam:
        data["naam"] = naam

    # Tekst-blob voor categorie/werkzaamheden/inhoud-detectie.
    blob = " ".join(x for x in [naam, jsonld.get("omschrijving"), jsonld.get("categorie_bron"),
                                meta.get("og:description"), meta.get("description")] if x)

    # — Merk: maak de productnaam completer (prefix als het merk ontbreekt). Bron:
    #   schema.org brand of een bekend NL/BE-merk in de tekst. Er is geen apart merk-veld
    #   in het formulier; het merk verbetert uitsluitend de naam en de herkenning.
    merk = (jsonld.get("merk") or "").strip() or (_raad_merk(blob or naam) or "")
    if merk and naam and merk.lower() not in naam.lower():
        naam = (merk + " " + naam)[:80].strip()
        data["naam"] = naam
        blob = merk + " " + blob

    # — Prijs (alleen indien gevonden; anders weglaten = leeg laten) —
    prijs = _parse_prijs(jsonld.get("prijs") or meta.get("product:price:amount")
                         or meta.get("og:price:amount") or meta.get("product:price")
                         or _prijs_uit_tekst(html_txt))
    if prijs is not None:
        data["prijs"] = prijs

    # — Inhoud + inhoud-eenheid (uit naam/omschrijving) —
    inh, inh_eh, _eh_hint = _parse_inhoud((naam + "  " + blob).strip())
    if inh is not None:
        data["inhoud"] = inh
        if inh_eh in INHOUD_EH_OPTIES:
            data["inhoud_eenheid"] = inh_eh

    # — Verkoop-eenheid (altijd een logische gok) —
    eenheid = _raad_eenheid(inh_eh, naam + " " + blob)
    if eenheid in EENHEID_OPTIES:
        data["eenheid"] = eenheid
    if "inhoud_eenheid" not in data:
        data["inhoud_eenheid"] = {"liter": "liter", "tube": "ml", "rol": "meter",
                                  "vel": "vel", "kg": "kg", "stuk": "stuk"}.get(eenheid, "liter")

    # — Categorie + werkzaamheden (altijd een voorstel) —
    cat = _raad_categorie(blob or naam)
    data["categorie"] = cat if cat in CATEGORIE_OPTIES else "Overig"
    data["werkzaamheden"] = _raad_werkzaamheden(blob or naam, data["categorie"])

    # — Verbruik per m² (uit dekkings-/rendementtekst) — alleen zinvol voor verf/primer
    #   en alleen bij een plausibele dekking; anders weglaten (nooit een gok). —
    if data["categorie"] in ("Verf", "Primer"):
        _verbruik = _parse_verbruik(blob + "  " + _strip_tags(html_txt))
        if _verbruik is not None:
            data["verbruik"] = _verbruik

    if not data.get("naam"):
        return {}, ("Productinformatie kon niet automatisch worden opgehaald. "
                    "Vul de gegevens handmatig in.")
    return data, None


# ============================================================
# 6) Publieke dispatcher — uitbreidpunt voor toekomstige bronnen
# ============================================================
def product_uit_bron(bron_type, waarde, **opts):
    """Centrale ingang voor productimport. Returnt (data, fout|None).

    bron_type:
        "url"     -> product_uit_url(waarde)            (nu actief)
        "barcode" -> (toekomst) EAN/GTIN -> merk-/GS1-API
        "foto"    -> (toekomst) OCR/labelherkenning
        "ai"      -> (toekomst) LLM-extractie uit vrije tekst/afbeelding

    Alle toekomstige bronnen leveren hetzelfde genormaliseerde 'data'-dict
    (sleutels ⊂ PRODUCT_IMPORT_VELDEN), zodat de UI-invullogica ongewijzigd
    blijft: alleen hier een nieuwe tak toevoegen volstaat.
    """
    if bron_type == "url":
        return product_uit_url(waarde, **opts)
    return {}, "Deze importbron is nog niet beschikbaar."
