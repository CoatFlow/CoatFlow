"""
Tests voor db.py's opslaglaag: multi-sessie dirty-tracking (_sync_table) mag nooit
een rij verwijderen die een ANDERE, gelijktijdig actieve sessie van hetzelfde bedrijf
zojuist heeft toegevoegd. Regressietest voor een kritieke audit-fix (de dirty-tracking
cache verhuisde van een proces-breed module-dict naar st.session_state).

Draait tegen een NEP Supabase-client (in-memory dict) en een nep 'streamlit'-module
met wisselbare session_state — geen netwerk, geen echte database, geen echte data.
"""
import sys
import types

import pytest


@pytest.fixture
def fake_streamlit(monkeypatch):
    """Injecteert een nep 'streamlit'-module met een los, per-test session_state.
    db.py doet 'import streamlit as st' lokaal in elke functie (nooit op module-
    niveau), dus dit wordt bij elke aanroep opnieuw opgepikt."""
    fake = types.ModuleType("streamlit")
    fake.session_state = {}
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    return fake


@pytest.fixture
def db(fake_streamlit):
    """Herimporteer db.py vers per test, ná het inspuiten van de nep-streamlit-
    module, zodat geen state lekt tussen tests (module-level caches als
    _ontbrekende_kolommen zijn nog steeds proces-breed voor wat er WEL bewust
    proces-breed hoort te blijven)."""
    sys.modules.pop("db", None)
    import db as _db
    return _db


class FakeResp:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Minimale simulatie van de supabase-py fluent query-builder, genoeg voor
    company/klanten-achtige upsert/select/delete zoals db.py ze aanroept."""
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self.op = None
        self.payload = None
        self._eq = {}
        self._in = None
        self._negate = False

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def order(self, *a, **k):
        return self

    def single(self):
        return self

    def not_(self):
        self._negate = True
        return self

    def in_(self, col, vals):
        self._in = (col, set(vals))
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self.op = "upsert"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        if self.table == "companies":
            row = self.store["companies"][self._eq.get("id")]
            if self.op == "update":
                row.update(self.payload)
            return FakeResp(dict(row))

        rows = self.store.setdefault(self.table, [])
        if self.op == "upsert":
            for r in self.payload:
                bestaand = next((x for x in rows if x.get("id") == r.get("id")
                                 and x.get("company_id") == r.get("company_id")), None)
                if bestaand:
                    bestaand.update(r)
                else:
                    rows.append(dict(r))
            return FakeResp(self.payload)
        if self.op == "delete":
            def moet_weg(r):
                if r.get("company_id") != self._eq.get("company_id"):
                    return False
                if self._in:
                    col, vals = self._in
                    inset = r.get(col) in vals
                    return (not inset) if self._negate else inset
                return True
            self.store[self.table] = [r for r in rows if not moet_weg(r)]
            return FakeResp(None)
        return FakeResp([r for r in rows if all(r.get(c) == v for c, v in self._eq.items())])


class FakeClient:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)


CID = "test-bedrijf"


def _fake_store(klanten=None):
    return {
        "companies": {CID: {"id": CID, "instellingen": {}, "volgende_project_id": 1,
                            "volgende_klant_id": 1}},
        "klanten": klanten or [], "producten": [], "personeel": [], "projecten": [],
        "taken": [], "agenda_items": [], "project_personeel": [],
    }


def test_toegevoegde_rij_door_sessie_a_overleeft_ongerelateerde_save_door_sessie_b(db, fake_streamlit):
    """Het scenario uit de audit: sessie A voegt een klant toe en slaat op; sessie B,
    nog met haar eigen oudere snapshot, slaat daarna iets ongerelateerds op. Klant #7
    (door A toegevoegd, B kent 'm niet) mag NIET verdwijnen."""
    store = _fake_store(klanten=[{"id": 1, "naam": "Bestaande klant", "company_id": CID}])
    cl = FakeClient(store)

    sessie_a, sessie_b = {}, {}

    fake_streamlit.session_state = sessie_a
    data_a = db._load_impl(cl, CID)
    fake_streamlit.session_state = sessie_b
    data_b = db._load_impl(cl, CID)

    assert [k["id"] for k in data_a["klanten"]] == [1]
    assert [k["id"] for k in data_b["klanten"]] == [1]

    # Sessie A voegt klant #7 toe en slaat op.
    fake_streamlit.session_state = sessie_a
    state_a = dict(data_a)
    state_a["klanten"] = data_a["klanten"] + [{"id": 7, "naam": "Nieuwe klant (A)"}]
    db._save_impl(cl, CID, state_a)
    assert sorted(r["id"] for r in store["klanten"]) == [1, 7]

    # Sessie B (kent #7 niet) bewerkt klant #1 en slaat op.
    fake_streamlit.session_state = sessie_b
    state_b = dict(data_b)
    state_b["klanten"] = [dict(data_b["klanten"][0], naam="Bewerkt door B")]
    db._save_impl(cl, CID, state_b)

    ids_na_b = sorted(r["id"] for r in store["klanten"])
    assert 7 in ids_na_b, "BUG: klant #7 (door sessie A toegevoegd) is verdwenen na sessie B's save"
    assert ids_na_b == [1, 7]

    klant1 = next(r for r in store["klanten"] if r["id"] == 1)
    assert klant1["naam"] == "Bewerkt door B", "sessie B's eigen wijziging moet wel opgeslagen zijn"


def test_eigen_verwijdering_werkt_nog_gewoon(db, fake_streamlit):
    """Regressiecheck: een sessie die zelf een rij verwijdert, moet die nog gewoon
    kunnen verwijderen (de fix mag verwijderen niet blokkeren, alleen het
    cross-sessie-abuscenario)."""
    store = _fake_store(klanten=[
        {"id": 1, "naam": "Blijft", "company_id": CID},
        {"id": 2, "naam": "Wordt verwijderd", "company_id": CID},
    ])
    cl = FakeClient(store)
    fake_streamlit.session_state = {}

    data = db._load_impl(cl, CID)
    assert sorted(k["id"] for k in data["klanten"]) == [1, 2]

    state = dict(data)
    state["klanten"] = [k for k in data["klanten"] if k["id"] != 2]
    db._save_impl(cl, CID, state)

    assert [r["id"] for r in store["klanten"]] == [1]


def test_geen_company_id_weigert_opslaan(db, fake_streamlit):
    fake_streamlit.session_state = {}
    with pytest.raises(db.DbError):
        db.save_company_data(None, {})
