"""enriched.json (+ validation.json) -> report.md + review.csv

report.md ist das Dokument, das der Kunde bekommt: Was ist drin, was fehlt, was muss er nachliefern.
review.csv ist Davids Arbeitsliste (eine Zeile je Artikel/Merkmal unter Schwelle).
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from . import config, versions
from .schemas import EnrichedProduct


def _thin(e: EnrichedProduct) -> bool:
    """Artikel mit zu geringer Merkmalsabdeckung (gleiche Regel wie in features.py)."""
    return bool(e.class_id) and bool(e.features) and config.MIN_COVERAGE > 0 and e.coverage < config.MIN_COVERAGE


def run(out_dir: Path, supplier_name: str = "") -> Path:
    items = [EnrichedProduct.model_validate(x) for x in json.loads((out_dir / "enriched.json").read_text())]
    val = {}
    if (out_dir / "validation.json").exists():
        val = json.loads((out_dir / "validation.json").read_text())

    n = len(items)
    classified = [e for e in items if e.class_id]
    review = [e for e in items if e.needs_review]
    total_feats = sum(len(e.features) for e in classified)
    filled = sum(1 for e in classified for f in e.features if f.value is not None)
    high = sum(1 for e in classified for f in e.features if f.value is not None and f.confidence >= config.REVIEW_THRESHOLD)
    class_counts = Counter((e.class_id, e.class_desc) for e in classified)
    missing = Counter()
    for e in classified:
        for f in e.features:
            if f.value is None:
                missing[(f.feature_id, e.feature_meta.get(f.feature_id, {}).get("desc", ""))] += 1

    lines = [
        f"# Produktdaten-Report{': ' + supplier_name if supplier_name else ''}",
        "",
        f"Job `{out_dir.name}` · {versions.label(next((e.etim_version for e in items if e.etim_version), None))}"
        f" · Schwelle für Freigabe {config.REVIEW_THRESHOLD:.0%}",
        "",
        "## Zusammenfassung",
        "",
        f"- Artikel erkannt: **{n}**",
        f"- Mit ETIM-Klasse: **{len(classified)}** ({len(classified) / n:.0%})" if n else "- keine Artikel",
        f"- Davon ohne Rückfrage freigabefähig: **{n - len(review)}**; zur Prüfung: **{len(review)}**",
        f"- Merkmale: {filled} von {total_feats} befüllt ({filled / total_feats:.0%}), davon {high} mit hoher Sicherheit" if total_feats else "- keine Merkmale",
        f"- Artikel unter der Mindestabdeckung von {config.MIN_COVERAGE:.0%}: **{sum(1 for e in classified if _thin(e))}**" if config.MIN_COVERAGE > 0 else "- Mindestabdeckung abgeschaltet",
    ]
    if val:
        n_err = sum(i["level"] == "error" for i in val.get("issues", []))
        lines.append(f"- BMEcat-Prüfung: XSD {'bestanden' if val.get('xsd_ok') else ('nicht bestanden' if val.get('xsd') else 'nicht geprüft (XSD fehlt)')}, {n_err} strukturelle Fehler")
    lines += ["", "## Klassen", "", "| ETIM-Klasse | Beschreibung | Artikel |", "|---|---|---|"]
    for (cid, cdesc), c in class_counts.most_common():
        lines.append(f"| {cid} | {cdesc} | {c} |")
    lines += ["", "## Häufig fehlende Merkmale (der Hersteller sollte diese nachliefern)", "", "| Merkmal | Beschreibung | fehlt bei Artikeln |", "|---|---|---|"]
    for (fid, fdesc), c in missing.most_common(15):
        lines.append(f"| {fid} | {fdesc} | {c} |")
    lines += ["", "## Artikel zur Prüfung", "", "| Artikel-Nr. | Bezeichnung | Klasse | Konfidenz | Grund |", "|---|---|---|---|---|"]
    for e in review[:200]:
        if not e.class_id:
            reason = "keine Klasse"
        elif e.class_confidence < config.REVIEW_THRESHOLD:
            reason = "Klasse unsicher"
        elif _thin(e):
            reason = f"nur {e.coverage:.0%} der Merkmale befüllt"
        else:
            reason = "Merkmale unsicher"
        lines.append(f"| {e.product.supplier_pid} | {e.product.name[:50]} | {e.class_id or '—'} | {e.class_confidence:.0%} | {reason} |")
    if len(review) > 200:
        lines.append(f"| … | +{len(review) - 200} weitere | | | |")

    path = out_dir / "report.md"
    path.write_text("\n".join(lines) + "\n")

    with (out_dir / "review.csv").open("w", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["supplier_pid", "name", "page", "class_id", "class_desc", "class_conf", "feature_id", "feature_desc", "value", "confidence", "source", "reason", "decision(ok/fix)", "corrected_value"])
        for e in items:
            if not e.class_id or e.class_confidence < config.REVIEW_THRESHOLD:
                w.writerow([e.product.supplier_pid, e.product.name, e.product.page, e.class_id, e.class_desc, f"{e.class_confidence:.2f}", "", "KLASSE", "", "", "", "", "", ""])
            if _thin(e):
                w.writerow([e.product.supplier_pid, e.product.name, e.product.page, e.class_id, e.class_desc, f"{e.class_confidence:.2f}", "", "ABDECKUNG", "", f"{e.coverage:.2f}", "", f"nur {e.coverage:.0%} der Merkmale befüllt (Mindestabdeckung {config.MIN_COVERAGE:.0%})", "", ""])
            for f in e.features:
                if f.value is not None and f.confidence < config.REVIEW_THRESHOLD:
                    m = e.feature_meta.get(f.feature_id, {})
                    w.writerow([e.product.supplier_pid, e.product.name, e.product.page, e.class_id, e.class_desc, f"{e.class_confidence:.2f}", f.feature_id, m.get("desc", ""), f.value, f"{f.confidence:.2f}", f.source or "", f.reason or "", "", ""])
    print(f"report: → {path} und {out_dir / 'review.csv'}")
    return path
