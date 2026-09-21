"""python -m etim <befehl>"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="etim", description="Herstellerkatalog -> ETIM -> BMEcat")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("inspect", help="ETIM-Ordner: Dateien und Spalten anzeigen")
    s.add_argument("folder", type=Path)

    s = sub.add_parser("load-model", help="ETIM-CSV nach SQLite laden und Klassen-Embeddings bauen")
    s.add_argument("folder", type=Path)
    s.add_argument("--no-embed", action="store_true")

    for name, help_ in [
        ("ingest", "Katalog -> products.json"),
        ("classify", "products.json -> classified.json"),
        ("features", "classified.json -> enriched.json"),
        ("export", "enriched.json -> catalog.bmecat.xml"),
        ("validate", "BMEcat prüfen"),
        ("report", "report.md + review.csv"),
        ("compare", "Gemini gegen Jev auf denselben Artikeln"),
        ("run", "alles"),
    ]:
        s = sub.add_parser(name, help=help_)
        if name in ("ingest", "run"):
            s.add_argument("source", type=Path, help="PDF, XLSX oder CSV")
            s.add_argument("--pages", help="z. B. 12-40 (nur PDF)")
        s.add_argument("--job", required=True, help="Name des Ausgabeordners unter out/")
        s.add_argument("--supplier", default="Hersteller", help="Herstellername für BMEcat/Report")
        s.add_argument("--gln", default=None)
        s.add_argument("--include-review", action="store_true", help="auch unsichere Artikel exportieren")
        if name == "compare":
            s.add_argument("--reuse-gemini", action="store_true",
                           help="Gemini aus classified.json übernehmen, nur Jev neu fragen")
            s.add_argument("--features", action="store_true",
                           help="auch ETIM-Merkmale vergleichen (Typ L als Noul, Typ A als Choice)")

    s = sub.add_parser("review", help="Review-CSV eines Jobs zusammenfassen")
    s.add_argument("job_dir", type=Path)

    s = sub.add_parser("reference", help="Gerüst für reference.json anlegen (Klassen bleiben leer)")
    s.add_argument("job_dir", type=Path, help="Ordner unter out/, z. B. out/strawa")

    s = sub.add_parser("ui", help="Prüf-Cockpit im Browser öffnen")
    s.add_argument("job_dir", type=Path, help="Ordner unter out/, z. B. out/demo — oder out/ selbst")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-open", action="store_true", help="Browser nicht automatisch öffnen")

    s = sub.add_parser("studio", help="Cockpit über allen Jobs: Katalog hochladen, Lauf starten")
    s.add_argument("--out", type=Path, default=None, help="Verzeichnis der Jobs (Vorgabe: out/)")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-open", action="store_true", help="Browser nicht automatisch öffnen")

    a = ap.parse_args(argv)
    if a.cmd == "inspect":
        from .model import inspect_folder

        inspect_folder(a.folder)
        return
    if a.cmd == "load-model":
        from .model import EtimModel, build_sqlite

        build_sqlite(a.folder)
        m = EtimModel()
        print("Zähler:", m.count())
        if not a.no_embed:
            from .classify import _class_matrix

            ids, emb = _class_matrix(m)
            print(f"Embeddings: {emb.shape}")
        return
    if a.cmd == "ui":
        from .ui import run as run_ui

        run_ui(a.job_dir, a.port, not a.no_open)
        return
    if a.cmd == "studio":
        from .ui import run as run_ui

        run_ui(a.out or config.OUT, a.port, not a.no_open)
        return
    if a.cmd == "reference":
        from .compare import reference_skeleton

        try:
            reference_skeleton(a.job_dir)
        except (FileExistsError, ValueError, OSError) as e:
            raise SystemExit(str(e))
        return
    if a.cmd == "review":
        import csv

        rows = list(csv.DictReader((a.job_dir / "review.csv").open(), delimiter=";"))
        by_pid = {}
        for r in rows:
            by_pid.setdefault(r["supplier_pid"], []).append(r)
        print(f"{len(rows)} Prüfpunkte bei {len(by_pid)} Artikeln")
        for pid, rs in list(by_pid.items())[:30]:
            print(f"  {pid} ({rs[0]['name'][:40]}): " + ", ".join((r['feature_id'] or 'KLASSE') for r in rs))
        return

    out_dir = config.OUT / a.job
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = None
    if getattr(a, "pages", None):
        lo, hi = a.pages.split("-")
        pages = (int(lo), int(hi))

    if a.cmd in ("ingest", "run"):
        from . import ingest

        ingest.run(a.source, out_dir, pages)
    if a.cmd in ("classify", "run"):
        from . import classify

        classify.run(out_dir)
    if a.cmd == "compare":
        from . import compare

        compare.run(out_dir, reuse_gemini=a.reuse_gemini, with_features=a.features)
        return
    if a.cmd in ("features", "run"):
        from . import features

        features.run(out_dir)
    if a.cmd in ("export", "run"):
        from . import export_bmecat

        export_bmecat.run(out_dir, a.supplier, a.gln, a.include_review)
    if a.cmd in ("validate", "run"):
        from . import validate

        validate.run(out_dir)
    if a.cmd in ("report", "run"):
        from . import report

        report.run(out_dir, a.supplier)


if __name__ == "__main__":
    sys.exit(main())
