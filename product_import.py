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
import socket
import ipaddress
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urljoin

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

# Gerichte foutmeldingen: zeg wat er aan de hand is én wat de schilder kan doen.
# De import blokkeert nooit het formulier — handmatig invullen kan altijd.
_FOUT_GENERIEK = ("Productinformatie kon niet automatisch worden opgehaald. "
                  "Controleer de URL of vul de gegevens handmatig in.")
_FOUT_URL = ("Dit lijkt geen geldige productlink. Plak de volledige link uit de "
             "adresbalk (bv. https://www.winkel.nl/product/…).")
_FOUT_VERBINDING = ("Kon geen verbinding maken met de website. Controleer of de link "
                    "klopt en of er internetverbinding is.")
_FOUT_TRAAG = "De website reageert te traag. Probeer het over een moment nog eens."
_FOUT_404 = "Deze pagina bestaat niet (meer). Controleer of de link klopt."
_FOUT_GEBLOKKEERD = ("Deze website blokkeert automatisch ophalen door programma's. "
                     "Vul de gegevens handmatig in — dat werkt altijd.")
_FOUT_INTERN_ADRES = ("Deze link kan niet worden opgehaald. Plak de volledige link van "
                      "de productpagina op de website van de leverancier.")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_MAX_REDIRECTS = 5


# ============================================================
# 0) SSRF-afscherming — vóór ELKE netwerkverbinding (initieel én per redirect-hop)
# ============================================================
# BUG-FIX (kritiek, SSRF): de oude check keek alleen of de hostnaam "op een domein
# leek" (bevat een punt, geen spaties) — een IP-literal als 169.254.169.254 (cloud-
# metadata) of elk intern adres (10.x/192.168.x/localhost) voldeed daar triviaal aan,
# en 'allow_redirects=True' volgde bovendien blind elke doorverwijzing. Iedere
# ingelogde gebruiker kon zo de server dwingen een intern adres op te vragen. Fix:
# elke hostnaam wordt vóór het verbinden daadwerkelijk opgelost en elk resulterend
# IP-adres (v4 én v6) getoetst tegen privé/lokale/gereserveerde ranges — inclusief
# vóór elke afzonderlijke redirect-hop, niet alleen de eerste aanvraag.
def _host_is_safe(host: str) -> bool:
    """True als GEEN van de IP-adressen waar deze hostnaam naar oplost privé, lokaal
    of gereserveerd is. Kan de hostnaam niet worden opgelost, dan faalt dit VEILIG
    (geweigerd) i.p.v. toegestaan."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        ip_str = info[4][0].split("%")[0]   # IPv6 zone-id (bv. %eth0) wegknippen
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _url_is_safe(url: str) -> bool:
    """Schema + hostnaam-vorm + daadwerkelijke IP-veiligheid. Wordt aangeroepen vóór
    de initiële aanvraag ÉN vóór elke redirect-hop."""
    try:
        parts = urlsplit(url)
    except Exception:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = parts.hostname
    if not host or "." not in host or " " in host:
        return False
    return _host_is_safe(host)


class _VeiligeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Volgt een redirect alleen als het doel opnieuw de host-/IP-check doorstaat —
    zelfde SSRF-afscherming als het requests-pad, voor het urllib-noodpad."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _url_is_safe(newurl):
            return None   # blokkeert de redirect
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ============================================================
# 1) Netwerk — HTML ophalen (requests; stdlib-urllib als noodpad)
# ============================================================
def _haal_html(url, timeout=12, max_bytes=2_000_000):
    """Returnt (html_text, fout|None). Onderscheidt fouten die de schilder kan
    oplossen (verkeerde link, geen verbinding) van blokkades/storingen, en probeert
    het bij een timeout of verbindingsfout één keer opnieuw."""
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.7",
    }
    if not _url_is_safe(url):
        return None, _FOUT_INTERN_ADRES
    try:
        import requests
    except ImportError:                    # hoort niet voor te komen (Streamlit-dep)
        return _haal_html_urllib(url, headers, timeout, max_bytes)

    fout = _FOUT_GENERIEK
    for _poging in (1, 2):                 # 2e poging alleen na timeout/verbindingsfout
        try:
            resp = None
            huidige_url = url
            # Redirects HANDMATIG volgen i.p.v. allow_redirects=True: elke hop moet
            # opnieuw de host-/IP-check doorstaan, anders kan een externe link de
            # server serverside naar een intern adres laten doorspringen.
            for _hop in range(_MAX_REDIRECTS + 1):
                try:
                    # BUG-FIX (klein, geheugenrisico): stream=True + hieronder een
                    # begrensde iter_content-lees i.p.v. resp.text[:max_bytes] — dat
                    # laatste kapte pas af NÁ het volledig binnenhalen van de response,
                    # dus een kwaadaardige/kapotte pagina met een enorme body kon toch
                    # het volledige ding in het geheugen trekken vóór de afkapping.
                    resp = requests.get(huidige_url, headers=headers, timeout=timeout,
                                        allow_redirects=False, stream=True)
                except requests.exceptions.Timeout:
                    fout = _FOUT_TRAAG
                    resp = None
                    break
                except requests.exceptions.ConnectionError:
                    fout = _FOUT_VERBINDING
                    resp = None
                    break
                if resp.status_code not in (301, 302, 303, 307, 308) or not resp.headers.get("Location"):
                    break
                huidige_url = urljoin(huidige_url, resp.headers["Location"])
                if not _url_is_safe(huidige_url):
                    return None, _FOUT_INTERN_ADRES
            if resp is None:
                continue                   # timeout/verbindingsfout -> volgende poging
            status = resp.status_code
            if status in (401, 403, 429, 503):
                return None, _FOUT_GEBLOKKEERD     # WAF/botbeveiliging of tijdelijk plat
            if status in (404, 410):
                return None, _FOUT_404
            if status >= 400:
                return None, _FOUT_GENERIEK
            # Begrensd lezen (stream=True hierboven): stop zodra max_bytes bereikt is,
            # i.p.v. eerst de volledige (mogelijk enorme) body op te halen. resp.content/
            # .text raken hier bewust NIET aan — die zouden de rest van de stream alsnog
            # volledig inlezen.
            raw = b""
            for _chunk in resp.iter_content(chunk_size=65_536):
                raw += _chunk
                if len(raw) >= max_bytes:
                    break
            resp.close()
            raw = raw[:max_bytes]
            # Zonder charset in de Content-Type-header kiest requests latin-1 →
            # mojibake ('Ã«' i.p.v. 'ë'). Header-charset gebruiken als die er is,
            # anders utf-8 als vangnet (geen aparte content-sniffing meer nodig,
            # want we hebben de body al als bytes in handen).
            _enc = requests.utils.get_encoding_from_headers(resp.headers) or "utf-8"
            try:
                tekst = raw.decode(_enc, errors="replace")
            except (LookupError, TypeError):
                tekst = raw.decode("utf-8", errors="replace")
            if not tekst.strip():
                return None, _FOUT_GENERIEK        # lege pagina
            return tekst, None
        except Exception:
            return None, _FOUT_GENERIEK
    return None, fout


def _haal_html_urllib(url, headers, timeout, max_bytes):
    """Noodpad zonder 'requests'. Zelfde foutonderscheid, geen retry. Volgt redirects
    uitsluitend naar opnieuw-gevalideerde adressen (_VeiligeRedirectHandler)."""
    if not _url_is_safe(url):
        return None, _FOUT_INTERN_ADRES
    opener = urllib.request.build_opener(_VeiligeRedirectHandler)
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req, timeout=timeout) as r:
            raw = r.read(max_bytes)
        tekst = raw.decode("utf-8", "replace")
        return (tekst, None) if tekst.strip() else (None, _FOUT_GENERIEK)
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", 0)
        if code in (401, 403, 429, 503):
            return None, _FOUT_GEBLOKKEERD
        if code in (404, 410):
            return None, _FOUT_404
        return None, _FOUT_GENERIEK
    except socket.timeout:
        return None, _FOUT_TRAAG
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), socket.timeout):
            return None, _FOUT_TRAAG
        return None, _FOUT_VERBINDING
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
        # Sommige shops wikkelen de JSON in CDATA of HTML-commentaar — eraf strippen.
        raw = re.sub(r"^\s*(?:/\*|//)?\s*(?:<!\[CDATA\[|<!--)\s*(?:\*/)?", "", raw)
        raw = re.sub(r"(?:/\*|//)?\s*(?:\]\]>|-->)\s*(?:\*/)?\s*$", "", raw).strip()
        if not raw:
            continue
        try:
            # strict=False: accepteer rauwe control-characters in strings (komt voor
            # in omschrijvingen) die de strikte parser anders laten struikelen.
            obj = json.loads(raw, strict=False)
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
            # Specificaties (size/weight/additionalProperty): hier staat de inhoud
            # ('2,5 L') en soms het verbruik, óók als naam/omschrijving ze niet noemen.
            delen = []
            for sleutel in ("size", "weight"):
                v = node.get(sleutel)
                for x in (v if isinstance(v, list) else [v]):
                    if isinstance(x, dict):
                        delen.append(f"{x.get('value', '')} "
                                     f"{x.get('unitText') or x.get('unitCode') or ''}")
                    elif x:
                        delen.append(str(x))
            props = node.get("additionalProperty")
            for prop in (props if isinstance(props, list) else [props] if props else []):
                if isinstance(prop, dict):
                    delen.append(f"{prop.get('name', '')} {prop.get('value', '')} "
                                 f"{prop.get('unitText') or ''}")
            spec_txt = _unescape(" ".join(d.strip() for d in delen if str(d).strip()))
            if spec_txt and "specs" not in out:
                out["specs"] = spec_txt
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


def _extract_microdata(html_txt):
    """schema.org-microdata op WILLEKEURIGE tags: <span itemprop="price" content="12,34">.
    Magento-/Shopware-shops zetten de prijs zo in de HTML i.p.v. in JSON-LD of meta-tags
    (_extract_meta ziet alleen <meta>-tags). Alleen content-attributen — nooit de vrije
    tekst tussen de tags — zodat een doorgestreepte van-prijs niet meegepakt wordt."""
    out = {}
    for m in re.finditer(r"<[a-zA-Z][^>]*\bitemprop\s*=\s*[\"'](price|name|brand)[\"'][^>]*>",
                         html_txt, re.I):
        prop = m.group(1).lower()
        c = re.search(r'\bcontent\s*=\s*["\']([^"\']*)["\']', m.group(0), re.I)
        if c and c.group(1).strip():
            out.setdefault(prop, _unescape(c.group(1)))
    return out


def _extract_h1(html_txt):
    """Eerste <h1> — op productpagina's vrijwel altijd de kale productnaam, zonder de
    SEO-ruis ('… kopen? | Bestel online') die in <title> zit."""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html_txt, re.S | re.I)
    if not m:
        return ""
    return _unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))).strip()


def _prijs_uit_tekst(html_txt):
    """Laatste redmiddel: eerste plausibele €-bedrag in de paginatekst — maar sla bedragen
    over die bij verzendkosten/kortingsdrempels horen ('gratis verzending vanaf
    € 50,00'), anders wordt zo'n drempel per ongeluk de productprijs."""
    for m in re.finditer(r"(?:€|&euro;|EUR)\s*([0-9][0-9.   ]*[.,][0-9]{2})\b", html_txt):
        context = html_txt[max(0, m.start() - 30):m.start()].lower()
        if re.search(r"verzend|bezorg|vanaf|retour|cadeau|tegoed|korting|bespaar|adviesprijs",
                     context):
            continue
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


def _parse_verbruik(tekst, inhoud_eenheid="liter"):
    """Leid 'verbruik per m²' af uit dekkings-/rendementtekst, in de eenheid van de
    verpakkingsinhoud (liter-producten → l/m², kg-producten → kg/m²).

    Herkent o.a. '8-10 m² per liter', 'tot 12 m²/l', '1 liter per 8 m²',
    '1 liter is voldoende voor ca. 8 m²', '125 ml/m²', '0,1 l per m²' en voor
    kg-producten '1,5 kg/m²' / '150 g/m²'. Alleen bij een plausibele waarde
    (verf 2–25 m²/l → 0,04–0,5 l/m²; kg 0,05–3 kg/m²); daarbuiten None (nooit
    een gok). Bij een bereik telt de LAAGSTE dekking (= veiligste inschatting)."""
    if not tekst:
        return None
    t = tekst.lower().replace("\xa0", " ")
    kandidaten = []  # (basis 'liter'|'kg', waarde per m²)
    # 'X (- Y) m² per// liter'
    for m in re.finditer(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:[-–]|tot)?\s*(\d{1,3}(?:[.,]\d{1,2})?)?\s*"
                         r"m[²2]\s*(?:per|/)\s*l(?:iter)?\b", t):
        a = _parse_getal(m.group(1))
        b2 = _parse_getal(m.group(2)) if m.group(2) else None
        vals = [x for x in (a, b2) if x and x > 0]
        if vals:
            kandidaten.append(("liter", 1.0 / min(vals)))
    # 'X liter per/voor (ca.) Y m²' — vangt ook 'is voldoende voor', 'genoeg voor'.
    for m in re.finditer(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*l(?:iter)?\b[^.;:!?]{0,40}?"
                         r"(?:per|voor)\s*(?:ca\.?|circa|ongeveer|±)?\s*"
                         r"(\d{1,3}(?:[.,]\d{1,2})?)\s*m[²2]\b", t):
        liter = _parse_getal(m.group(1))
        opp = _parse_getal(m.group(2))
        if liter and opp and liter > 0 and opp > 0:
            kandidaten.append(("liter", liter / opp))
    # Direct verbruik: 'X ml/l/g/kg per m²' (technische datasheets: '125 ml/m²').
    for m in re.finditer(r"(\d{1,4}(?:[.,]\d{1,3})?)\s*(ml|milliliter|l|liter|g|gr|gram|kg)\s*"
                         r"(?:per|/)\s*m[²2]\b", t):
        w = _parse_getal(m.group(1))
        eh = m.group(2)
        if not w or w <= 0:
            continue
        if eh in ("ml", "milliliter"):
            kandidaten.append(("liter", w / 1000.0))
        elif eh in ("l", "liter"):
            kandidaten.append(("liter", w))
        elif eh in ("g", "gr", "gram"):
            kandidaten.append(("kg", w / 1000.0))
        else:
            kandidaten.append(("kg", w))
    doel = "kg" if inhoud_eenheid == "kg" else "liter"
    for basis, w in kandidaten:
        if basis != doel:
            continue
        if basis == "liter" and 0.04 <= w <= 0.5:
            return round(w, 3)
        if basis == "kg" and 0.05 <= w <= 3.0:
            return round(w, 3)
    return None


def _parse_kit_rendement(tekst):
    """Strekkende meters per kitkoker ('voldoende voor ± 12 meter', '12 m per koker').
    Plausibel 2–40 m; daarbuiten None. Samen met de inhoud (ml) geeft dit het
    verbruik per strekkende meter dat de kit-calculatie nodig heeft."""
    if not tekst:
        return None
    t = tekst.lower().replace("\xa0", " ")
    for pat in (r"(?:voldoende|geschikt|genoeg|goed)\s+voor\s*(?:ca\.?|circa|ongeveer|±)?\s*"
                r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:strekkende\s*)?m(?:eter|¹|1)?\b",
                r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:strekkende\s*)?m(?:eter|¹|1)?\s*per\s*"
                r"(?:koker|tube|patroon|worst)"):
        for m in re.finditer(pat, t):
            w = _parse_getal(m.group(1))
            if w and 2.0 <= w <= 40.0:
                return w
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


# Trefwoord-verankerd: 'Inhoud: 750 ml' / 'Verpakking 2,5 l' in een specificatietabel.
_SPEC_INHOUD = re.compile(
    r"(?:inhoud|verpakking|volume|netto(?:gewicht| gewicht)?)\s*[:\-]?\s*"
    r"(\d{1,4}(?:[.,]\d{1,3})?)\s*"
    r"(milliliter|kilogram|liter|litre|ltr|ml|cl|kg|gram|gr|meter|mtr|vel(?:len)?|"
    r"rol(?:len)?|stuks?|l|m)\b", re.I)


def _inhoud_uit_spec(tekst):
    """Inhoud uit de zichtbare paginatekst, maar ALLEEN direct achter een spec-trefwoord
    ('Inhoud', 'Verpakking', …). Losse maten elders op de pagina (gerelateerde producten,
    verzenddrempels) tellen zo niet mee. Zelfde normalisatie als _parse_inhoud."""
    m = _SPEC_INHOUD.search(tekst or "")
    if not m:
        return None, None, None
    return _parse_inhoud(m.group(1) + " " + m.group(2))


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
                 r"\bmatt?\b|zijdeglans|hoogglans|emulsie|latex|betonverf|vloerverf|muurcoating|"
                 r"\bsatin\b|\bgloss\b|\beggshell\b|aflak", b):   # EN-glansgraden (S2U Nova Satin e.d.)
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
    # Sanity-check op de hostnaam vóór het netwerk in te gaan: 'hallo' of een
    # zin met spaties is geen link → meteen een duidelijke melding, geen DNS-fout.
    _host = re.match(r"^https?://([^/:?#]+)", url, re.I)
    if not _host or "." not in _host.group(1) or " " in _host.group(1):
        return {}, _FOUT_URL

    html_txt, fout = _haal_html(url, timeout=timeout)
    if not html_txt:
        return {}, fout or _FOUT_GENERIEK

    try:
        jsonld = _extract_jsonld(html_txt)
        meta = _extract_meta(html_txt)
        titel = _extract_title(html_txt)
        micro = _extract_microdata(html_txt)
    except Exception:
        jsonld, meta, titel, micro = {}, {}, "", {}

    # Botbeveiliging met status 200 (Cloudflare-challenge e.d.): herkenbaar aan de
    # titel. VÓÓR de naamextractie afvangen, anders wordt 'Just a moment...' de
    # productnaam en krijgt de schilder een onbruikbaar 'succes'.
    if re.search(r"just a moment|attention required|access denied|are you a robot|"
                 r"een ogenblik|verifieer dat u|captcha", titel or "", re.I):
        return {}, _FOUT_GEBLOKKEERD

    data = {}

    # — Naam — (h1 vóór <title>: de titel bevat SEO-ruis die h1 niet heeft)
    naam = _schoon_naam(jsonld.get("naam") or meta.get("og:title")
                        or meta.get("twitter:title") or micro.get("name")
                        or _extract_h1(html_txt) or titel or "")
    if naam:
        data["naam"] = naam

    # Tekst-blob voor categorie/werkzaamheden/inhoud-detectie.
    blob = " ".join(x for x in [naam, jsonld.get("omschrijving"), jsonld.get("categorie_bron"),
                                meta.get("og:description"), meta.get("description")] if x)

    # — Merk: maak de productnaam completer (prefix als het merk ontbreekt). Bron:
    #   schema.org brand of een bekend NL/BE-merk in de tekst. Er is geen apart merk-veld
    #   in het formulier; het merk verbetert uitsluitend de naam en de herkenning.
    merk = ((jsonld.get("merk") or "").strip()
            or (micro.get("brand") or "").strip()
            or (meta.get("product:brand") or "").strip()
            or (_raad_merk(blob or naam) or ""))
    if merk and naam and merk.lower() not in naam.lower():
        naam = (merk + " " + naam)[:80].strip()
        data["naam"] = naam
        blob = merk + " " + blob

    # — Prijs (alleen indien gevonden; anders weglaten = leeg laten) —
    #   Bronvolgorde op betrouwbaarheid: JSON-LD → meta-tags → microdata → paginatekst.
    prijs = _parse_prijs(jsonld.get("prijs") or meta.get("product:price:amount")
                         or meta.get("og:price:amount") or meta.get("product:price")
                         or micro.get("price") or _prijs_uit_tekst(html_txt))
    if prijs is not None:
        data["prijs"] = prijs

    # — Inhoud + inhoud-eenheid —
    #   1) naam/omschrijving/JSON-LD-specs; 2) terugval: trefwoord-verankerde
    #   spec-regel ('Inhoud: 750 ml') in de zichtbare paginatekst — daar staat de
    #   maat op shops die 'm niet in de naam of meta-tags zetten.
    zichtbaar = _strip_tags(html_txt)
    inh, inh_eh, _eh_hint = _parse_inhoud(
        (naam + "  " + blob + "  " + (jsonld.get("specs") or "")).strip())
    if inh is None:
        inh, inh_eh, _eh_hint = _inhoud_uit_spec(zichtbaar)
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
    #   Eerst op de productNAAM (sterkste signaal), pas daarna op de omschrijving:
    #   een muurverf-omschrijving noemt vaak 'aanbrengen met roller of kwast' en werd
    #   daardoor ten onrechte Gereedschap (gezien bij Karwei/Flexa EasyCare).
    cat = _raad_categorie(naam)
    if cat == "Overig":
        cat = _raad_categorie(blob or naam)
    data["categorie"] = cat if cat in CATEGORIE_OPTIES else "Overig"
    data["werkzaamheden"] = _raad_werkzaamheden(blob or naam, data["categorie"])

    # — Verbruik — in de eenheid die de calculatie verwacht: per m² voor verf/primer
    #   (l/m² of kg/m²), per strekkende meter voor Kit (ml/m) en Afplakken (m tape/m).
    #   Altijd alleen bij een plausibele waarde; anders weglaten (nooit een gok).
    if data["categorie"] in ("Verf", "Primer"):
        _verbruik = _parse_verbruik(blob + "  " + zichtbaar,
                                    data.get("inhoud_eenheid", "liter"))
        if _verbruik is not None:
            data["verbruik"] = _verbruik
    elif (data["categorie"] == "Kit" and data.get("inhoud")
          and data.get("inhoud_eenheid") == "ml"):
        # 'voldoende voor ± 12 meter' + kokerinhoud → ml per strekkende meter.
        _meters = _parse_kit_rendement(blob + "  " + zichtbaar)
        if _meters:
            data["verbruik"] = round(data["inhoud"] / _meters, 1)
    elif data["categorie"] == "Afplakken" and data.get("inhoud_eenheid") == "meter":
        # Definitie, geen gok: 1 meter tape per afgeplakte strekkende meter.
        data["verbruik"] = 1.0

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
