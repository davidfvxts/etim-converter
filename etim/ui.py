"""Prüf-Cockpit: `python -m etim ui out/<job>` oder `python -m etim studio`

Serviert web/ und die Zwischenstände der Jobs über einen Server aus der
Standardbibliothek — kein Build, keine zusätzliche Abhängigkeit.

Gelesen wird alles, was die Pipeline geschrieben hat. Geschrieben werden
ausschließlich: review.decisions.json (Freigaben aus der Oberfläche), der
hochgeladene Katalog unter out/<job>/source/ und die Ergebnisse eines Laufs.

Der Server hört nur auf 127.0.0.1. Hochgeladene Kataloge sind Kundendaten und
landen unter out/ — nie im Repo.
"""
from __future__ import annotations

import json
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import config, jev, studio, versions

WEB = config.ROOT / "web"
DECISIONS = "review.decisions.json"


def _code_mtime() -> float:
    """Neueste Aenderung am Python-Code dieses Programms."""
    try:
        return max(p.stat().st_mtime for p in Path(__file__).parent.glob("*.py"))
    except (OSError, ValueError):
        return 0.0


# Stand des Codes beim Start. Die Oberflaeche wird bei jedem Aufruf frisch von
# der Platte gelesen, der Python-Code aber nur einmal beim Start. Nach einem
# `git pull` bekommt der Browser also neue Knoepfe an einem alten Server — und
# eine neue Route antwortet dann mit "Unbekannte Route". Dieser Vergleich macht
# daraus eine verstaendliche Ansage.
_STARTED_WITH = _code_mtime()


def code_is_stale() -> bool:
    return _code_mtime() > _STARTED_WITH + 1

FILE_DESC = {
    "catalog.bmecat.xml": "BMEcat 2005 · Lieferdatei für den Großhändler",
    "enriched.json": "Merkmale mit Quellzitat und Konfidenz",
    "classified.json": "Klassenentscheidung mit Kandidaten",
    "compare.json": "Modellvergleich Gemini gegen Jev",
    "products.json": "Rohartikel aus dem Katalog",
    "validation.json": "Befunde der BMEcat-Prüfung",
    "report.md": "Kundenreport: Vollständigkeit und Rückfragen",
    "review.csv": "Arbeitsliste der Prüfpunkte",
    "reference.json": "Referenzklassen für die Trefferquote",
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


def _value_labels(enriched: list[dict], extra_classes: list[str] = (),
                  etim_version: str | None = None) -> dict[str, str]:
    """EV-Codes in Klartext auflösen, damit die Oberfläche keine nackten Codes zeigt.

    Ohne geladenes Modell (data/cache/etim.sqlite fehlt) bleibt die Zuordnung leer —
    die Oberfläche zeigt dann den Code, erfindet aber keinen Text.
    """
    codes = {f["value"] for e in enriched for f in e.get("features", [])
             if isinstance(f.get("value"), str) and f["value"].startswith("EV")}
    class_ids = {e["class_id"] for e in enriched if e.get("class_id")} | set(extra_classes)
    if not codes or not class_ids:
        return {}
    try:
        from .model import EtimModel

        # Wertelisten aus der Version des Jobs — Codes bedeuten anderswo anderes.
        model = EtimModel(version=etim_version)
        labels: dict[str, str] = {}
        for cid in class_ids:
            for feat in model.features_for(cid):
                for code, text in feat.values:
                    if code in codes:
                        labels[code] = text
        return labels
    except Exception:
        return {}


def _classifier_of(classified: list) -> dict:
    """Welches Modell die vorliegende Klassifizierung erzeugt hat.

    Steht an jedem Artikel; hier zusammengefasst, damit die Oberflaeche es oben
    anzeigen kann, ohne die ganze Liste durchzusehen.
    """
    if not classified:
        return {}
    models = {r.get("model", "gemini") for r in classified}
    etim = sorted({r.get("etim_version", "") for r in classified} - {""})
    return {
        "models": sorted(models),
        "etim": etim,
        "etim_label": ", ".join(versions.label(v) for v in etim),
        "label": " und ".join(studio.LABELS.get(m, m) for m in sorted(models)),
        "mixed": len(models) > 1,
        "simulated": any(r.get("simulated") for r in classified),
    }


def payload(job_dir: Path) -> dict:
    products = _load(job_dir / "products.json", {})
    enriched = _load(job_dir / "enriched.json", [])
    compare = _load(job_dir / "compare.json", None)
    classified = _load(job_dir / "classified.json", [])
    files = []
    for name, desc in FILE_DESC.items():
        p = job_dir / name
        if p.exists():
            files.append({"name": name, "desc": desc, "size": _human(p.stat().st_size)})
    extra = [fc["class_id"] for fc in (compare or {}).get("features", []) if fc.get("class_id")]
    return {
        "job": job_dir.name,
        "config": {
            "review_threshold": config.REVIEW_THRESHOLD,
            "min_coverage": config.MIN_COVERAGE,
            "etim_version": versions.label(_classifier_of(classified).get("etim", [None])[0]
                                           if classified else None),
            "top_k": config.TOP_K_CLASSES,
            "jev_top_k": config.JEV_TOP_K,
            "max_upload_mb": config.MAX_UPLOAD_MB,
            "classifier": config.CLASSIFIER,
            "classifiers": list(config.CLASSIFIERS),
            "etim_default": versions.default(),
        },
        "products": products.get("products", []) if isinstance(products, dict) else products,
        "classified": classified,
        "classifier": _classifier_of(classified),
        "enriched": enriched,
        "compare": compare,
        "validation": _load(job_dir / "validation.json", None),
        "decisions": _load(job_dir / DECISIONS, {}),
        "labels": _value_labels(enriched, extra,
                                next((e.get("etim_version") for e in enriched if e.get("etim_version")), None)),
        "files": files,
        "jev": jev.status(),
        "run": studio.run_state(job_dir.name),
        "meta": _load(job_dir / "job.json", {}),
    }


class Handler(SimpleHTTPRequestHandler):
    out_root: Path
    default_job: str = ""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    # ---------------------------------------------------------------- Werkzeug

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status: int, message: str):
        # Bei einem abgelehnten Upload steht der Rumpf noch ungelesen im Socket.
        # Ohne Schliessen liest der Server ihn als naechste Anfrage.
        self.close_connection = True
        self._json({"error": message}, status)

    def _query(self) -> dict[str, str]:
        q = parse_qs(urlparse(self.path).query)
        return {k: v[0] for k, v in q.items() if v}

    def _job_dir(self, name: str = "") -> Path:
        name = name or self.default_job
        if not name:
            raise studio.UploadError("Kein Job gewählt.")
        return self.out_root / studio.safe_job(name)

    def _local_only(self) -> bool:
        """Nur lokale Aufrufe. Schützt vor Seiten, die auf 127.0.0.1 posten."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            o = urlparse(origin).hostname
            if o not in ("127.0.0.1", "localhost", "::1"):
                return False
        return True

    # -------------------------------------------------------------------- GET

    def do_GET(self):
        route = urlparse(self.path).path.rstrip("/") or "/"
        if not route.startswith("/api"):
            return super().do_GET()
        q = self._query()
        try:
            if route == "/api/jobs":
                return self._json({
                    "stale": code_is_stale(),
                    "jobs": studio.list_jobs(self.out_root),
                    "default_job": self.default_job,
                    "jev": jev.status(),
                    "max_upload_mb": config.MAX_UPLOAD_MB,
                    "allowed": sorted(studio.ALLOWED),
                    "classifier": config.CLASSIFIER,
                    "classifiers": list(config.CLASSIFIERS),
                    "etim_default": versions.default(),
                    "etim_versions": versions.available(),
                })
            if route == "/api/job":
                job_dir = self._job_dir(q.get("job", ""))
                if not job_dir.exists():
                    return self._fail(404, f"Job '{job_dir.name}' gibt es nicht.")
                return self._json(payload(job_dir))
            if route == "/api/run":
                job = q.get("job", "") or self.default_job
                # Laeufe ohne Job (ETIM laden) tragen "etim:<version>" als Schluessel.
                key = job if job.startswith("etim:") else studio.safe_job(job)
                return self._json(studio.run_state(key) or {"state": "idle"})
            if route == "/api/etim":
                return self._json({"versions": versions.available(),
                                   "default": versions.default(),
                                   "runs": {v["version"]: studio.run_state(studio.etim_key(v["version"]))
                                            for v in versions.available()}})
        except studio.UploadError as e:
            return self._fail(400, str(e))
        return self._fail(404, self._unknown_route(route))

    def _unknown_route(self, route: str) -> str:
        if code_is_stale():
            return (f"Diese Funktion gibt es im laufenden Server noch nicht ({route}). "
                    "Der Programmcode auf der Platte ist neuer als der laufende Server — "
                    "bitte das Cockpit einmal neu starten.")
        return f"Unbekannte Route: {route}"

    # ------------------------------------------------------------------- POST

    # Wieviel von einem zu grossen Rumpf noch weggelesen wird, damit der Browser
    # die Begruendung bekommt statt eines Verbindungsabbruchs. Darueber wird hart
    # geschlossen — wer so viel schickt, meint es nicht ernst.
    DRAIN_CAP = 256 * 1024 * 1024

    def _drain(self, n: int) -> None:
        rest = min(n, self.DRAIN_CAP)
        while rest > 0:
            chunk = self.rfile.read(min(rest, 1 << 20))
            if not chunk:
                return
            rest -= len(chunk)

    def _body(self, limit: int) -> bytes:
        length = self.headers.get("Content-Length")
        if length is None:
            raise studio.UploadError("Anfrage ohne Content-Length.")
        n = int(length)
        if n > limit:
            # Erst zu Ende lesen, dann ablehnen: sonst bricht die Verbindung ab,
            # waehrend der Browser noch sendet, und er zeigt einen Netzfehler
            # statt der Begruendung.
            self._drain(n)
            raise studio.UploadError(
                f"Die Datei ist {n / 1e6:.1f} MB gross, erlaubt sind {limit / 1e6:.0f} MB.")
        return self.rfile.read(n)

    def do_POST(self):
        route = urlparse(self.path).path.rstrip("/") or "/"
        if not self._local_only():
            return self._fail(403, "Nur lokale Aufrufe erlaubt.")
        try:
            if route == "/api/upload":
                return self._upload()
            if route == "/api/run":
                return self._start_run()
            if route == "/api/load-etim":
                data = json.loads(self._body(4_000) or b"{}")
                return self._json(studio.start_load_etim(str(data.get("version") or "")), 202)
            if route == "/api/jev-check":
                self._body(4_000) if self.headers.get("Content-Length") else None
                return self._json(jev.selftest())
            if route == "/api/decisions":
                return self._decisions()
        except studio.UploadError as e:
            return self._fail(400, str(e))
        except FileNotFoundError as e:
            return self._fail(404, str(e))
        except Exception as e:  # noqa: BLE001 — ein Fehler gehört ins Dashboard, nicht nur ins Terminal
            return self._fail(500, f"{type(e).__name__}: {e}")
        return self._fail(404, self._unknown_route(route))

    def _upload(self):
        limit = config.MAX_UPLOAD_MB * 1024 * 1024
        # Der Browser schickt den Namen prozentkodiert (HTTP-Header sind ASCII).
        name = unquote(self.headers.get("X-Filename") or "") or "katalog"
        data = self._body(limit)
        info = studio.accept_upload(self.out_root, name, data)
        info["summary"] = studio.job_summary(self.out_root / info["job"])
        return self._json(info, 201)

    def _start_run(self):
        data = json.loads(self._body(64_000) or b"{}")
        job_dir = self._job_dir(str(data.get("job") or ""))
        if not job_dir.exists():
            return self._fail(404, f"Job '{job_dir.name}' gibt es nicht.")
        kind = "full" if data.get("kind") == "full" else "compare"
        if kind == "compare" and not (job_dir / "products.json").exists():
            return self._fail(400, "Für diesen Job gibt es noch keine products.json — "
                                   "erst den Katalog einlesen lassen.")
        pages = None
        if isinstance(data.get("pages"), list) and len(data["pages"]) == 2:
            pages = (int(data["pages"][0]), int(data["pages"][1]))
        state = studio.start(job_dir, kind=kind,
                             classifier=str(data.get("classifier") or "") or None,
                             etim_version=str(data.get("etim_version") or "") or None,
                             reuse_gemini=bool(data.get("reuse_gemini")),
                             with_features=bool(data.get("with_features")),
                             pages=pages)
        return self._json(state, 202)

    def _decisions(self):
        q = self._query()
        job_dir = self._job_dir(q.get("job", ""))
        if not job_dir.exists():
            return self._fail(404, f"Job '{job_dir.name}' gibt es nicht.")
        data = json.loads(self._body(2_000_000) or b"{}")
        if not isinstance(data, dict):
            return self._fail(400, "Objekt erwartet.")
        (job_dir / DECISIONS).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return self._json({"saved": len(data)})

    def end_headers(self):
        """Nichts zwischenspeichern lassen — gilt fuer statische Dateien und JSON.

        Ohne Cache-Control schickt der Server nur Last-Modified. Browser leiten
        daraus eine eigene Haltbarkeit ab (Safari besonders grosszuegig) und
        liefern nach einem `git pull` weiter die alte Oberflaeche aus — neue
        Knoepfe fehlen dann einfach, ohne jede Fehlermeldung. Auf 127.0.0.1
        bringt Zwischenspeichern ohnehin nichts.
        """
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):  # Zugriffe nicht ins Terminal spülen
        pass


def run(target: Path, port: int = 8000, open_browser: bool = True) -> None:
    """`target` ist entweder ein Job-Ordner oder das out/-Verzeichnis selbst."""
    target = target.resolve()
    if target.exists() and not target.is_dir():
        raise SystemExit(f"Kein Ordner: {target}")

    is_job = target.name != config.OUT.name and (
        (target / "job.json").exists() or (target / "products.json").exists()
        or (target / "enriched.json").exists())
    if is_job:
        out_root, default_job = target.parent, target.name
    else:
        out_root, default_job = target, ""
        out_root.mkdir(parents=True, exist_ok=True)

    ok, reason = jev.configured()
    # "eingerichtet", nicht "bereit": geprueft ist nur, dass die Zugangsdaten
    # dastehen. Ob Jev antwortet, sagt erst ein echter Aufruf (make jev-check).
    print(f"Jev: {'eingerichtet' if ok else 'nicht eingerichtet'} — {reason}")
    if ok and not config.DRY_RUN:
        print("     ob er auch antwortet, zeigt:  make jev-check")

    handler = type("JobHandler", (Handler,), {"out_root": out_root, "default_job": default_job})
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/" + (f"?job={default_job}" if default_job else "")
        label = f"Job '{default_job}'" if default_job else f"{len(studio.list_jobs(out_root))} Jobs in {out_root}"
        print(f"Prüf-Cockpit · {label}: {url}  (Strg+C beendet)")
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbeendet")
