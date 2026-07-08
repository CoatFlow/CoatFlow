"""
CoatFlow — eenmalige migratie  data/appdata.json  →  Supabase PostgreSQL.

Draai dit NA supabase_schema.sql en NA het invullen van .streamlit/secrets.toml.

    python migrate_json_to_supabase.py --dry-run     # alleen tonen, niets schrijven
    python migrate_json_to_supabase.py               # echte migratie

Veilig & herhaalbaar:
  * Geen dataverlies: appdata.json blijft ongewijzigd staan als backup.
  * Idempotent: bij een tweede run wordt de doel-company eerst leeggemaakt
    (ON DELETE CASCADE) en opnieuw gevuld — dus geen dubbele records.
  * Verifieert na afloop de record-aantallen DB vs. bron.

De gemigreerde data gaat naar één 'default company'. De company-uuid wordt
geprint → zet die in .streamlit/secrets.toml als default_company_id.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import db  # hergebruikt de veld-mappers (_klant_row etc.) — geen Streamlit nodig

ROOT = Path(__file__).parent
DATA_PATH = ROOT / "data" / "appdata.json"
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"

SENT_STATUSSEN = {"Offerte verzonden", "Geaccepteerd", "In uitvoering", "Afgerond"}


def _load_secrets() -> dict:
    """Lees [supabase] uit .streamlit/secrets.toml (zonder Streamlit-runtime)."""
    if not SECRETS_PATH.exists():
        sys.exit(f"FOUT: {SECRETS_PATH} ontbreekt. Kopieer secrets.toml.example en vul in.")
    try:
        import tomllib  # Python 3.11+
        with open(SECRETS_PATH, "rb") as f:
            cfg = tomllib.load(f)
    except ModuleNotFoundError:
        import tomli  # type: ignore
        with open(SECRETS_PATH, "rb") as f:
            cfg = tomli.load(f)
    sup = cfg.get("supabase", {})
    if not (sup.get("supabase_url") and sup.get("supabase_service_key")):
        sys.exit("FOUT: supabase_url en/of supabase_service_key ontbreken in secrets.toml.")
    return sup


def _client(sup: dict):
    try:
        from supabase import create_client
    except ModuleNotFoundError:
        sys.exit("FOUT: pakket 'supabase' niet geïnstalleerd. Run: pip install -r requirements.txt")
    return create_client(sup["supabase_url"], sup["supabase_service_key"])


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows-console: box-tekens/pijlen tonen
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Alleen tonen wat zou gebeuren.")
    args = ap.parse_args()

    if not DATA_PATH.exists():
        sys.exit(f"FOUT: {DATA_PATH} niet gevonden — niets te migreren.")
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    klanten   = data.get("klanten", [])
    producten = data.get("producten", [])
    personeel = data.get("personeel", [])
    projecten = data.get("projecten", [])
    taken     = data.get("taken", [])
    instellingen = data.get("instellingen", {})
    agenda_taken = data.get("agenda_taken", {})

    # afgeleide records
    offertes = [p for p in projecten if p.get("offerte_nummer")]
    facturen = [p for p in projecten if p.get("factuur_nummer")]
    links    = sum(len(p.get("medewerkers") or []) for p in projecten)
    agenda_n = sum(len(v or []) for v in agenda_taken.values())

    print("── Bron (appdata.json) ─────────────────────────────")
    print(f"  klanten={len(klanten)}  producten={len(producten)}  personeel={len(personeel)}")
    print(f"  projecten={len(projecten)}  taken={len(taken)}  agenda-items={agenda_n}")
    print(f"  → offertes(af te leiden)={len(offertes)}  facturen={len(facturen)}  project↔personeel-links={links}")
    print(f"  instellingen-keys={len(instellingen)}")

    if args.dry_run:
        print("\n[dry-run] Niets geschreven. Verwijder --dry-run voor de echte migratie.")
        return

    sup = _load_secrets()
    cl = _client(sup)
    target_id = sup.get("default_company_id") or None

    # ── company aanmaken / leegmaken (idempotent) ──
    if target_id:
        cl.table("companies").delete().eq("id", target_id).execute()  # cascade ruimt children
        comp = {
            "id": target_id, "naam": instellingen.get("bedrijfsnaam", "Mijn bedrijf"),
            "instellingen": instellingen,
            "volgende_project_id": data.get("volgende_project_id", 1),
            "volgende_klant_id": data.get("volgende_klant_id", 1),
        }
        cl.table("companies").insert(comp).execute()
        company_id = target_id
    else:
        comp = {
            "naam": instellingen.get("bedrijfsnaam", "Mijn bedrijf"),
            "instellingen": instellingen,
            "volgende_project_id": data.get("volgende_project_id", 1),
            "volgende_klant_id": data.get("volgende_klant_id", 1),
        }
        company_id = cl.table("companies").insert(comp).execute().data[0]["id"]

    cid = company_id

    def rows(maker, items):
        return [dict(maker(x), company_id=cid) for x in items]

    # ── kern-entiteiten ──
    if klanten:
        cl.table("klanten").insert(rows(db._klant_row, klanten)).execute()
    if producten:
        cl.table("producten").insert(rows(db._product_row, producten)).execute()
    if personeel:
        cl.table("personeel").insert(rows(db._personeel_row, personeel)).execute()
    if projecten:
        cl.table("projecten").insert(rows(db._project_row, projecten)).execute()
    if taken:
        cl.table("taken").insert(rows(db._taak_row, taken)).execute()

    # ── M:N project↔personeel ──
    link_rows = []
    for p in projecten:
        for mid in (p.get("medewerkers") or []):
            link_rows.append({"company_id": cid, "project_id": p.get("id"), "personeel_id": mid})
    if link_rows:
        cl.table("project_personeel").insert(link_rows).execute()

    # ── agenda_items ──
    ag_rows = []
    for datum, items in agenda_taken.items():
        for it in (items or []):
            ag_rows.append({
                "company_id": cid, "datum": datum, "tijd": it.get("tijd", ""),
                "titel": it.get("titel", "") or it.get("taak", ""),
                "subtitel": it.get("subtitel", ""), "status": it.get("status", ""),
                "payload": it,
            })
    if ag_rows:
        cl.table("agenda_items").insert(ag_rows).execute()

    # ── offertes (first-class, afgeleid van project.offerte_nummer) ──
    off_rows = []
    for p in offertes:
        off_rows.append({
            "company_id": cid, "project_id": p.get("id"), "nummer": p.get("offerte_nummer"),
            "datum": p.get("aangemaakt", ""),
            "status": "verzonden" if p.get("status") in SENT_STATUSSEN else "concept",
            "snapshot": p.get("prijs_snapshot"),
        })
    if off_rows:
        cl.table("offertes").insert(off_rows).execute()

    # ── facturen (first-class, afgeleid van project.factuur_nummer) ──
    fac_rows = []
    for p in facturen:
        snap = p.get("prijs_snapshot") or {}
        bedrag = (snap.get("totaal") or {}).get("incl_btw") if isinstance(snap, dict) else None
        fac_rows.append({
            "company_id": cid, "project_id": p.get("id"), "nummer": p.get("factuur_nummer"),
            "factuurdatum": p.get("factuur_datum", ""), "bedrag": bedrag,
            "status": "verzonden", "snapshot": p.get("prijs_snapshot"),
        })
    if fac_rows:
        cl.table("facturen").insert(fac_rows).execute()

    # ── verificatie ──
    def cnt(table):
        return cl.table(table).select("id", count="exact").eq("company_id", cid).execute().count

    print("\n── Verificatie (database vs. bron) ─────────────────")
    checks = [
        ("klanten", len(klanten)), ("producten", len(producten)),
        ("personeel", len(personeel)), ("projecten", len(projecten)),
        ("taken", len(taken)), ("project_personeel", len(link_rows)),
        ("agenda_items", len(ag_rows)), ("offertes", len(off_rows)),
        ("facturen", len(fac_rows)),
    ]
    ok = True
    for table, bron in checks:
        got = cnt(table)
        flag = "OK " if got == bron else "!! "
        if got != bron:
            ok = False
        print(f"  {flag}{table:<20} db={got}  bron={bron}")

    print("\n────────────────────────────────────────────────────")
    print(f"  Company-id: {cid}")
    print(f"  → Zet dit in .streamlit/secrets.toml als  default_company_id = \"{cid}\"")
    print(f"  Status: {'GESLAAGD — alle aantallen kloppen.' if ok else 'LET OP — aantallen wijken af, controleer hierboven.'}")
    print("  appdata.json is NIET gewijzigd (blijft als backup staan).")


if __name__ == "__main__":
    main()
