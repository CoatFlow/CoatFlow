"""
CoatFlow — authenticatie & tenant-isolatie (Fase 2).

Supabase Auth is de bron van waarheid voor inloggen. Deze module levert:
  * require_auth()  → route-guard: toont login/registratie + st.stop() als niet ingelogd.
  * sign-in / sign-up / sign-out, met nette foutmeldingen (geen crashes).
  * Tenant-creatie bij registratie: nieuwe gebruiker → eigen company (UUID) → app_users-koppeling.
  * Eenmalige claim van de gemigreerde 'default company' door de eerste eigenaar
    (overgang anoniem→beveiligd zonder dataverlies).

Auth-calls gebruiken de ANON key; het aanmaken/koppelen van company + app_users
gebruikt de service_role client uit db.py (de nieuwe user heeft nog geen rijen,
dus RLS — straks Fase 3 — mag hier niet blokkeren).

Alleen actief wanneer Supabase is geconfigureerd. In lokale JSON-modus draait de
app zonder login (single-user dev), precies zoals voorheen.
"""
from __future__ import annotations

import uuid as _uuid

import streamlit as st

import db  # service_role client + secrets + DbError


class AuthError(Exception):
    """Nette, leesbare auth-fout voor in de UI."""


def _valid_uuid(v) -> bool:
    """True als v een echte UUID is (niet leeg en geen placeholdertekst)."""
    try:
        _uuid.UUID(str(v))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


# ── clients ──────────────────────────────────────────────────────────────────
_auth_cl = None


def _auth_client():
    """Anon-key client voor sign in/up/out (publieke auth-flows)."""
    global _auth_cl
    if _auth_cl is not None:
        return _auth_cl
    s = db._secrets()
    if not s or not s.get("supabase_url"):
        raise AuthError("Authenticatie is niet geconfigureerd.")
    key = s.get("supabase_anon_key") or s.get("supabase_service_key")
    if not key:
        raise AuthError("Geen Supabase-sleutel gevonden in de configuratie.")
    try:
        from supabase import create_client
        _auth_cl = create_client(s["supabase_url"], key)
        return _auth_cl
    except Exception as e:
        raise AuthError(f"Kon de authenticatie-service niet bereiken: {e}")


def is_active() -> bool:
    """True als login afgedwongen moet worden (Supabase geconfigureerd)."""
    try:
        return db.is_enabled()
    except Exception:
        return False


def is_platform_admin() -> bool:
    """SERVER-SIDE platform-admincheck voor het /admin-dashboard.

    True alleen als: (1) er een ingelogde sessie is, (2) Supabase actief is, én
    (3) de gekoppelde app_users-rij is_admin = true heeft. De databasecontrole draait
    via de service_role client in db.py (Python-zijde, dus niet te omzeilen vanuit de
    browser). Het resultaat wordt per sessie gecachet (`_is_admin`) zodat we niet bij
    elke rerun opnieuw queryen; bij uitloggen wordt de cache gewist (_clear_session).

    Fail-closed: bij niet-ingelogd, JSON-modus, ontbrekende is_admin-kolom of welke
    fout dan ook → False. Een niet-admin krijgt dus nooit toegang."""
    if not st.session_state.get("authenticated"):
        return False
    if not is_active():
        return False
    cached = st.session_state.get("_is_admin")
    if cached is not None:
        return bool(cached)
    uid = st.session_state.get("user_id")
    try:
        val = bool(db.admin_user_is_admin(uid))
    except Exception:
        val = False
    st.session_state["_is_admin"] = val
    return val


# ── tenant-koppeling ─────────────────────────────────────────────────────────
def _ensure_company(user_id: str, email: str) -> str:
    """Geef de company van deze gebruiker terug; maak/claim er één als die ontbreekt."""
    svc = db._get_client()
    if not svc:
        raise AuthError("Database niet bereikbaar.")
    try:
        r = svc.table("app_users").select("company_id").eq("id", user_id).limit(1).execute()
        if r.data:
            return r.data[0]["company_id"]

        # Geen koppeling → ALTIJD een vers, leeg bedrijf voor deze gebruiker.
        # (De oude "claim de gemigreerde default company"-logica is bewust VERWIJDERD:
        # die koppelde de eerste gebruiker aan het gemigreerde test-/demobedrijf, waardoor
        # die persoon alle oude testdata zag. Nu is elke gebruiker volledig geïsoleerd met
        # een eigen, leeg bedrijf — géén gedeelde of geërfde data.)
        comp = svc.table("companies").insert({
            "naam": "Mijn bedrijf",
            # Volledig, geldig profiel bij registratie (geen leeg {}), zodat een nieuw
            # bedrijf meteen alle vereiste instellingen heeft. Zelfde bron als de
            # app-side merge in init_state() → geen drift.
            "instellingen": dict(db.STANDAARD_INSTELLINGEN),
        }).execute().data[0]
        svc.table("app_users").insert({
            "id": user_id, "company_id": comp["id"],
            "email": email, "role": "owner",
        }).execute()
        return comp["id"]
    except AuthError:
        raise
    except Exception as e:
        raise AuthError(f"Kon het bedrijf niet aan je account koppelen: {e}")


def _friendly(err: Exception) -> str:
    msg = str(getattr(err, "message", "") or err).lower()
    if "invalid login" in msg or "invalid" in msg and "credential" in msg:
        return "Onjuiste e-mail of wachtwoord."
    if "already registered" in msg or "already exists" in msg or "user already" in msg:
        return "Er bestaat al een account met dit e-mailadres."
    if "password" in msg and ("least" in msg or "weak" in msg or "short" in msg):
        return "Wachtwoord te zwak — gebruik minimaal 6 tekens."
    if "email" in msg and "valid" in msg:
        return "Vul een geldig e-mailadres in."
    if "not confirmed" in msg or "confirm" in msg:
        return "Bevestig eerst je e-mailadres via de link in je mailbox."
    return "Er ging iets mis. Probeer het opnieuw."


# ── sessie-cookie (onthoud login bij refresh) ────────────────────────────────
_COOKIE = "cf_sess"
_COOKIE_MAXAGE = 7 * 24 * 3600   # 7 dagen


def _fresh_auth_client():
    """Nieuwe anon-client (niet de singleton) — voorkomt dat één gebruikers­sessie
    in de gedeelde client blijft hangen bij het herstellen (multi-user veilig)."""
    s = db._secrets()
    if not s or not s.get("supabase_url"):
        raise AuthError("Authenticatie is niet geconfigureerd.")
    from supabase import create_client
    key = s.get("supabase_anon_key") or s.get("supabase_service_key")
    return create_client(s["supabase_url"], key)


# ── cookie-transport ─────────────────────────────────────────────────────────
# We gebruiken streamlit-cookies-controller: die schrijft/leest de cookie via een
# component-iframe dat op HETZELFDE origin als de app draait. De cookie belandt zo
# op het app-domein en overleeft een refresh (F5).
#
# WAAROM niet de oude aanpak: die deed `window.parent.document.cookie` vanuit een
# components.html-iframe. Op Streamlit Cloud blokkeert de iframe-sandbox die toegang
# tot het PARENT-document (cross-frame) → de cookie werd nooit geschreven, dus na een
# refresh was je uitgelogd. Lokaal werkte het toevallig wél, vandaar dat de bug pas
# in de cloud zichtbaar werd. De component schrijft in zijn EIGEN same-origin document
# en heeft die parent-toegang niet nodig. De ruwe JS blijft als terugval bestaan voor
# het geval de package ontbreekt.

def _cookie_ctrl():
    """Eén CookieController per rerun. De controller cachet zichzelf in
    st.session_state, dus meermaals per run instantiëren rendert de component maar
    één keer (geen dubbele-widget-fout). None als de package ontbreekt → JS-terugval."""
    try:
        from streamlit_cookies_controller import CookieController
        return CookieController(key="cf_cookies")
    except Exception:
        return None


def _set_cookie(token: str):
    ctrl = _cookie_ctrl()
    if ctrl is not None:
        try:
            import datetime as _dt
            # SameSite=Lax + géén Secure: werkt op zowel http://localhost (dev) als
            # https (cloud); Lax stuurt de cookie mee bij een top-level refresh.
            ctrl.set(_COOKIE, token,
                     expires=_dt.datetime.now() + _dt.timedelta(seconds=_COOKIE_MAXAGE),
                     same_site="lax")
            return
        except Exception:
            pass
    _set_cookie_js(token)


def _clear_cookie():
    ctrl = _cookie_ctrl()
    if ctrl is not None:
        try:
            if ctrl.get(_COOKIE) is not None:   # remove() raist KeyError als afwezig
                ctrl.remove(_COOKIE)
            return
        except Exception:
            pass
    _clear_cookie_js()


def _read_cookie():
    # 1) Native request-cookies: direct beschikbaar op de eerste run (geen extra
    #    rerun/flash) zodra de cookie op het app-domein staat.
    try:
        v = st.context.cookies.get(_COOKIE)
        if v:
            return v
    except Exception:
        pass
    # 2) Component-cache: betrouwbaar op Streamlit Cloud waar (1) faalt. Kan één
    #    extra rerun kosten voordat de waarde beschikbaar is.
    ctrl = _cookie_ctrl()
    if ctrl is not None:
        try:
            return ctrl.get(_COOKIE)
        except Exception:
            return None
    return None


# ── ruwe-JS terugval (alleen als de cookie-component ontbreekt) ───────────────
def _set_cookie_js(token: str):
    import json as _j
    import streamlit.components.v1 as _c
    js = ("<script>try{var d=window.parent.document;"
          "var sec=(window.parent.location.protocol==='https:')?'; Secure':'';"
          "d.cookie='" + _COOKIE + "='+" + _j.dumps(token)
          + "+'; path=/; max-age=" + str(_COOKIE_MAXAGE)
          + "; SameSite=Lax'+sec;}catch(e){}</script>")
    _c.html(js, height=0)


def _clear_cookie_js():
    import streamlit.components.v1 as _c
    _c.html("<script>try{window.parent.document.cookie='" + _COOKIE
            + "=; path=/; max-age=0';}catch(e){}</script>", height=0)


# ── publieke acties ──────────────────────────────────────────────────────────
def sign_in(email: str, password: str):
    cl = _auth_client()
    try:
        res = cl.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:
        raise AuthError(_friendly(e))
    if not getattr(res, "user", None):
        raise AuthError("Onjuiste e-mail of wachtwoord.")
    sess = getattr(res, "session", None)
    company_id = _ensure_company(res.user.id, email)
    _set_session(res.user.id, email, company_id,
                 getattr(sess, "refresh_token", None),
                 getattr(sess, "access_token", None))


def sign_up(email: str, password: str):
    cl = _auth_client()
    try:
        res = cl.auth.sign_up({"email": email, "password": password})
    except Exception as e:
        raise AuthError(_friendly(e))
    user = getattr(res, "user", None)
    if not user:
        raise AuthError("Registratie mislukte. Probeer een ander e-mailadres.")
    # De company_id wordt door de DATABASE-TRIGGER in public.app_users aangemaakt
    # (zie supabase_trigger.sql) — dus niet meer vanuit Python. Werkt ook als de
    # gebruiker zijn e-mail nog moet bevestigen.
    sess = getattr(res, "session", None)
    if sess is None:
        return False  # e-mailbevestiging vereist → "check je mailbox"
    # (alleen als e-mailbevestiging UIT staat: direct ingelogd)
    company_id = _ensure_company(user.id, email)
    _set_session(user.id, email, company_id,
                 getattr(sess, "refresh_token", None),
                 getattr(sess, "access_token", None))
    return True


def reset_password(email: str):
    """Stuur een wachtwoord-reset mail via Supabase."""
    cl = _auth_client()
    try:
        cl.auth.reset_password_for_email(email)
    except Exception as e:
        raise AuthError(_friendly(e))


def sign_out():
    try:
        cl = _auth_client()
        cl.auth.sign_out()
    except Exception:
        pass  # lokale sessie hieronder altijd opruimen
    _clear_session()
    # Cookie wordt op de eerstvolgende (login-)render gewist; zo voorkomen we dat
    # een refresh de zojuist uitgelogde sessie meteen weer herstelt.
    st.session_state["_force_logout"] = True


def try_restore_session():
    """Herstel een bewaarde sessie na een page-refresh (cookie → Supabase).
    Faalt stil terug naar het inlogscherm; nooit een crash."""
    if st.session_state.get("authenticated"):
        return
    token = _read_cookie()
    if not token:
        return
    try:
        cl = _fresh_auth_client()
        res = cl.auth.refresh_session(token)
        sess = getattr(res, "session", None)
        user = getattr(res, "user", None) or getattr(sess, "user", None)
        if not user:
            _clear_cookie()
            return
        email = getattr(user, "email", "") or ""
        company_id = _ensure_company(user.id, email)
        new_token = getattr(sess, "refresh_token", None) or token
        _set_session(user.id, email, company_id, new_token,
                     getattr(sess, "access_token", None))
        st.session_state["_cookie_set"] = False  # (nieuwe) token opnieuw bewaren
    except Exception:
        _clear_cookie()  # verlopen/ongeldig → gewoon opnieuw inloggen


def persist_session():
    """Bewaar de refresh-token in een cookie zodat een refresh ingelogd blijft.
    Draait op een ingelogde render (niet tijdens login-submit) zodat de JS echt
    uitvoert. Eénmalig per sessie (of na token-rotatie)."""
    if (st.session_state.get("authenticated")
            and st.session_state.get("_refresh_token")
            and not st.session_state.get("_cookie_set")):
        _set_cookie(st.session_state["_refresh_token"])
        st.session_state["_cookie_set"] = True


# ── sessie-state ─────────────────────────────────────────────────────────────
def _set_session(user_id, email, company_id, refresh_token=None, access_token=None):
    st.session_state.authenticated = True
    st.session_state.user_id = user_id
    st.session_state.user_email = email
    st.session_state.company_id = company_id
    if refresh_token:
        st.session_state["_refresh_token"] = refresh_token
    if access_token:
        # access-token = het user-JWT waarmee db.py queryt → RLS dwingt de tenant af.
        st.session_state["_access_token"] = access_token


def _clear_session():
    # Auth-state + alle in-memory data-cache wissen zodat een volgende login
    # vers laadt (geen datalek tussen accounts binnen dezelfde browsersessie).
    for k in ("authenticated", "user_id", "user_email", "company_id",
              "_company_id", "_data_geladen", "_refresh_token", "_access_token",
              "_cookie_set", "_is_admin",
              "klanten", "projecten", "personeel", "producten", "taken",
              "instellingen", "volgende_project_id", "volgende_klant_id",
              "agenda_taken"):
        st.session_state.pop(k, None)


# ── route-guard + login-UI ───────────────────────────────────────────────────
def require_auth():
    """Blokkeer de hele app tot de gebruiker is ingelogd."""
    if st.session_state.get("authenticated"):
        return
    if st.session_state.pop("_force_logout", False):
        _clear_cookie()      # zojuist uitgelogd → cookie wissen, niet herstellen
        _render_login()
        st.stop()
    try_restore_session()    # page-refresh → bewaarde sessie terughalen
    if st.session_state.get("authenticated"):
        return
    _render_login()
    st.stop()


def _inject_login_css():
    """Tweekoloms hero-loginscherm. In-app via st.markdown geïnjecteerd zodat de
    styling ná het inloggen vanzelf verdwijnt (geen invloed op het dashboard)."""
    import urllib.parse as _url

    def _ico(paths, color="#3B82F6", sw="1.9"):
        svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='"
               + color + "' stroke-width='" + sw + "' stroke-linecap='round' stroke-linejoin='round'>"
               + paths + "</svg>")
        return "data:image/svg+xml," + _url.quote(svg, safe="")

    logo = "data:image/svg+xml," + _url.quote(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48' fill='none'>"
        "<path d='M35 14.5 A15.5 15.5 0 1 0 35 33.5' stroke='#2563EB' stroke-width='8.5' "
        "stroke-linecap='round'/></svg>", safe="")
    clip = _ico("<rect width='8' height='4' x='8' y='2' rx='1'/><path d='M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2'/>")
    fold = _ico("<path d='M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z'/>")
    calc = _ico("<rect width='16' height='20' x='4' y='2' rx='2'/><line x1='8' x2='16' y1='6' y2='6'/><path d='M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01M8 19h.01M12 19h.01'/>")
    chart = _ico("<path d='M3 3v18h18'/><path d='M18 17V9'/><path d='M13 17V5'/><path d='M8 17v-3'/>")
    shield = _ico("<path d='M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1Z'/><path d='m9 12 2 2 4-4'/>")
    mail = _ico("<rect width='20' height='16' x='2' y='4' rx='2'/><path d='m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7'/>", color="#94A3B8", sw="2")
    lock = _ico("<rect width='18' height='11' x='3' y='11' rx='2'/><path d='M7 11V7a5 5 0 0 1 10 0v4'/>", color="#94A3B8", sw="2")

    css = r"""
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
    html, body, input, button, textarea, [class*="css"] { font-family:'DM Sans',sans-serif !important; }
    .stApp { background:#EEF2F7; }
    #MainMenu, header, footer, [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] { display:none !important; }
    .stApp [data-testid="stMainBlockContainer"], .stApp .block-container { padding:0 !important; max-width:100% !important; }
    /* geen flex-gap boven de kolommen (anders 16px witruimte boven het navy paneel) */
    [data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] { gap:0 !important; }

    /* verborgen markers innemen geen flex-ruimte */
    [data-testid="stElementContainer"]:has(span.cf-card-mk),
    [data-testid="stElementContainer"]:has(span.cf-mail-mk),
    [data-testid="stElementContainer"]:has(span.cf-lock-mk),
    [data-testid="stElementContainer"]:has(span.cf-forgot-mk),
    [data-testid="stElementContainer"]:has(span.cf-google-mk),
    [data-testid="stElementContainer"]:has(span.cf-switch) { display:none !important; }

    /* ---- buitenste split 34 / 66 ---- */
    [data-testid="stHorizontalBlock"]:has(.cf-hero) { gap:0 !important; min-height:100vh; align-items:stretch; }
    [data-testid="stHorizontalBlock"]:has(.cf-hero) > [data-testid="stColumn"] { padding:0 !important; }
    [data-testid="stHorizontalBlock"]:has(.cf-hero) > [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"] {
        min-height:100vh; justify-content:center; align-items:center; gap:0 !important; padding:34px 24px;
    }

    /* ---- linker hero-paneel ---- */
    .cf-hero { box-sizing:border-box; min-height:100vh; color:#fff; padding:54px 50px;
        background:linear-gradient(157deg,#102C57 0%,#0B2042 50%,#081429 100%);
        display:flex; flex-direction:column; justify-content:space-between; }
    .cf-logo { width:38px; height:38px; flex:none; background:url('__LOGO__') center/contain no-repeat; }
    .cf-hero-brand { display:flex; align-items:center; gap:12px; font-size:25px; font-weight:700; letter-spacing:-.3px; }
    .cf-hero-mid { margin:26px 0; }
    .cf-hero-title { font-size:33px; line-height:1.18; font-weight:800; letter-spacing:-.6px; margin:0 0 16px; max-width:430px; }
    .cf-hero-title .cf-ac { color:#4C8DFF; }
    .cf-hero-sub { font-size:15px; line-height:1.55; color:#A9BBD4; max-width:380px; margin:0; }
    .cf-hr { height:1px; background:rgba(255,255,255,.12); margin:26px 0; max-width:430px; }
    .cf-feat { display:flex; gap:15px; align-items:flex-start; margin:0 0 22px; max-width:420px; }
    .cf-feat:last-child { margin-bottom:0; }
    .cf-feat-ic { width:26px; height:26px; flex:none; margin-top:1px; background-repeat:no-repeat; background-position:center; background-size:24px; }
    .cf-ic-clip{background-image:url('__CLIP__');}
    .cf-ic-fold{background-image:url('__FOLD__');}
    .cf-ic-calc{background-image:url('__CALC__');}
    .cf-ic-chart{background-image:url('__CHART__');}
    .cf-ic-shield{background-image:url('__SHIELD__');}
    .cf-feat-t { font-size:15px; font-weight:700; color:#F2F6FC; margin:0 0 2px; }
    .cf-feat-d { font-size:13px; line-height:1.5; color:#9BAFCB; margin:0; }
    .cf-hero-sec { display:flex; gap:15px; align-items:flex-start; max-width:420px; }

    /* ---- login-kaart ---- */
    [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] span.cf-card-mk) {
        width:500px !important; max-width:92vw; margin-left:auto !important; margin-right:auto !important; }
    [data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] span.cf-card-mk) {
        background:#fff; border:1px solid #EAEEF3 !important; border-radius:22px;
        box-shadow:0 20px 48px -18px rgba(20,45,90,.22), 0 6px 16px -10px rgba(20,45,90,.10);
        padding:42px 44px 36px !important; gap:0 !important; }
    .cf-card-head { text-align:center; margin:0 0 24px; }
    .cf-card-brand { display:inline-flex; align-items:center; gap:9px; font-size:21px; font-weight:700; color:#11264A; margin-bottom:18px; }
    .cf-card-brand .cf-logo { width:28px; height:28px; }
    .cf-card-title { font-size:27px; font-weight:800; color:#11264A; letter-spacing:-.5px; margin:0 0 7px; }
    .cf-card-sub { font-size:13.5px; color:#7689A3; margin:0; line-height:1.45; }

    /* labels + invoervelden */
    .stApp [data-testid="stWidgetLabel"] p { font-size:13px !important; font-weight:600 !important; color:#33455F !important; margin-bottom:6px !important; }
    .stApp div[data-testid="stTextInput"] { margin-bottom:14px; }
    .stApp div[data-testid="stTextInput"] [data-baseweb="base-input"] { border-radius:11px !important; background:#fff !important; border:1.5px solid #E2E8F0 !important; height:48px !important; }
    .stApp div[data-testid="stTextInput"] [data-baseweb="base-input"]:focus-within { border-color:#3B82F6 !important; box-shadow:0 0 0 3px rgba(59,130,246,.13) !important; }
    .stApp div[data-testid="stTextInput"] input { height:46px !important; font-size:14px !important; background:#fff !important; color:#0F2444 !important;
        padding-left:42px !important; background-repeat:no-repeat !important; background-position:14px center !important; background-size:18px !important; }
    [data-testid="stElementContainer"]:has(span.cf-mail-mk) + [data-testid="stElementContainer"] input { background-image:url('__MAIL__') !important; }
    [data-testid="stElementContainer"]:has(span.cf-lock-mk) + [data-testid="stElementContainer"] input { background-image:url('__LOCK__') !important; }
    /* dubbel oog weg: verberg het browser-eigen wachtwoord-onthul-icoon (Edge ::-ms-reveal),
       Streamlit levert al een eigen oog-toggle → anders twee oogjes naast elkaar */
    input[type="password"]::-ms-reveal, input[type="password"]::-ms-clear { display:none !important; }
    /* "Press Enter to apply"-hint onder de invulvakken verbergen (login heeft eigen CSS,
       erft de globale app-regel niet) */
    [data-testid="InputInstructions"] { display:none !important; }

    /* primaire knop (Inloggen / Account aanmaken / Stuur resetlink) */
    .stApp .stButton > button[kind="primary"] {
        background:linear-gradient(135deg,#3B82F6 0%,#2563EB 100%) !important; color:#fff !important; border:none !important;
        border-radius:11px !important; height:46px !important; font-weight:600 !important; font-size:14.5px !important; width:100% !important;
        margin-top:6px !important; box-shadow:0 8px 18px -7px rgba(37,99,235,.55) !important; transition:filter .15s, transform .05s !important; }
    .stApp .stButton > button[kind="primary"]:hover { filter:brightness(1.07) !important; }
    .stApp .stButton > button[kind="primary"]:active { transform:translateY(1px) !important; }

    /* 'Wachtwoord vergeten?' rechts uitgelijnd */
    [data-testid="stElementContainer"]:has(span.cf-forgot-mk) + [data-testid="stElementContainer"] { display:flex !important; width:100% !important; justify-content:flex-end !important; margin:-6px 0 2px; }
    [data-testid="stElementContainer"]:has(span.cf-forgot-mk) + [data-testid="stElementContainer"] .stButton { width:auto !important; }
    [data-testid="stElementContainer"]:has(span.cf-forgot-mk) + [data-testid="stElementContainer"] .stButton > button {
        background:transparent !important; border:none !important; box-shadow:none !important; color:#2563EB !important;
        font-size:13px !important; font-weight:500 !important; height:auto !important; min-height:0 !important; padding:2px 0 !important; width:auto !important; }
    [data-testid="stElementContainer"]:has(span.cf-forgot-mk) + [data-testid="stElementContainer"] .stButton > button:hover { color:#1D4ED8 !important; text-decoration:underline !important; background:transparent !important; }

    /* scheidingslijn met 'of' */
    .cf-divider { display:flex; align-items:center; gap:12px; margin:18px 0 14px; color:#9AA7B8; font-size:12px; font-weight:500; }
    .cf-divider::before, .cf-divider::after { content:''; flex:1; height:1px; background:#E6EBF1; }

    /* Google-knop: wit met rand + officieel G-logo */
    [data-testid="stElementContainer"]:has(span.cf-google-mk) + [data-testid="stElementContainer"] .stButton > button {
        background:#fff !important; color:#1F2A3D !important; border:1.5px solid #E2E8F0 !important; border-radius:11px !important;
        height:46px !important; font-weight:600 !important; font-size:14px !important; box-shadow:none !important; width:100% !important; transition:background .14s, border-color .14s !important; }
    [data-testid="stElementContainer"]:has(span.cf-google-mk) + [data-testid="stElementContainer"] .stButton > button:hover { background:#F8FAFC !important; border-color:#CBD5E1 !important; }
    [data-testid="stElementContainer"]:has(span.cf-google-mk) + [data-testid="stElementContainer"] .stButton > button p::before {
        content:''; display:inline-block; width:18px; height:18px; margin-right:10px; vertical-align:-3px; background-repeat:no-repeat; background-size:contain;
        background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E%3Cpath fill='%23EA4335' d='M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z'/%3E%3Cpath fill='%234285F4' d='M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z'/%3E%3Cpath fill='%23FBBC05' d='M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z'/%3E%3Cpath fill='%2334A853' d='M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z'/%3E%3C/svg%3E"); }

    /* onderste wissel-link (twee-tonig) */
    [data-testid="stElementContainer"]:has(span.cf-switch) + [data-testid="stElementContainer"] { display:flex; justify-content:center; margin-top:20px; }
    [data-testid="stElementContainer"]:has(span.cf-switch) + [data-testid="stElementContainer"] .stButton { display:flex !important; justify-content:center !important; width:100% !important; }
    [data-testid="stElementContainer"]:has(span.cf-switch) + [data-testid="stElementContainer"] .stButton > button {
        background:transparent !important; border:none !important; box-shadow:none !important; color:#2563EB !important;
        font-size:13.5px !important; font-weight:600 !important; height:auto !important; min-height:0 !important; padding:0 !important; width:auto !important; }
    [data-testid="stElementContainer"]:has(span.cf-switch) + [data-testid="stElementContainer"] .stButton > button:hover { text-decoration:underline !important; background:transparent !important; }
    [data-testid="stElementContainer"]:has(span.cf-switch-reg) + [data-testid="stElementContainer"] .stButton > button p::before { content:'Nog geen account?\00a0\00a0'; color:#6B7C93; font-weight:400; }
    [data-testid="stElementContainer"]:has(span.cf-switch-login) + [data-testid="stElementContainer"] .stButton > button p::before { content:'Heb je al een account?\00a0\00a0'; color:#6B7C93; font-weight:400; }

    .stApp [data-testid="stAlert"] { border-radius:11px; font-size:13.5px; }

    /* ---- mobiel: paneel als compacte banner bóven de kaart ---- */
    @media (max-width: 820px) {
        [data-testid="stHorizontalBlock"]:has(.cf-hero) { min-height:auto !important; }
        .cf-hero { min-height:auto !important; padding:32px 26px 30px !important; }
        .cf-hero-mid { margin:18px 0 !important; }
        .cf-hero-title { font-size:26px !important; }
        .cf-hr, .cf-feat, .cf-hero-sec { display:none !important; }
        .cf-hero-sub { margin-bottom:0 !important; }
        [data-testid="stHorizontalBlock"]:has(.cf-hero) > [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"] {
            min-height:auto !important; justify-content:flex-start !important; padding:26px 14px 40px !important; }
        [data-testid="stLayoutWrapper"]:has(> [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"] span.cf-card-mk) { width:100% !important; }
    }
    """
    css = (css.replace("__LOGO__", logo).replace("__CLIP__", clip).replace("__FOLD__", fold)
              .replace("__CALC__", calc).replace("__CHART__", chart).replace("__SHIELD__", shield)
              .replace("__MAIL__", mail).replace("__LOCK__", lock))
    st.markdown("<style>" + css + "</style>", unsafe_allow_html=True)


def _render_login():
    _inject_login_css()
    view = st.session_state.get("cf_auth_view", "login")

    def _feat(ic, title, desc):
        return ('<div class="cf-feat"><div class="cf-feat-ic cf-ic-' + ic + '"></div>'
                '<div><div class="cf-feat-t">' + title + '</div>'
                '<div class="cf-feat-d">' + desc + '</div></div></div>')

    hero = (
        '<div class="cf-hero">'
        '<div class="cf-hero-brand"><div class="cf-logo"></div><span>CoatFlow</span></div>'
        '<div class="cf-hero-mid">'
        '<div class="cf-hero-title">Dé alles-in-één oplossing voor <span class="cf-ac">schilders.</span></div>'
        '<div class="cf-hero-sub">Offertes, projecten, calculaties en facturen in één overzichtelijk systeem.</div>'
        '<div class="cf-hr"></div>'
        + _feat("clip", "Offertes &amp; Facturen", "Maak professionele offertes en facturen in enkele minuten.")
        + _feat("fold", "Projecten Beheren", "Houd al je projecten, klanten en taken overzichtelijk bij.")
        + _feat("calc", "Slim Calculeren", "Bereken materiaal, arbeid en kosten snel en nauwkeurig.")
        + _feat("chart", "Overzicht &amp; Inzicht", "Krijg inzicht in je omzet, projecten en prestaties.")
        + '</div>'
        '<div class="cf-hero-sec"><div class="cf-feat-ic cf-ic-shield"></div>'
        '<div><div class="cf-feat-t">Veilig &amp; Betrouwbaar</div>'
        '<div class="cf-feat-d">Jouw gegevens zijn veilig bij ons.</div></div></div>'
        '</div>')

    heads = {
        "login": ("Welkom terug", "Log in om toegang te krijgen tot je account"),
        "register": ("Account aanmaken", "Maak in één minuut je eigen bedrijfsomgeving aan"),
        "reset": ("Wachtwoord vergeten?", "We sturen je een link om een nieuw wachtwoord in te stellen"),
    }
    title, sub = heads[view]

    col_l, col_r = st.columns([34, 66])
    with col_l:
        st.markdown(hero, unsafe_allow_html=True)

    with col_r:
        with st.container(border=True):
            st.markdown('<span class="cf-card-mk"></span>', unsafe_allow_html=True)
            st.markdown('<div class="cf-card-head">'
                        '<div class="cf-card-brand"><div class="cf-logo"></div><span>CoatFlow</span></div>'
                        '<div class="cf-card-title">' + title + '</div>'
                        '<div class="cf-card-sub">' + sub + '</div></div>', unsafe_allow_html=True)

            # ── INLOGGEN ──
            if view == "login":
                st.markdown('<span class="cf-mail-mk"></span>', unsafe_allow_html=True)
                email = st.text_input("E-mailadres", key="li_email", placeholder="jouw@email.nl")
                st.markdown('<span class="cf-lock-mk"></span>', unsafe_allow_html=True)
                pw = st.text_input("Wachtwoord", type="password", key="li_pw", placeholder="••••••••")
                st.markdown('<span class="cf-forgot-mk"></span>', unsafe_allow_html=True)
                if st.button("Wachtwoord vergeten?", key="cf_forgot"):
                    st.session_state["cf_auth_view"] = "reset"
                    st.rerun()
                if st.button("Inloggen", key="cf_login_btn", type="primary", use_container_width=True):
                    if not email or not pw:
                        st.error("Vul je e-mailadres en wachtwoord in.")
                    else:
                        try:
                            sign_in(email.strip(), pw)
                            st.rerun()
                        except AuthError as e:
                            st.error(str(e))
                st.markdown('<div class="cf-divider">of</div>', unsafe_allow_html=True)
                st.markdown('<span class="cf-google-mk"></span>', unsafe_allow_html=True)
                if st.button("Inloggen met Google", key="cf_google_login", use_container_width=True):
                    st.info("Google login wordt momenteel gekoppeld…")

            # ── REGISTREREN ──
            elif view == "register":
                st.markdown('<span class="cf-mail-mk"></span>', unsafe_allow_html=True)
                r_email = st.text_input("E-mailadres", key="rg_email", placeholder="jouw@email.nl")
                st.markdown('<span class="cf-lock-mk"></span>', unsafe_allow_html=True)
                r_pw = st.text_input("Wachtwoord", type="password", key="rg_pw", placeholder="Min. 6 tekens")
                st.markdown('<span class="cf-lock-mk"></span>', unsafe_allow_html=True)
                r_pw2 = st.text_input("Herhaal wachtwoord", type="password", key="rg_pw2", placeholder="••••••••")
                if st.button("Account aanmaken", key="cf_reg_btn", type="primary", use_container_width=True):
                    if not r_email or not r_pw:
                        st.error("Vul je e-mailadres en wachtwoord in.")
                    elif r_pw != r_pw2:
                        st.error("De wachtwoorden komen niet overeen.")
                    elif len(r_pw) < 6:
                        st.error("Gebruik een wachtwoord van minimaal 6 tekens.")
                    else:
                        try:
                            if sign_up(r_email.strip(), r_pw):
                                st.rerun()  # e-mailbevestiging UIT → direct ingelogd
                            else:
                                st.success("Gelukt! Check je mailbox om je account te activeren. "
                                           "Pas daarna kun je inloggen.")
                        except AuthError as e:
                            st.error(str(e))
                st.markdown('<div class="cf-divider">of</div>', unsafe_allow_html=True)
                st.markdown('<span class="cf-google-mk"></span>', unsafe_allow_html=True)
                if st.button("Inloggen met Google", key="cf_google_reg", use_container_width=True):
                    st.info("Google login wordt momenteel gekoppeld…")

            # ── WACHTWOORD VERGETEN ──
            else:
                st.markdown('<span class="cf-mail-mk"></span>', unsafe_allow_html=True)
                rs_email = st.text_input("E-mailadres", key="rs_email", placeholder="jouw@email.nl")
                if st.button("Stuur resetlink", key="cf_reset_btn", type="primary", use_container_width=True):
                    if not rs_email:
                        st.error("Vul je e-mailadres in.")
                    else:
                        try:
                            reset_password(rs_email.strip())
                            st.success("Is dit e-mailadres bij ons bekend? Dan staat er zo een "
                                       "link in je mailbox om je wachtwoord te resetten.")
                        except AuthError as e:
                            st.error(str(e))

        # ── wissel-link onder de kaart ──
        if view == "login":
            st.markdown('<span class="cf-switch cf-switch-reg"></span>', unsafe_allow_html=True)
            if st.button("Account aanmaken", key="cf_go_reg"):
                st.session_state["cf_auth_view"] = "register"
                st.rerun()
        elif view == "register":
            st.markdown('<span class="cf-switch cf-switch-login"></span>', unsafe_allow_html=True)
            if st.button("Inloggen", key="cf_go_login"):
                st.session_state["cf_auth_view"] = "login"
                st.rerun()
        else:
            st.markdown('<span class="cf-switch"></span>', unsafe_allow_html=True)
            if st.button("← Terug naar inloggen", key="cf_go_login2"):
                st.session_state["cf_auth_view"] = "login"
                st.rerun()
