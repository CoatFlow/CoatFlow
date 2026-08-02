"""
Tests voor db.py's _anon_client(): de JWT/RLS-datapad-client mocht nooit een
proces-brede singleton zijn. _jwt_data_client() muteert de client met
cl.postgrest.auth(tok) — het JWT van de HUIDIGE gebruiker. Streamlit bedient
meerdere sessies als threads in hetzelfde proces; met een gedeelde client kon
sessie A's query (bij interleaving) met sessie B's token lopen, en dus B's
RLS-rechten/company_id krijgen — een cross-tenant-lekrisico. Regressietest voor
een Belangrijk-audit-fix: de client is nu per-sessie gecachet (st.session_state),
zelfde aanpak als de eerdere cross-sessie-databug in _sess_cache().

Draait tegen de ECHTE _anon_client() met een nep 'streamlit'-module (per-sessie
wisselbare session_state) en env-var-secrets — geen netwerk nodig (create_client
zet alleen een client-object op, geen round-trip).
"""
import sys
import types

import pytest


@pytest.fixture
def fake_streamlit(monkeypatch):
    fake = types.ModuleType("streamlit")
    fake.session_state = {}
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    return fake


@pytest.fixture
def db(fake_streamlit, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake-project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "fake-anon-key")
    sys.modules.pop("db", None)
    import db as _db
    return _db


def test_twee_sessies_krijgen_elk_hun_eigen_anon_client(db, fake_streamlit):
    sessie_a, sessie_b = {}, {}

    fake_streamlit.session_state = sessie_a
    cl_a = db._anon_client()

    fake_streamlit.session_state = sessie_b
    cl_b = db._anon_client()

    assert cl_a is not None
    assert cl_b is not None
    assert cl_a is not cl_b, (
        "BUG NIET GEFIXT: twee sessies delen dezelfde anon-client-instantie — "
        "een JWT gezet door de ene sessie (cl.postgrest.auth(tok)) kan dan de "
        "query van de andere sessie raken"
    )


def test_zelfde_sessie_hergebruikt_dezelfde_client(db, fake_streamlit):
    """Performance-regressie: binnen ÉÉN sessie moet de client-handshake niet bij
    elke aanroep opnieuw gebeuren — dat was de reden dat dit ooit een singleton
    werd (zie de historische comment in db.py)."""
    fake_streamlit.session_state = {}

    cl_1 = db._anon_client()
    cl_2 = db._anon_client()

    assert cl_1 is cl_2
