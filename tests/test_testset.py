"""Spaltenzuordnung, Zubehoer-Widerspruch (Jev) und Messskript gegen eine Loesungsdatei."""
import json
import sys
from pathlib import Path

import openpyxl

from etim.ingest import map_columns

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import eval_testset  # noqa: E402


def test_map_columns_typical_manufacturer_list():
    # Frueher: Typbezeichnung wurde Name, Kurzbeschreibung Beschreibung, Langtext Rohattribut.
    h = ["Artikelnummer", "GTIN", "Typbezeichnung", "Kurzbeschreibung", "Langbeschreibung", "Zubehör (Artikelnummern)"]
    assert map_columns(h) == {"pid": "Artikelnummer", "gtin": "GTIN", "name": "Kurzbeschreibung", "desc": "Langbeschreibung"}


def test_map_columns_variants():
    assert map_columns(["Art.-Nr.", "EAN", "Bezeichnung", "Langtext"]) == {"pid": "Art.-Nr.", "gtin": "EAN", "name": "Bezeichnung", "desc": "Langtext"}
    assert map_columns(["SUPPLIER_PID", "DESCRIPTION_SHORT", "DESCRIPTION_LONG"]) == {"pid": "SUPPLIER_PID", "name": "DESCRIPTION_SHORT", "desc": "DESCRIPTION_LONG"}
    assert map_columns(["Artikelnummer", "Hersteller", "Typbezeichnung"]).get("name") is None


def test_accessory_conflict():
    from etim import classify

    class M:
        def class_desc(self, cid):
            return {"EC1": "Accessories/spare parts for luminaires", "EC2": "Downlight/spot/floodlight"}.get(cid, "")

    m = M()
    assert classify.accessory_conflict(m, "EC2", 0.9)       # Zubehoer in Hauptproduktklasse
    assert classify.accessory_conflict(m, "EC1", 0.1)       # Hauptprodukt in Zubehoerklasse
    assert not classify.accessory_conflict(m, "EC1", 0.9)
    assert not classify.accessory_conflict(m, "EC2", 0.5)   # unklar -> kein Signal
    assert not classify.accessory_conflict(m, "EC2", None)


def _solution(path: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Artikelnummer", "Kurzbeschreibung", "ETIM-Version", "ETIM-Klasse"])
    ws.append(["A1", "Downlight", "ETIM-9.0", "EC2"])
    ws.append(["A2", "Diffusor", "ETIM-9.0", "EC1"])
    f = wb.create_sheet("Loesung_Merkmale")
    f.append(["Artikelnummer", "ETIM-Klasse", "Merkmal (EF)", "Wert 1", "Wert 2 (Bereich bis)", "Wert-Details", "Einheit"])
    f.append(["A1", "EC2", "EF1", "3000", "", "", ""])
    f.append(["A1", "EC2", "EF2", "EV5", "", "", ""])
    f.append(["A1", "EC2", "EF3", "220", "240", "", ""])
    f.append(["A1", "EC2", "EF4", "-", "", "NA", ""])
    wb.save(path)


def test_eval_testset(tmp_path):
    sol = tmp_path / "sol.xlsx"
    _solution(sol)
    job = tmp_path / "job"
    job.mkdir()
    prod = lambda pid: {"supplier_pid": pid, "name": pid}
    (job / "classified.json").write_text(json.dumps([
        {"product": prod("A1"), "candidates": [{"class_id": "EC2", "description": "", "score": 1}],
         "decision": {"class_id": "EC2", "confidence": 0.9}, "needs_review": False, "model": "jev"},
        {"product": prod("A2"), "candidates": [], "decision": {"class_id": "EC2", "confidence": 0.9},
         "needs_review": True, "model": "jev"},
    ]))
    (job / "enriched.json").write_text(json.dumps([{"product": prod("A1"), "features": [
        {"feature_id": "EF1", "value": "3010"},                    # 0,3 % daneben -> richtig
        {"feature_id": "EF2", "value": "EV6"},                     # falsch
        {"feature_id": "EF3", "value": "220", "value_max": "240"},  # Bereich richtig
        {"feature_id": "EF4", "value": "1"},                       # Soll NA -> falsch gefuellt
    ]}]))
    r = eval_testset.evaluate(job, sol)
    assert r["klasse_treffer"] == 1 and r["artikel"] == 2
    assert r["fehler_von_review_aufgefangen"] == "1/1"
    m = r["merkmale"]
    assert (m["soll"], m["richtig"], m["falsch_gefuellt"]) == (3, 2, 2)
    eval_testset.main([str(job), str(sol), "--write-reference"])
    assert json.loads((job / "reference.json").read_text())["reference"] == {"A1": "EC2", "A2": "EC1"}
    assert (job / "eval.md").exists()
