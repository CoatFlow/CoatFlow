"""
Test voor auth.py's _ensure_company(): bij een database-fout tijdens het aanmaken
van een nieuw bedrijf (registratie) mag de rauwe technische exceptie NIET
rechtstreeks in de AuthError-boodschap terechtkomen — die wordt namelijk
ongewijzigd op het inlogscherm getoond. Regressietest voor een Klein-audit-fix.

Draait tegen de ECHTE auth._ensure_company() met een nep-'db'-client (geen
netwerk, geen echte Supabase).
"""
import auth


class _FakeQuery:
    """Simuleert alleen wat _ensure_company nodig heeft: select().eq().limit()
    dat een lege rij teruggeeft (geen bestaande koppeling), en insert() dat
    voor 'companies' een technische exceptie gooit."""
    def __init__(self, table):
        self.table = table

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def insert(self, payload):
        if self.table == "companies":
            raise RuntimeError("connection to server at \"10.0.5.12\", port 5432 failed")
        return self

    def execute(self):
        if self.table == "app_users":
            class _Resp:
                data = []
            return _Resp()
        return None


class _FakeClient:
    def table(self, name):
        return _FakeQuery(name)


def test_database_fout_bij_registratie_lekt_geen_rauwe_exceptie(monkeypatch, capsys):
    monkeypatch.setattr(auth.db, "_get_client", lambda: _FakeClient())

    try:
        auth._ensure_company("user-123", "nieuw@bedrijf.nl")
        assert False, "had een AuthError moeten gooien"
    except auth.AuthError as e:
        msg = str(e)
        assert "10.0.5.12" not in msg, (
            "BUG NIET GEFIXT: de rauwe database-exceptie lekt naar de gebruiker"
        )
        assert "5432" not in msg
        assert msg == "Kon het bedrijf niet aan je account koppelen. Probeer het later opnieuw."

    # De technische details moeten wél naar de serverlog gaan (Manage app → Logs),
    # anders is de fout nergens meer te herleiden.
    captured = capsys.readouterr()
    assert "10.0.5.12" in captured.out
    assert "user-123" in captured.out
