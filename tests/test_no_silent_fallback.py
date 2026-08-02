"""
Tests voor load_data()/save_data() in SchilderTool1.py: bij een DB-fout in
Supabase-modus mag er NOOIT stil worden teruggevallen op het gedeelde, niet-per-
bedrijf-gescheiden lokale bestand (data/appdata.json). Regressietest voor een
kritieke audit-fix.

Draait tegen de ECHTE functies (AST-extractie) met een nep-'_db'-module — geen
Streamlit-runtime, geen echte database, geen echte bestanden buiten een tijdelijke
map voor het puur-lokale-JSON-scenario.
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from _schildertool_extract import extract, FakeSt

NAMES = ("load_data", "save_data", "_load_data_json", "_save_data_json",
        "_use_db", "DATA_PATH", "PERSISTENT_KEYS", "_LEGE_PERSISTENTE_STAAT")


class BoomException(Exception):
    """Signaal: de lokale-JSON-terugval is aangeroepen terwijl dat in Supabase-modus
    nooit mag gebeuren."""


def _namespace(db_ok, db_stub):
    ns = extract(*NAMES, extra_globals={
        "st": FakeSt(), "_DB_OK": db_ok, "_db": db_stub,
        "json": __import__("json"), "Path": Path,
        "__file__": str(Path(__file__).resolve().parent.parent / "SchilderTool1.py"),
    })

    def boom_load():
        raise BoomException("_load_data_json werd aangeroepen in DB-modus!")

    def boom_save():
        raise BoomException("_save_data_json werd aangeroepen in DB-modus!")

    ns["_load_data_json_echt"] = ns["_load_data_json"]
    ns["_save_data_json_echt"] = ns["_save_data_json"]
    ns["_load_data_json"] = boom_load
    ns["_save_data_json"] = boom_save
    return ns


class _DbFailLoad:
    def is_enabled(self):
        return True

    def load_company_data(self, cid):
        raise RuntimeError("timeout / netwerkhapering")


class _DbNooitAanroepen:
    def is_enabled(self):
        return True

    def save_company_data(self, cid, state):
        raise AssertionError("had nooit aangeroepen mogen worden zonder company_id")


class _DbFailSave:
    def is_enabled(self):
        return True

    def save_company_data(self, cid, state):
        raise RuntimeError("Supabase tijdelijk onbereikbaar")


class _DbOk:
    def is_enabled(self):
        return True

    def load_company_data(self, cid):
        return {
            "klanten": [{"id": 1, "naam": "Echte klant"}], "projecten": [], "personeel": [],
            "producten": [], "taken": [], "instellingen": {"x": 1},
            "volgende_project_id": 5, "volgende_klant_id": 5, "agenda_taken": {},
        }

    def save_company_data(self, cid, state):
        self.laatst_opgeslagen = state


def test_load_data_bij_netwerkfout_geen_terugval_op_lokaal_bestand():
    ns = _namespace(True, _DbFailLoad())
    ns["st"].session_state["company_id"] = "bedrijf-x"
    ns["load_data"]()   # zou BoomException gooien als _load_data_json werd aangeroepen
    ss = ns["st"].session_state
    assert "_db_fout" in ss
    assert ss["klanten"] == []
    assert ss["projecten"] == []
    assert ss["instellingen"] == {}


def test_load_data_zonder_company_id_geen_terugval_op_lokaal_bestand():
    ns = _namespace(True, _DbFailLoad())
    ns["load_data"]()   # geen company_id gezet
    ss = ns["st"].session_state
    assert "_db_fout" in ss
    assert ss["klanten"] == []


def test_save_data_zonder_company_id_geen_terugval_op_lokaal_bestand():
    ns = _namespace(True, _DbNooitAanroepen())
    ns["save_data"]()
    assert "_db_fout" in ns["st"].session_state


def test_save_data_bij_dbfout_geen_terugval_op_lokaal_bestand():
    ns = _namespace(True, _DbFailSave())
    ns["st"].session_state["company_id"] = "bedrijf-x"
    ns["st"].session_state["klanten"] = [{"id": 1, "naam": "test"}]
    ns["save_data"]()
    assert "_db_fout" in ns["st"].session_state


def test_gelukte_load_en_save_werken_nog_gewoon():
    """Sanity-check: de fix mag de happy path niet breken."""
    db_ok = _DbOk()
    ns = _namespace(True, db_ok)
    ns["st"].session_state["company_id"] = "bedrijf-x"
    ns["load_data"]()
    ss = ns["st"].session_state
    assert ss["klanten"] == [{"id": 1, "naam": "Echte klant"}]
    assert "_db_fout" not in ss
    ns["save_data"]()
    assert db_ok.laatst_opgeslagen["klanten"] == ss["klanten"]


def test_pure_json_modus_zonder_supabase_blijft_werken():
    """Sanity-check: zonder Supabase geconfigureerd (_DB_OK=False) moet de lokale
    JSON-terugval nog gewoon werken -- dit is de bestaande, legitieme dev-modus."""
    ns = _namespace(False, None)
    ns["_load_data_json"] = ns["_load_data_json_echt"]
    ns["_save_data_json"] = ns["_save_data_json_echt"]

    tmpdir = tempfile.mkdtemp()
    try:
        ns["DATA_PATH"] = Path(tmpdir) / "appdata_test.json"
        ss = ns["st"].session_state
        for key, leeg in ns["_LEGE_PERSISTENTE_STAAT"].items():
            ss[key] = leeg
        ss["klanten"] = [{"id": 1, "naam": "Lokale test"}]

        ns["save_data"]()
        assert ns["DATA_PATH"].exists()

        ss.clear()
        ns["load_data"]()
        assert ss["klanten"] == [{"id": 1, "naam": "Lokale test"}]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
