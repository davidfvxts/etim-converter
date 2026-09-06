#!/usr/bin/env python3
"""Erzeugt web/assets/demo-data.js — die Beispieldaten der Oberflaeche.

Die Oberflaeche laedt normalerweise einen echten Job ueber `python -m etim ui`.
Wird index.html direkt geoeffnet (oder als Vorschau geteilt), faellt sie auf
diese Daten zurueck, damit das Cockpit nicht leer startet.

Klassen-, Merkmals- und Wertecodes stammen aus tests/fixtures/etim_mini/ —
das ist die handgebaute Test-Fixture, NICHT der echte ETIM-10.0-Release.
Sobald der echte Release vorliegt, sind die Codes hier bedeutungslos; die
Oberflaeche weist die Daten ausdruecklich als Beispieldaten aus.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "etim_mini"
OUT = ROOT / "web" / "assets" / "demo-data.js"


def read(name: str) -> list[dict]:
    with (FIX / f"{name}.csv").open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


classes = {r["ARTCLASSID"]: r["ARTCLASSDESC_EN"] for r in read("ETIMARTCLASS")}
featdesc = {r["FEATUREID"]: r["FEATUREDESC_EN"] for r in read("ETIMFEATURE")}
units = {r["UNITOFMEASID"]: r["UNITDESC_EN"] for r in read("ETIMUNIT")}
values = {r["VALUEID"]: r["VALUEDESC_EN"] for r in read("ETIMVALUE")}

fmap: dict[str, list[dict]] = {}
nr2feat: dict[str, dict] = {}
for r in sorted(read("ETIMARTCLASSFEATUREMAP"), key=lambda r: int(r["SORTNR"])):
    entry = {
        "feature_id": r["FEATUREID"],
        "type": r["FEATURETYPE"],
        "unit_id": r["UNITOFMEASID"] or None,
        "unit_desc": units.get(r["UNITOFMEASID"], "") if r["UNITOFMEASID"] else "",
        "values": [],
    }
    fmap.setdefault(r["ARTCLASSID"], []).append(entry)
    nr2feat[r["ARTCLASSFEATURENR"]] = entry
for r in read("ETIMARTCLASSFEATUREVALUEMAP"):
    nr2feat[r["ARTCLASSFEATURENR"]]["values"].append(r["VALUEID"])

# (pid, name, beschreibung, gtin, seite, klasse, konfidenz, zweitbeste, begruendung,
#  rohattribute, merkmalswerte {feature_id: (value, value_max, source, confidence, reason)})
ARTICLES = [
    ("RS-2025-M8", "Rohrschelle 20-25 mm M8/M10",
     "Stahlrohrschelle mit Schallschutzeinlage, verzinkt, nach DIN 4109",
     "4012345678901", 12, "EC000001", 0.96, "EC000006",
     "Eindeutig eine Rohrschelle mit Spannbereich und Anschlussgewinde; das Zubehörteil EC000006 hat kein eigenes Gewindemaß.",
     [("Material", "Stahl verzinkt"), ("Spannbereich", "20-25 mm"), ("Gewinde", "M8/M10"), ("Gummieinlage", "ja")],
     {"EF000001": ("EV000001", None, "Material: Stahl verzinkt", 0.95, None),
      "EF000005": ("20", "25", "Spannbereich: 20-25 mm", 0.97, None),
      "EF000003": ("EV000011", None, "Gewinde: M8/M10", 0.95, None),
      "EF000004": ("true", None, "Gummieinlage: ja", 0.92, None)}),

    ("RS-2632-M8", "Rohrschelle 26-32 mm M8/M10",
     "Stahlrohrschelle mit Schallschutzeinlage, verzinkt, nach DIN 4109",
     "4012345678918", 12, "EC000001", 0.96, "EC000006",
     "Baugleich zu RS-2025-M8, abweichender Spannbereich.",
     [("Material", "Stahl verzinkt"), ("Spannbereich", "26-32 mm"), ("Gewinde", "M8/M10"), ("Gummieinlage", "ja")],
     {"EF000001": ("EV000001", None, "Material: Stahl verzinkt", 0.95, None),
      "EF000005": ("26", "32", "Spannbereich: 26-32 mm", 0.97, None),
      "EF000003": ("EV000011", None, "Gewinde: M8/M10", 0.95, None),
      "EF000004": ("true", None, "Gummieinlage: ja", 0.92, None)}),

    ("RS-INOX-2833", "Rohrschelle Edelstahl 28-33 mm M10",
     "Rohrschelle A2, ohne Einlage, für Trinkwasserleitungen",
     "4012345679007", 13, "EC000001", 0.94, "EC000006",
     "Edelstahlausfuehrung derselben Bauform; Werkstoff aus der Bezeichnung belegt.",
     [("Material", "Edelstahl A2"), ("Spannbereich", "28-33 mm"), ("Gewinde", "M10")],
     {"EF000001": ("EV000002", None, "Material: Edelstahl A2", 0.93, None),
      "EF000005": ("28", "33", "Spannbereich: 28-33 mm", 0.96, None),
      "EF000003": ("EV000012", None, "Gewinde: M10", 0.94, None),
      "EF000004": (None, None, None, 0.0, "im Katalog nicht angegeben")}),

    ("GS-M10-1000", "Gewindestange M10 x 1000 mm",
     "Gewindestange verzinkt, Festigkeitsklasse 4.8", "4012345679014", 14,
     "EC000002", 0.93, "EC000001",
     "Gewindestange, kein Schellenkörper — kein Spannbereich im Katalog.",
     [("Material", "Stahl verzinkt"), ("Gewinde", "M10"), ("Länge", "1000 mm")],
     {"EF000001": ("EV000001", None, "Material: Stahl verzinkt", 0.94, None),
      "EF000003": ("EV000012", None, "Gewinde: M10", 0.96, None)}),

    ("GS-M8-2000", "Gewindestange M8 x 2000 mm",
     "Gewindestange verzinkt, Festigkeitsklasse 4.8", "4012345679021", 14,
     "EC000002", 0.93, "EC000001", "Wie GS-M10-1000, abweichendes Gewindemaß.",
     [("Material", "Stahl verzinkt"), ("Gewinde", "M8"), ("Länge", "2000 mm")],
     {"EF000001": ("EV000001", None, "Material: Stahl verzinkt", 0.94, None),
      "EF000003": ("EV000010", None, "Gewinde: M8", 0.96, None)}),

    ("KH-20", "Kugelhahn DN 20 IG/IG",
     "Messing-Kugelhahn mit Hebelgriff, PN 25, beidseitig Innengewinde",
     "4012345678925", 21, "EC000003", 0.95, "EC000004",
     "Kugelhahn mit Hebelgriff, kein Absperrventil mit Spindel.",
     [("Material", "Messing"), ("Nennweite", "DN 20"), ("Druckstufe", "PN 25")],
     {"EF000001": (None, None, None, 0.0, "Messing ist in der Werteliste dieser Klasse nicht enthalten"),
      "EF000002": ("20", None, "Kugelhahn DN 20", 0.94, None)}),

    ("KH-25", "Kugelhahn DN 25 IG/IG",
     "Messing-Kugelhahn mit Hebelgriff, PN 25", "4012345678932", 21,
     "EC000003", 0.95, "EC000004", "Wie KH-20, größere Nennweite.",
     [("Material", "Messing"), ("Nennweite", "DN 25"), ("Druckstufe", "PN 25")],
     {"EF000001": (None, None, None, 0.0, "Messing ist in der Werteliste dieser Klasse nicht enthalten"),
      "EF000002": ("25", None, "Kugelhahn DN 25", 0.94, None)}),

    ("KH-32-AG", "Kugelhahn DN 32 AG/IG mit Entleerung",
     "Messing-Kugelhahn, Außen-/Innengewinde, mit Entleerungsstopfen",
     "4012345678949", 22, "EC000003", 0.71, "EC000004",
     "Entleerungsfunktion und AG/IG-Kombination sprechen auch für ein Absperrventil; die Modelle waren uneinig.",
     [("Material", "Messing"), ("Nennweite", "DN 32"), ("Anschluss", "AG/IG")],
     {"EF000001": (None, None, None, 0.0, "Messing ist in der Werteliste dieser Klasse nicht enthalten"),
      "EF000002": ("32", None, "Kugelhahn DN 32", 0.88, None)}),

    ("AV-15", "Absperrventil DN 15 mit Rückflussverhinderer",
     "Rotguss-Absperrventil, Spindel steigend, PN 16", "4012345679038", 23,
     "EC000004", 0.88, "EC000003",
     "Steigende Spindel und Rückflussverhinderer schließen einen Kugelhahn aus.",
     [("Material", "Rotguss"), ("Nennweite", "DN 15"), ("Druckstufe", "PN 16")],
     {}),

    ("LED-P-3000", "LED-Panel 620x620 3000 lm 4000 K",
     "Einlege-LED-Panel für Rasterdecken, UGR < 19, weiß", "4012345679045", 41,
     "EC000005", 0.97, None, "Eindeutig eine LED-Leuchte mit Lichtstromangabe.",
     [("Lichtstrom", "3000 lm"), ("Farbtemperatur", "4000 K"), ("Gehäusefarbe", "weiß")],
     {"EF000006": ("3000", None, "Lichtstrom: 3000 lm", 0.96, None),
      "EF000007": ("EV000020", None, "Gehäusefarbe: weiß", 0.93, None)}),

    ("LED-FL-1200", "LED-Feuchtraumleuchte 1200 mm IP65",
     "Feuchtraumwannenleuchte, IP65, für Tiefgaragen und Lager", None, 42,
     "EC000005", 0.91, None,
     "LED-Leuchte; der Lichtstrom steht im Katalog nur in einer Fußnote als Bereich.",
     [("Schutzart", "IP65"), ("Länge", "1200 mm")],
     {"EF000006": (None, None, None, 0.0, "Lichtstrom im Katalog nur als Bereich '3600-4400 lm' angegeben"),
      "EF000007": (None, None, None, 0.0, "Gehäusefarbe nicht angegeben")}),

    ("GE-RS-20", "Gummieinlage für Rohrschelle 20-25",
     "Ersatz-Schallschutzeinlage EPDM", None, 13, "EC000006", 0.90, "EC000001",
     "Ersatzteil zur Schelle, nicht die Schelle selbst — deshalb die Zubehörklasse.",
     [("Material", "EPDM"), ("Spannbereich", "20-25 mm")],
     {"EF000001": (None, None, None, 0.0, "EPDM ist in der Werteliste dieser Klasse nicht enthalten")}),

    ("ZAS-3P", "Zähleranschlusssäule 3-Punkt mit Hausanschlusskasten",
     "Freistehende Anschlusssäule für Baustromversorgung, 3 Zählerplätze",
     "4012345679052", 55, None, 0.34, None,
     "Keine der 20 abgerufenen Kandidatenklassen passt; im geladenen Modell fehlt eine Klasse für Zähleranschlusssäulen.",
     [("Zählerplätze", "3"), ("Schutzart", "IP44"), ("Material", "GFK")],
     {}),
]


def build():
    products, classified, enriched = [], [], []
    for (pid, name, desc, gtin, page, cid, conf, runner, why, attrs, fills) in ARTICLES:
        product = {
            "supplier_pid": pid, "name": name, "description": desc, "gtin": gtin,
            "attributes": [{"name": k, "value": v} for k, v in attrs],
            "page": page,
            "source_quote": f"{pid} {name} " + " ".join(f"{k} {v}" for k, v in attrs),
        }
        products.append(product)

        if cid:
            cands = [{"class_id": cid, "description": classes[cid], "score": conf}]
            if runner:
                cands.append({"class_id": runner, "description": classes[runner], "score": round(conf - 0.11, 2)})
        else:
            cands = [{"class_id": k, "description": v, "score": round(0.34 - i * 0.03, 2)}
                     for i, (k, v) in enumerate(list(classes.items())[:3])]
        classified.append({
            "product": product, "candidates": cands,
            "decision": {"class_id": cid, "confidence": conf, "reasoning": why, "runner_up": runner},
            "needs_review": cid is None or conf < 0.75,
        })

        feats, meta = [], {}
        for spec in (fmap.get(cid, []) if cid else []):
            fid = spec["feature_id"]
            v, vmax, src, c2, reason = fills.get(fid, (None, None, None, 0.0, "vom Modell nicht beantwortet"))
            feats.append({"feature_id": fid, "value": v, "value_max": vmax,
                          "source": src, "confidence": c2, "reason": reason})
            meta[fid] = {"desc": featdesc[fid], "type": spec["type"], "unit_id": spec["unit_id"],
                         "unit_desc": spec["unit_desc"], "n_values": len(spec["values"])}
        n_filled = sum(1 for f in feats if f["value"] is not None)
        coverage = n_filled / len(feats) if feats else 0.0
        low = any(f["value"] is not None and f["confidence"] < 0.75 for f in feats)
        thin = bool(feats) and coverage < 0.30
        enriched.append({
            "product": product, "class_id": cid or "", "class_desc": classes.get(cid, "") if cid else "",
            "class_confidence": conf, "features": feats, "feature_meta": meta,
            "coverage": round(coverage, 4),
            "needs_review": cid is None or conf < 0.75 or low or thin,
        })

    issues = []
    for e in enriched:
        if e["needs_review"]:
            continue
        if not e["product"]["gtin"]:
            issues.append({"level": "warn", "pid": e["product"]["supplier_pid"],
                           "msg": "keine GTIN (DQR: Pflicht ab Qualitätsstufe 2)"})
        if not e["features"]:
            issues.append({"level": "warn", "pid": e["product"]["supplier_pid"],
                           "msg": "ETIM-Klasse ohne Merkmale"})

    exported = sum(1 for e in enriched if not e["needs_review"])
    return {
        "job": "beispiel-shk",
        "config": {"review_threshold": 0.75, "min_coverage": 0.30, "etim_version": "ETIM-10.0"},
        "products": products, "classified": classified, "enriched": enriched,
        "labels": dict(values), "decisions": {},
        "validation": {"file": "out/beispiel-shk/catalog.bmecat.xml", "xsd": None,
                       "xsd_ok": None, "xsd_errors": [], "issues": issues},
        "files": [
            {"name": "catalog.bmecat.xml", "desc": f"BMEcat 2005 · {exported} Artikel", "size": "48 KB"},
            {"name": "enriched.json", "desc": "Merkmale mit Quellzitat und Konfidenz", "size": "126 KB"},
            {"name": "report.md", "desc": "Kundenreport: Vollstaendigkeit und Rueckfragen", "size": "7 KB"},
            {"name": "review.csv", "desc": "Arbeitsliste der Prüfpunkte", "size": "3 KB"},
        ],
    }


if __name__ == "__main__":
    data = build()
    OUT.write_text(
        "/* Automatisch erzeugt von scripts/make_demo_data.py — nicht von Hand aendern.\n"
        "   Beispieldaten fuer die Oberflaeche, wenn kein Job geladen ist. Die ETIM-Codes\n"
        "   stammen aus der Test-Fixture, nicht aus dem echten ETIM-10.0-Release. */\n"
        "window.ETIM_DEMO = " + json.dumps(data, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8")
    n_rev = sum(1 for e in data["enriched"] if e["needs_review"])
    print(f"{OUT.relative_to(ROOT)}: {len(data['enriched'])} Artikel, {n_rev} zur Pruefung")
