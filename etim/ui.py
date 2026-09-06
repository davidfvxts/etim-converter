"""Prüf-Cockpit: `python -m etim ui out/<job>`

Serviert web/ und die Zwischenstände des Jobs über einen Server aus der
Standardbibliothek — kein Build, keine zusätzliche Abhängigkeit. Gelesen wird
alles, was die Pipeline geschrieben hat; geschrieben wird ausschließlich
review.decisions.json (Freigabe- und Korrekturvermerke aus der Oberfläche).
"""
from __future__ import annotations

import json
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config

WEB = config.ROOT / "web"
DECISIONS = "review.decisions.json"

FILE_DESC = {
    "catalog.bmecat.xml": "BMEcat 2005 · Lieferdatei für den Großhändler",
    "enriched.json": "Merkmale mit Quellzitat und Konfidenz",
    "classified.json": "Klassenentscheidung mit Kandidaten",
    "products.json": "Rohartikel aus dem Katalog",
    "validation.json": "Befunde der BMEcat-Prüfung",
    "report.md": "Kundenreport: Vollständigkeit und Rückfragen",
    "review.csv": "Arbeitsliste der Prüfpunkte",
    DECISIONS: "Freigaben aus dem Cockpit",
}


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _value_labels(enriched: list[dict]) -> dict[str, str]:
    """EV-Codes in Klartext auflösen, damit die Oberfläche keine nackten Codes zeigt.

    Ohne geladenes Modell (data/cache/etim.sqlite fehlt) bleibt die Zuordnung leer —
    die Oberfläche zeigt dann den Code, erfindet aber keinen Text.
    """
    codes = {f["value"] for e in enriched for f in e.get("features", [])
             if isinstance(f.get("value"), str) and f["value"].startswith("EV")}
    if not codes:
        return {}
    try:
        from .model import EtimModel

        model = EtimModel()
        labels: dict[str, str] = {}
        for e in enriched:
            if not e.get("class_id"):
                continue
            for feat in model.features_for(e["class_id"]):
                for code, text in feat.values:
                    if code in codes:
                        labels[code] = text
        return labels
    except Exception:
        return {}


def payload(job_dir: Path) -> dict:
    products = _load(job_dir / "products.json", {})
    enriched = _load(job_dir / "enriched.json", [])
    files = []
    for name, desc in FILE_DESC.items():
        p = job_dir / name
        if p.exists():
            files.append({"name": name, "desc": desc, "size": _human(p.stat().st_size)})
    return {
        "job": job_dir.name,
        "config": {
            "review_threshold": config.REVIEW_THRESHOLD,
            "min_coverage": config.MIN_COVERAGE,
            "etim_version": config.ETIM_VERSION,
        },
        "products": products.get("products", []) if isinstance(products, dict) else products,
        "classified": _load(job_dir / "classified.json", []),
        "enriched": enriched,
        "validation": _load(job_dir / "validation.json", None),
        "decisions": _load(job_dir / DECISIONS, {}),
        "labels": _value_labels(enriched),
        "files": files,
    }


class Handler(SimpleHTTPRequestHandler):
    job_dir: Path

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/api/job":
            self._json(payload(self.job_dir))
            return
        super().do_GET()

    def do_POST(self):
        if self.path.rstrip("/") != "/api/decisions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            self.send_error(413)
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_error(400, "kein gültiges JSON")
            return
        if not isinstance(data, dict):
            self.send_error(400, "Objekt erwartet")
            return
        (self.job_dir / DECISIONS).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self._json({"saved": len(data)})

    def log_message(self, fmt, *args):  # Zugriffe nicht ins Terminal spülen
        pass


def run(job_dir: Path, port: int = 8000, open_browser: bool = True) -> None:
    job_dir = job_dir.resolve()
    if not job_dir.exists():
        raise SystemExit(f"Job-Ordner fehlt: {job_dir}")
    if not (job_dir / "enriched.json").exists():
        print(f"Hinweis: {job_dir}/enriched.json fehlt — erst `python -m etim run <katalog>` ausführen.")

    handler = type("JobHandler", (Handler,), {"job_dir": job_dir})
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/"
        print(f"Prüf-Cockpit für Job '{job_dir.name}': {url}  (Strg+C beendet)")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbeendet")
