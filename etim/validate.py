"""catalog.bmecat.xml -> validation.json

Mit XSD (data/schema/**/bmecat_2005*.xsd aus der ETIM-Guideline-ZIP): echte Schemaprüfung.
Ohne XSD: Strukturregeln, die Open Datacheck / DQR typischerweise anmahnen.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from lxml import etree

from . import config
from .export_bmecat import NS


def find_xsd() -> Path | None:
    for p in (config.DATA / "schema").rglob("*.xsd"):
        if "bmecat" in p.name.lower() and "2005" in p.name:
            return p
    for p in (config.DATA / "schema").rglob("*.xsd"):
        if "bmecat" in p.name.lower():
            return p
    return None


def gtin_ok(s: str) -> bool:
    if not re.fullmatch(r"\d{8}|\d{12,14}", s):
        return False
    digits = [int(c) for c in s]
    check = digits.pop()
    total = sum(d * (3 if (len(digits) - i) % 2 == 1 else 1) for i, d in enumerate(digits))
    return (10 - total % 10) % 10 == check


def structural(tree: etree._ElementTree) -> list[dict]:
    issues = []
    seen = set()
    for prod in tree.getroot().iter(f"{{{NS}}}PRODUCT"):
        pid = prod.findtext(f"{{{NS}}}SUPPLIER_PID") or ""
        if not pid:
            issues.append({"level": "error", "pid": pid, "msg": "SUPPLIER_PID fehlt"})
        if pid in seen:
            issues.append({"level": "error", "pid": pid, "msg": "SUPPLIER_PID doppelt"})
        seen.add(pid)
        short = prod.findtext(f".//{{{NS}}}DESCRIPTION_SHORT") or ""
        if not short:
            issues.append({"level": "error", "pid": pid, "msg": "DESCRIPTION_SHORT fehlt"})
        elif len(short) > 80:
            issues.append({"level": "error", "pid": pid, "msg": "DESCRIPTION_SHORT > 80 Zeichen"})
        gtin = prod.findtext(f".//{{{NS}}}INTERNATIONAL_PID")
        if gtin is None:
            issues.append({"level": "warn", "pid": pid, "msg": "keine GTIN (DQR: Pflicht ab Qualitätsstufe 2)"})
        elif not gtin_ok(gtin):
            issues.append({"level": "error", "pid": pid, "msg": f"GTIN ungültig: {gtin}"})
        pf = prod.find(f"{{{NS}}}PRODUCT_FEATURES")
        if pf is None or not (pf.findtext(f"{{{NS}}}REFERENCE_FEATURE_GROUP_ID") or "").startswith("EC"):
            issues.append({"level": "error", "pid": pid, "msg": "keine ETIM-Klasse"})
        else:
            feats = pf.findall(f"{{{NS}}}FEATURE")
            if not feats:
                issues.append({"level": "warn", "pid": pid, "msg": "ETIM-Klasse ohne Merkmale"})
            names = [f.findtext(f"{{{NS}}}FNAME") for f in feats]
            if len(names) != len(set(names)):
                issues.append({"level": "error", "pid": pid, "msg": "Merkmal doppelt"})
    return issues


def run(out_dir: Path) -> dict:
    path = out_dir / "catalog.bmecat.xml"
    tree = etree.parse(str(path))
    result = {"file": str(path), "xsd": None, "xsd_ok": None, "xsd_errors": [], "issues": []}
    xsd = find_xsd()
    if xsd:
        schema = etree.XMLSchema(etree.parse(str(xsd)))
        ok = schema.validate(tree)
        result.update(xsd=str(xsd), xsd_ok=ok, xsd_errors=[str(e) for e in schema.error_log][:50])
    result["issues"] = structural(tree)
    n_err = sum(i["level"] == "error" for i in result["issues"])
    n_warn = len(result["issues"]) - n_err
    (out_dir / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"validate: XSD={'ok' if result['xsd_ok'] else ('FEHLER' if xsd else 'nicht vorhanden')}, {n_err} Fehler, {n_warn} Warnungen → {out_dir / 'validation.json'}")
    return result
