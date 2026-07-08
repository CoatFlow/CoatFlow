"""
CoatFlow — datatoegangslaag (Supabase PostgreSQL).   Fase 1 van de SaaS-fundering.

De database is de primaire bron van waarheid; session_state is een per-sessie
cache (1x laden bij sessiestart, write-through bij elke mutatie). Deze module is
volledig ontkoppeld van de UI: SchilderTool1.py raakt alleen load_company_data()
en save_company_data() aan (via zijn eigen load_data()/save_data()).

Zonder geconfigureerde secrets is is_enabled() False en blijft de app op lokale
JSON draaien (geen crash, nette terugval). Alle databasefouten worden vertaald
naar DbError met een leesbare melding — nooit een rauwe stacktrace in de UI.

Tenant-model: één company_id per rij. Fase 1 draait met de service_role key
binnen één default company; Fase 2 (login) zet RLS-isolatie aan via een JWT-claim.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# Logische groepen die de app als "persistent" kent (spiegelt PERSISTENT_KEYS).
APP_KEYS = [
    "klanten", "projecten", "personeel", "producten",
    "taken", "instellingen", "volgende_project_id", "volgende_klant_id",
    "agenda_taken",
]

# Canonieke standaard-instellingen voor een bedrijf — de ÉNE bron van waarheid, gedeeld
# door de app (init_state-merge in SchilderTool1.py) én de registratie (auth.sign_up).
# Hierdoor krijgt een nieuw bedrijf altijd een volledig, geldig profiel en blijven
# directe `inst["..."]`-toegangen overal veilig. Bewust in deze stdlib-only module zodat
# beide kanten dezelfde defaults importeren zonder duplicatie/drift.
STANDAARD_INSTELLINGEN = {
    # Bedrijfsgegevens
    "bedrijfsnaam": "", "contactpersoon": "", "kvk": "", "btw_nummer": "",
    "adres": "", "postcode": "", "plaats": "",
    "telefoon": "", "mobiel": "", "email": "", "website": "", "iban": "",
    "email_offertes": "", "email_facturen": "",
    "logo_b64": "", "bedrijfskleur": "#2563EB", "handtekening": "",
    "facebook": "", "instagram": "", "linkedin": "",
    # Financieel
    "standaard_marge": 25, "standaard_btw": 21,
    "standaard_uurloon": 45.0, "km_vergoeding": 0.23,
    "min_projectprijs": 0.0, "aanbetaling_pct": 0,
    "betalingstermijn": 14, "factuurtermijn": 30,
    "herinnering1_dagen": 14, "herinnering2_dagen": 7, "herinnering3_dagen": 3,
    "factuur_automatische_herinneringen": False,
    "decimalen": 2, "valuta": "Euro (€)",
    # Toeslagen (%) — realistische standaardwaarden; blijven per bedrijf aanpasbaar
    # via Instellingen > Toeslagen.
    "toeslag_hoogte_pct": 10, "toeslag_spoed_pct": 20, "toeslag_buiten_pct": 10,
    "toeslag_steiger_pct": 15, "toeslag_weekend_pct": 50, "toeslag_avond_pct": 25,
    "toeslag_winter_pct": 10, "toeslag_reis_pct": 5,
    # Offerte
    "offerte_prefix": "OFF", "offerte_startnummer": 1, "offerte_geldigheid": 30,
    "offerte_tekst": "", "voorwaarden": "",
    # Factuur
    "factuur_prefix": "FACT", "factuur_startnummer": 1,
    "factuur_tekst": "", "factuur_voettekst": "",
    "factuur_herinnering1": "", "factuur_herinnering2": "", "factuur_herinnering3": "",
    "bedanktekst": "", "afsluittekst": "",
    # PDF-weergave
    "pdf_logo_tonen": True, "pdf_materiaalkosten_tonen": True,
    "pdf_arbeidskosten_tonen": True, "pdf_btw_tonen": True,
    "pdf_inbegrepen_tonen": True, "pdf_handtekening_tonen": True,
    "pdf_voorwaarden_volledig_tonen": False, "pdf_intern_tonen": False,
    "pdf_paginanummers_tonen": True,
    # Voorkeuren
    "datumweergave": "DD-MM-JJJJ", "taal": "Nederlands", "thema": "Licht",
    "startpagina": "Dashboard", "std_project_status": "Concept",
    "std_sorteervolgorde": "Nieuwste eerst",
    "dashboard_filter": "Alle projecten", "dashboard_periode": "Huidige maand",
    "compacte_weergave": False,
}


class DbError(Exception):
    """Nette, gebruikersvriendelijke databasefout (geen rauwe stacktrace in de UI)."""


# ── verbinding (lazy singleton) ──────────────────────────────────────────────
_client = None
_client_lock = threading.Lock()
_init_failed = False
# anon-client (voor het JWT-pad) — óók als singleton cachen, anders wordt er bij
# ELKE load/save een nieuwe client opgezet (trage handshake → merkbare hapering).
_anon = None
_anon_lock = threading.Lock()
# dirty-tracking: laatst weggeschreven content-hash per (company_id, groep)
_last_hashes: dict[tuple, str] = {}


def _secrets():
    """Het [supabase]-blok uit .streamlit/secrets.toml (genormaliseerd), of None.

    Formaat-agnostisch: werkt zowel met de nieuwe Supabase-sleutels
    (sb_publishable_… / sb_secret_…) als met de oude JWT-keys — het zijn gewoon
    strings. Normaliseert spaties en een per ongeluk meegekopieerde '/rest/v1/'
    achter de project-URL, zodat de client altijd het juiste eindpunt gebruikt.
    """
    try:
        import streamlit as st
        raw = st.secrets.get("supabase", None)
        if not raw:
            return None
        out = {}
        for k in ("supabase_url", "supabase_service_key",
                  "supabase_anon_key", "default_company_id"):
            v = raw.get(k, None)
            out[k] = v.strip() if isinstance(v, str) else v
        u = out.get("supabase_url")
        if isinstance(u, str) and u:
            u = u.rstrip("/")
            if u.endswith("/rest/v1"):
                u = u[: -len("/rest/v1")]
            out["supabase_url"] = u
        return out
    except Exception:
        return None


def is_enabled() -> bool:
    """True als Supabase volledig is geconfigureerd (anders draait de app op JSON)."""
    s = _secrets()
    return bool(s and s.get("supabase_url") and s.get("supabase_service_key"))


def _get_client():
    global _client, _init_failed
    if _client is not None:
        return _client
    if _init_failed:
        return None
    with _client_lock:
        if _client is not None:
            return _client
        s = _secrets()
        if not (s and s.get("supabase_url") and s.get("supabase_service_key")):
            _init_failed = True
            return None
        try:
            from supabase import create_client
            _client = create_client(s["supabase_url"], s["supabase_service_key"])
            return _client
        except Exception as e:
            _init_failed = True
            raise DbError(f"Kon de databaseverbinding niet opzetten: {e}")


def default_company_id():
    """Expliciete default-company uit secrets (de eigenaar).

    MULTI-TENANT VEILIG: géén gok meer naar 'de eerste company in de database' —
    bij meerdere bedrijven zou dat data van een ánder bedrijf kunnen teruggeven.
    Bij een ingelogde sessie levert auth de company_id (uit app_users); dit is
    enkel de fallback voor de eigenaar zonder actieve sessie."""
    s = _secrets()
    if s and s.get("default_company_id"):
        return s["default_company_id"]
    return None


def test_connection() -> bool:
    """Lichte connectiviteitstest (voor diagnostiek/migratie)."""
    cl = _get_client()
    if not cl:
        return False
    try:
        cl.table("companies").select("id").limit(1).execute()
        return True
    except Exception as e:
        raise DbError(f"Verbindingstest mislukte: {e}")


# ── helpers ──────────────────────────────────────────────────────────────────
_STRIP = {"company_id", "created_at", "updated_at", "created_by"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _clean(row: dict) -> dict:
    """DB-rij → app-dict (technische kolommen weg)."""
    return {k: v for k, v in row.items() if k not in _STRIP}


def _hash(obj) -> str:
    return hashlib.md5(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


# Veld-whitelists per tabel (voorkomt dat afgeleide velden zoals medewerkers/
# project_ids of onbekende keys naar de verkeerde kolom worden gestuurd).
def _klant_row(k: dict) -> dict:
    return {
        "id": k.get("id"), "naam": k.get("naam") or "Onbekende klant",
        "bedrijf": k.get("bedrijf", ""), "adres": k.get("adres", ""),
        "postcode": k.get("postcode", ""), "stad": k.get("stad", ""),
        "telefoon": k.get("telefoon", ""), "email": k.get("email", ""),
        "btw_nummer": k.get("btw_nummer", ""), "kvk": k.get("kvk", ""),
        "notities": k.get("notities", ""), "actief": bool(k.get("actief", True)),
        "aangemaakt": k.get("aangemaakt", ""), "updated_at": _now(),
    }


def _product_row(p: dict) -> dict:
    return {
        "id": p.get("id"), "naam": p.get("naam") or "Product",
        "prijs": p.get("prijs", 0) or 0, "verbruik": p.get("verbruik", 0) or 0,
        "eenheid": p.get("eenheid", "stuk"), "categorie": p.get("categorie", "Overig"),
        "werkzaamheden": p.get("werkzaamheden") or [],
        "inhoud": p.get("inhoud", 0) or 0, "inhoud_eenheid": p.get("inhoud_eenheid", ""),
        "verbruik_eenheid": p.get("verbruik_eenheid", "m²"),
        "actief": bool(p.get("actief", True)), "updated_at": _now(),
    }


def _personeel_row(m: dict) -> dict:
    return {
        "id": m.get("id"), "naam": m.get("naam") or "Medewerker",
        "uurtarief": m.get("uurtarief", 0) or 0, "functie": m.get("functie", ""),
        "telefoon": m.get("telefoon", ""), "email": m.get("email", ""),
        "notities": m.get("notities", ""), "actief": bool(m.get("actief", True)),
        "status": m.get("status", "Actief"), "updated_at": _now(),
    }


def _taak_row(t: dict) -> dict:
    return {
        "id": t.get("id"), "taak": t.get("taak", ""),
        "klaar": bool(t.get("klaar", False)), "datum": t.get("datum", ""),
    }


def _project_row(p: dict) -> dict:
    return {
        "id": p.get("id"), "naam": p.get("naam") or "Naamloos project",
        "klant_id": p.get("klant_id"), "adres": p.get("adres", ""),
        "status": p.get("status", "Concept"), "aangemaakt": p.get("aangemaakt", ""),
        "notities": p.get("notities", ""), "btw": p.get("btw"), "marge": p.get("marge"),
        "onderdelen": p.get("onderdelen") or [],
        "offerte_nummer": p.get("offerte_nummer"),
        "factuur_nummer": p.get("factuur_nummer"), "factuur_datum": p.get("factuur_datum"),
        "prijs_snapshot": p.get("prijs_snapshot"),   # None of jsonb — exact bewaren
        "updated_at": _now(),
    }


# ── JWT-pad (Fase 3): data-queries via het user-JWT → RLS dwingt de tenant af ─
# Veilige terugval: lukt het JWT-pad niet (geen token, verlopen, misconfig), dan
# gebruikt db de service_role client. De queries filteren ALTIJD op company_id,
# dus geen van beide paden kan ooit data van een ander bedrijf teruggeven.
def _anon_client():
    global _anon
    if _anon is not None:
        return _anon
    s = _secrets()
    key = s.get("supabase_anon_key") if s else None
    if not (s and s.get("supabase_url") and key):
        return None
    with _anon_lock:
        if _anon is not None:
            return _anon
        try:
            from supabase import create_client
            _anon = create_client(s["supabase_url"], key)
        except Exception:
            _anon = None
        return _anon


def _jwt_data_client():
    """Anon-client mét het access-token van de ingelogde gebruiker. PostgREST ziet
    dan het user-JWT (met company_id-claim) en RLS dwingt de tenant af. None als er
    geen token of anon-key is."""
    try:
        import streamlit as st
        tok = st.session_state.get("_access_token")
    except Exception:
        tok = None
    if not tok:
        return None
    cl = _anon_client()
    if cl is None:
        return None
    try:
        cl.postgrest.auth(tok)   # Authorization: Bearer <user JWT>
        return cl
    except Exception:
        return None


def _refresh_access_token() -> bool:
    """Vernieuw het access-token met het refresh-token (bij een verlopen JWT)."""
    try:
        import streamlit as st
        rtok = st.session_state.get("_refresh_token")
        if not rtok:
            return False
        cl = _anon_client()
        if cl is None:
            return False
        res = cl.auth.refresh_session(rtok)
        sess = getattr(res, "session", None)
        at = getattr(sess, "access_token", None)
        rt = getattr(sess, "refresh_token", None)
        if at:
            st.session_state["_access_token"] = at
            if rt:
                st.session_state["_refresh_token"] = rt
            st.session_state["_cookie_set"] = False
            return True
    except Exception:
        pass
    return False


def _is_auth_error(e) -> bool:
    m = str(e).lower()
    return any(w in m for w in (
        "jwt", "token", "expired", "401", "unauthorized",
        "row-level", "rls", "permission denied"))


def _run_with_client(fn):
    """Voer een data-operatie uit via het user-JWT (RLS afgedwongen). Bij een
    auth-fout: token verversen en 1× opnieuw; lukt dat niet, val veilig terug op de
    service_role client (queries blijven op company_id filteren → nooit een lek)."""
    try:
        import streamlit as st
        _ss = st.session_state
    except Exception:
        _ss = None
    # PERF: het JWT-pad één keer per sessie afschrijven als het structureel faalt. Anders
    # doet ELKE save/load een mislukte JWT-write + token-refresh + mislukte retry vóór de
    # service_role-terugval = 4-6 round-trips → 1-2s hapering + witte waas. Service_role
    # filtert altijd op company_id (geen lek); RLS-afdwinging vervalt enkel als het JWT-pad
    # tóch al niet werkte. Restest vanzelf bij een nieuwe sessie (session_state-flag).
    if _ss is None or not _ss.get("_jwt_dead"):
        jwt_cl = _jwt_data_client()
        if jwt_cl is not None:
            try:
                return fn(jwt_cl)
            except Exception as e:
                auth_err = _is_auth_error(e)
                if auth_err and _refresh_access_token():
                    cl2 = _jwt_data_client()
                    if cl2 is not None:
                        try:
                            return fn(cl2)
                        except Exception:
                            pass
                # Aanhoudende auth-fout (RLS/hook-misconfig of niet-verversbare sessie) →
                # dit pad deze sessie niet meer proberen. Niet-auth fout: gewoon terugvallen.
                if auth_err and _ss is not None:
                    _ss["_jwt_dead"] = True
    cl = _get_client()
    if not cl:
        raise DbError("Database niet geconfigureerd.")
    return fn(cl)


# ── LADEN: database → app-session_state-vorm ─────────────────────────────────
def load_company_data(company_id) -> dict:
    """Laad alle data van één company. Draait via het user-JWT (RLS) met veilige
    terugval op service_role; de queries filteren altijd op company_id."""
    if not company_id:
        # Fail-safe: nooit een ongefilterde/lege-tenant query uitvoeren.
        raise DbError("Geen actief bedrijf (company_id) — laden geweigerd.")
    try:
        return _run_with_client(lambda cl: _load_impl(cl, company_id))
    except DbError:
        raise
    except Exception as e:
        raise DbError(f"Laden uit de database mislukte: {e}")


def _load_impl(cl, company_id) -> dict:
    comp = cl.table("companies").select("*").eq("id", company_id).single().execute().data
    klanten   = [_clean(r) for r in cl.table("klanten").select("*").eq("company_id", company_id).order("id").execute().data]
    producten = [_clean(r) for r in cl.table("producten").select("*").eq("company_id", company_id).order("id").execute().data]
    personeel = [_clean(r) for r in cl.table("personeel").select("*").eq("company_id", company_id).order("id").execute().data]
    proj_rows = cl.table("projecten").select("*").eq("company_id", company_id).order("id").execute().data
    taken     = [_clean(r) for r in cl.table("taken").select("*").eq("company_id", company_id).order("id").execute().data]
    agenda_rows = cl.table("agenda_items").select("*").eq("company_id", company_id).execute().data
    links     = cl.table("project_personeel").select("project_id, personeel_id").eq("company_id", company_id).execute().data

    # M:N personeel↔projecten rehydrateren (beide richtingen die de app gebruikt)
    mw_by_proj, proj_by_mw = defaultdict(list), defaultdict(list)
    for l in links:
        mw_by_proj[l["project_id"]].append(l["personeel_id"])
        proj_by_mw[l["personeel_id"]].append(l["project_id"])

    projecten = []
    for r in proj_rows:
        p = _clean(r)
        p["onderdelen"] = r.get("onderdelen") or []
        p["medewerkers"] = sorted(mw_by_proj.get(r["id"], []))
        projecten.append(p)
    for mw in personeel:
        mw["project_ids"] = sorted(proj_by_mw.get(mw["id"], []))

    # agenda_taken (datum → lijst van originele item-dicts) uit payload
    agenda_taken: dict = {}
    for r in sorted(agenda_rows, key=lambda x: (x.get("datum", ""), x.get("id", 0))):
        agenda_taken.setdefault(r["datum"], []).append(dict(r.get("payload") or {}))

    data = {
        "klanten": klanten, "producten": producten, "personeel": personeel,
        "projecten": projecten, "taken": taken,
        "instellingen": comp.get("instellingen") or {},
        "volgende_project_id": comp.get("volgende_project_id") or 1,
        "volgende_klant_id": comp.get("volgende_klant_id") or 1,
        "agenda_taken": agenda_taken,
    }
    # baseline-hashes zetten zodat een save direct na load niets onnodig wegschrijft
    _seed_hashes(company_id, data)
    return data


# ── OPSLAAN: write-through, alleen gewijzigde groepen ────────────────────────
def _group_payloads(state: dict) -> dict:
    """Bouw per logische groep de exacte content (voor hash + schrijven)."""
    return {
        "settings": {
            "instellingen": state.get("instellingen") or {},
            "volgende_project_id": state.get("volgende_project_id") or 1,
            "volgende_klant_id": state.get("volgende_klant_id") or 1,
        },
        "klanten":   [_klant_row(k) for k in state.get("klanten", [])],
        "producten": [_product_row(p) for p in state.get("producten", [])],
        "personeel": [_personeel_row(m) for m in state.get("personeel", [])],
        "taken":     [_taak_row(t) for t in state.get("taken", [])],
        "projecten": [_project_row(p) for p in state.get("projecten", [])],
        "links":     _links_payload(state.get("projecten", [])),
        "agenda":    _agenda_payload(state.get("agenda_taken", {})),
    }


def _links_payload(projecten):
    out = []
    for p in projecten:
        for mid in (p.get("medewerkers") or []):
            out.append({"project_id": p.get("id"), "personeel_id": mid})
    return out


def _agenda_payload(agenda_taken):
    out = []
    for datum, items in (agenda_taken or {}).items():
        for it in (items or []):
            out.append({
                "datum": datum, "tijd": it.get("tijd", ""),
                "titel": it.get("titel", "") or it.get("taak", ""),
                "subtitel": it.get("subtitel", ""), "status": it.get("status", ""),
                "payload": it,
            })
    return out


def _seed_hashes(company_id, state):
    for grp, content in _group_payloads(state).items():
        _last_hashes[(company_id, grp)] = _hash(content)


def _is_changed(company_id, grp, content) -> bool:
    """Alleen CHECKEN (niet markeren) — markeren gebeurt pas ná een geslaagde write,
    zodat een mislukte JWT-poging + service_role-terugval de write veilig overdoet."""
    return _last_hashes.get((company_id, grp)) != _hash(content)


def _mark_saved(company_id, grp, content):
    _last_hashes[(company_id, grp)] = _hash(content)


def _sync_table(cl, table, company_id, rows):
    """Upsert alle rows (+company_id) en verwijder DB-rijen die niet meer bestaan."""
    payload = [dict(r, company_id=company_id) for r in rows]
    if payload:
        cl.table(table).upsert(payload, on_conflict="company_id,id").execute()
    keep = [r["id"] for r in rows if r.get("id") is not None]
    q = cl.table(table).delete().eq("company_id", company_id)
    if keep:
        q = q.not_.in_("id", keep)
    q.execute()


def save_company_data(company_id, state: dict):
    """Schrijf de cache weg naar de database (alleen gewijzigde groepen). Draait via
    het user-JWT (RLS) met veilige terugval op service_role; idempotent, dus de
    terugval-retry kan nooit data dubbel/half schrijven."""
    if not company_id:
        # Fail-safe: nooit naar een onbekende/lege tenant schrijven.
        raise DbError("Geen actief bedrijf (company_id) — opslaan geweigerd.")
    try:
        _run_with_client(lambda cl: _save_impl(cl, company_id, state))
    except DbError:
        raise
    except Exception as e:
        raise DbError(f"Opslaan in de database mislukte: {e}")


def _save_impl(cl, company_id, state):
    groups = _group_payloads(state)

    def write(grp, do_write):
        # Hash pas markeren ná een geslaagde write (retry-veilig bij terugval).
        if _is_changed(company_id, grp, groups[grp]):
            do_write()
            _mark_saved(company_id, grp, groups[grp])

    s = groups["settings"]
    write("settings", lambda: cl.table("companies").update({
        "instellingen": s["instellingen"],
        "volgende_project_id": s["volgende_project_id"],
        "volgende_klant_id": s["volgende_klant_id"],
        "updated_at": _now(),
    }).eq("id", company_id).execute())

    # Volgorde respecteert FK's: klanten/personeel vóór projecten vóór de join.
    write("klanten",   lambda: _sync_table(cl, "klanten",   company_id, groups["klanten"]))
    write("producten", lambda: _sync_table(cl, "producten", company_id, groups["producten"]))
    write("personeel", lambda: _sync_table(cl, "personeel", company_id, groups["personeel"]))
    write("taken",     lambda: _sync_table(cl, "taken",     company_id, groups["taken"]))
    write("projecten", lambda: _sync_table(cl, "projecten", company_id, groups["projecten"]))

    def _write_links():
        cl.table("project_personeel").delete().eq("company_id", company_id).execute()
        if groups["links"]:
            cl.table("project_personeel").insert(
                [dict(l, company_id=company_id) for l in groups["links"]]).execute()
    write("links", _write_links)

    def _write_agenda():
        cl.table("agenda_items").delete().eq("company_id", company_id).execute()
        if groups["agenda"]:
            cl.table("agenda_items").insert(
                [dict(a, company_id=company_id) for a in groups["agenda"]]).execute()
    write("agenda", _write_agenda)


# ── ADMIN / PLATFORM-BEHEER ──────────────────────────────────────────────────
# Platform-brede (cross-tenant) queries voor het /admin-dashboard. Deze draaien
# BEWUST via de service_role client (RLS-bypass) omdat ze over álle bedrijven heen
# kijken. Ze mogen daarom UITSLUITEND worden aangeroepen vanuit de server-side
# admin-guard (auth.is_platform_admin()). De muterende functies her-verifiëren de
# adminrol bovendien zelf (defense-in-depth) vóór ze iets wegschrijven.
_ADMIN_STATUSSEN = ("trial", "active", "suspended")


def admin_user_is_admin(user_id) -> bool:
    """True als deze auth-gebruiker de platform-adminvlag heeft (is_admin in app_users).
    Veilige terugval: False als de kolom ontbreekt (migratie nog niet gedraaid), als de
    gebruiker onbekend is, of bij elke andere fout — nooit per ongeluk toegang verlenen."""
    if not user_id:
        return False
    cl = _get_client()
    if not cl:
        return False
    try:
        r = cl.table("app_users").select("is_admin").eq("id", str(user_id)).limit(1).execute()
        return bool(r.data and r.data[0].get("is_admin"))
    except Exception:
        # Kolom ontbreekt of query faalt → géén admin (fail-closed).
        return False


def _assert_admin(acting_user_id):
    """Defense-in-depth: weiger de mutatie als de aanroeper geen platform-admin is."""
    if not admin_user_is_admin(acting_user_id):
        raise DbError("Geen adminrechten voor deze actie.")


def admin_stats() -> dict:
    """Platform-brede tellingen over alle tenants: aantal geregistreerde gebruikers
    (schilders) en totaal aantal calculaties/offertes (projecten). count='exact' haalt
    enkel het totaal op (geen rij-data) → efficiënt."""
    cl = _get_client()
    if not cl:
        raise DbError("Database niet geconfigureerd.")
    try:
        users = cl.table("app_users").select("id", count="exact").limit(1).execute()
        projecten = cl.table("projecten").select("id", count="exact").limit(1).execute()
        companies = cl.table("companies").select("id", count="exact").limit(1).execute()
        return {
            "gebruikers": int(users.count or 0),
            "projecten": int(projecten.count or 0),
            "bedrijven": int(companies.count or 0),
        }
    except Exception as e:
        raise DbError(f"Kon platformstatistieken niet laden: {e}")


def admin_list_users() -> list:
    """Alle platformgebruikers + de abonnement/proef-status van hun bedrijf, gesorteerd
    op registratiedatum (nieuwste eerst). Service_role (cross-tenant)."""
    cl = _get_client()
    if not cl:
        raise DbError("Database niet geconfigureerd.")
    try:
        try:
            users = (cl.table("app_users")
                       .select("id, email, role, created_at, is_admin, company_id")
                       .order("created_at", desc=True).execute().data) or []
        except Exception:
            # is_admin-kolom ontbreekt nog → laad zonder, default False.
            users = (cl.table("app_users")
                       .select("id, email, role, created_at, company_id")
                       .order("created_at", desc=True).execute().data) or []

        comp_ids = list({u["company_id"] for u in users if u.get("company_id")})
        comps = {}
        if comp_ids:
            crows = (cl.table("companies")
                       .select("id, naam, subscription_status, trial_ends_at, plan")
                       .in_("id", comp_ids).execute().data) or []
            comps = {c["id"]: c for c in crows}

        out = []
        for u in users:
            c = comps.get(u.get("company_id"), {})
            out.append({
                "id": u.get("id"),
                "email": u.get("email") or "—",
                "role": u.get("role") or "owner",
                "is_admin": bool(u.get("is_admin")),
                "created_at": u.get("created_at"),
                "company_id": u.get("company_id"),
                "company_naam": c.get("naam") or "—",
                "subscription_status": (c.get("subscription_status") or "—"),
                "trial_ends_at": c.get("trial_ends_at"),
                "plan": c.get("plan") or "free",
            })
        return out
    except DbError:
        raise
    except Exception as e:
        raise DbError(f"Kon gebruikers niet laden: {e}")


def admin_extend_trial(company_id, acting_user_id, days: int = 14) -> str:
    """Verleng de proefperiode van een bedrijf met `days` dagen vanaf nu en zet de status
    op 'trial'. Her-verifieert de adminrol. Geeft de nieuwe einddatum (ISO-datum) terug."""
    _assert_admin(acting_user_id)
    if not company_id:
        raise DbError("Geen bedrijf opgegeven.")
    cl = _get_client()
    if not cl:
        raise DbError("Database niet geconfigureerd.")
    new_end = datetime.now(timezone.utc) + timedelta(days=int(days))
    try:
        cl.table("companies").update({
            "trial_ends_at": new_end.isoformat(),
            "subscription_status": "trial",
            "updated_at": _now(),
        }).eq("id", company_id).execute()
        return new_end.date().isoformat()
    except Exception as e:
        raise DbError(f"Proefperiode verlengen mislukte: {e}")


def admin_set_status(company_id, status, acting_user_id) -> None:
    """Wijzig de abonnementsstatus van een bedrijf (trial | active | suspended).
    'active' = account geactiveerd, 'suspended' = gedeactiveerd. Her-verifieert admin."""
    _assert_admin(acting_user_id)
    if not company_id:
        raise DbError("Geen bedrijf opgegeven.")
    if status not in _ADMIN_STATUSSEN:
        raise DbError("Ongeldige status.")
    cl = _get_client()
    if not cl:
        raise DbError("Database niet geconfigureerd.")
    try:
        cl.table("companies").update({
            "subscription_status": status,
            "updated_at": _now(),
        }).eq("id", company_id).execute()
    except Exception as e:
        raise DbError(f"Status wijzigen mislukte: {e}")
