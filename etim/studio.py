"""Jobs anlegen, Kataloge entgegennehmen, Laeufe im Hintergrund fahren.

Alles, was das Dashboard braucht, um ohne Terminal zu arbeiten. Der HTTP-Teil
steht in ui.py; hier steht, was passiert — damit es testbar bleibt, ohne einen
Server zu starten.

Regeln, die hier durchgesetzt werden:
- nur PDF/XLSX/CSV, am Inhalt geprueft, nicht an der Endung
- Groessenlimit aus config.MAX_UPLOAD_MB
- Jobnamen aus dem Dateinamen, bei Kollision hochgezaehlt — nie ueberschreiben
- je Job nur ein aktiver Lauf
- Kundenkataloge liegen in out/<job>/source/ und damit ausserhalb des Repos
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from . import config, versions

ALLOWED = {".pdf": "PDF", ".xlsx": "Excel", ".csv": "CSV"}
SOURCE_DIR = "source"

# Ablauf eines Laufs, wie ihn das Dashboard anzeigt.
STAGES = [
    ("etim", "ETIM laden"),
    ("ingest", "Artikel extrahieren"),
    ("retrieval", "Retrieval"),
    ("gemini", "Gemini"),
    ("jev", "Jev"),
    ("features", "Merkmale"),
]

LABELS = {"gemini": "Gemini", "jev": "Jev", "both": "Gemini und Jev"}

# Schluessel fuer Laeufe, die zu keinem Job gehoeren (ETIM-Version laden).
def etim_key(version: str) -> str:
    return f"etim:{version}"


class UploadError(ValueError):
    """Abgelehnter Upload — die Meldung geht wortwoertlich ins Dashboard."""


class RunCancelled(Exception):
    """Der Lauf wurde aus der Oberflaeche abgebrochen.

    Wird aus dem Fortschritts-Rueckruf geworfen, also zwischen zwei Artikeln.
    Der Zwischenstand bleibt damit erhalten und der naechste Lauf setzt dort auf.
    """


# ----------------------------------------------------------------- Dateiprüfung

def sniff(data: bytes, filename: str) -> str:
    """Dateityp am Inhalt bestimmen. Die Endung ist nur ein Hinweis, kein Beweis."""
    suffix = Path(filename).suffix.lower()
    if data[:5] == b"%PDF-":
        return ".pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile:
            raise UploadError("Die Datei sieht aus wie ein ZIP, ist aber beschaedigt.") from None
        if "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names):
            return ".xlsx"
        raise UploadError("ZIP-Datei, aber keine Excel-Arbeitsmappe.")
    # CSV: muss als Text lesbar sein und ein Trennzeichen haben.
    if suffix == ".csv":
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                head = data[:8192].decode(enc)
            except UnicodeDecodeError:
                continue
            if "\x00" in head:
                break
            if any(d in head for d in (";", ",", "\t")):
                return ".csv"
            raise UploadError("CSV ohne erkennbares Trennzeichen (; , oder Tab).")
        raise UploadError("CSV-Datei ist nicht als Text lesbar.")
    raise UploadError(
        "Nur PDF, XLSX und CSV werden angenommen — der Inhalt dieser Datei passt zu keinem davon.")


def slug(filename: str) -> str:
    """Jobname aus dem Dateinamen: klein, nur a-z0-9 und Bindestrich."""
    stem = Path(filename).stem.lower()
    s = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:48].strip("-")
    return s or "katalog"


def safe_job(name: str) -> str:
    """Jobnamen aus einer Anfrage saeubern — kein Pfad, kein Ausbruch aus out/."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name or "") or name in (".", ".."):
        raise UploadError(f"Ungueltiger Jobname: {name!r}")
    return name


def free_job_name(out_root: Path, base: str) -> str:
    """Nie einen bestehenden Job ueberschreiben — bei Kollision hochzaehlen."""
    name, i = base, 2
    while (out_root / name).exists():
        name = f"{base}-{i}"
        i += 1
    return name


def accept_upload(out_root: Path, filename: str, data: bytes) -> dict:
    """Katalog annehmen und einen neuen Job dafuer anlegen."""
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    if not data:
        raise UploadError("Die Datei ist leer.")
    if len(data) > limit:
        raise UploadError(f"Die Datei ist {len(data) / 1e6:.1f} MB gross, erlaubt sind "
                          f"{config.MAX_UPLOAD_MB} MB.")
    suffix = sniff(data, filename)
    out_root.mkdir(parents=True, exist_ok=True)
    job = free_job_name(out_root, slug(filename))
    job_dir = out_root / job
    (job_dir / SOURCE_DIR).mkdir(parents=True)
    target = job_dir / SOURCE_DIR / f"katalog{suffix}"
    target.write_bytes(data)
    (job_dir / "job.json").write_text(json.dumps({
        "job": job,
        "original_filename": Path(filename).name,
        "kind": ALLOWED[suffix],
        "bytes": len(data),
        "uploaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": f"{SOURCE_DIR}/{target.name}",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"job": job, "kind": ALLOWED[suffix], "bytes": len(data), "source": str(target)}


# ------------------------------------------------------------------ Jobliste

def _count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if isinstance(data, dict):
        data = data.get("products", [])
    return len(data) if isinstance(data, list) else 0


def _classifier_label(job_dir: Path) -> str:
    """Welches Modell diesen Job klassifiziert hat — fuer die Jobliste."""
    try:
        rows = json.loads((job_dir / "classified.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return ""
    if not isinstance(rows, list) or not rows:
        return ""
    models = sorted({r.get("model", "gemini") for r in rows if isinstance(r, dict)})
    return " und ".join(LABELS.get(m, m) for m in models)


def _etim_label(job_dir: Path) -> str:
    try:
        rows = json.loads((job_dir / "classified.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return ""
    if not isinstance(rows, list):
        return ""
    vs = sorted({r.get("etim_version", "") for r in rows if isinstance(r, dict)} - {""})
    return ", ".join(versions.label(v) for v in vs)


def job_summary(job_dir: Path) -> dict:
    meta_path = job_dir / "job.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
    run = RUNS.get(job_dir.name)
    return {
        "job": job_dir.name,
        "source_name": meta.get("original_filename", ""),
        "kind": meta.get("kind", ""),
        "uploaded": meta.get("uploaded", ""),
        "products": _count(job_dir / "products.json"),
        "has_classified": (job_dir / "classified.json").exists(),
        "classifier": _classifier_label(job_dir),
        "etim": _etim_label(job_dir),
        "has_enriched": (job_dir / "enriched.json").exists(),
        "has_compare": (job_dir / "compare.json").exists(),
        "has_reference": (job_dir / "reference.json").exists(),
        "running": bool(run and run.state == "running"),
        "mtime": job_dir.stat().st_mtime,
    }


def list_jobs(out_root: Path) -> list[dict]:
    if not out_root.exists():
        return []
    jobs = [job_summary(p) for p in out_root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    return sorted(jobs, key=lambda j: -j["mtime"])


# --------------------------------------------------------------------- Laeufe

@dataclass
class RunState:
    job: str
    kind: str
    classifier: str = "gemini"      # gemini | jev | both
    etim_version: str = ""          # 8.0 | 9.0 | 10.0
    state: str = "running"          # running | done | error | cancelled
    cancel: bool = False
    stage: str = ""
    stage_label: str = ""
    done: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    started: str = ""
    finished: str = ""
    log: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    _t0: float = 0.0
    _t1: float = 0.0

    def elapsed(self) -> float:
        start = self._t0 or 0.0
        end = self._t1 or time.monotonic()
        return max(0.0, end - start)

    def eta(self) -> float | None:
        """Restzeit aus dem bisherigen Tempo. Erst ab ein paar Artikeln sinnvoll."""
        if self.state != "running" or self.done < 3 or self.total <= self.done:
            return None
        pro_stueck = self.elapsed() / self.done
        return pro_stueck * (self.total - self.done)

    def as_dict(self) -> dict:
        return {
            "elapsed_s": round(self.elapsed()),
            "eta_s": round(self.eta()) if self.eta() is not None else None,
            "cancel_requested": self.cancel,
            "job": self.job, "kind": self.kind, "classifier": self.classifier,
            # Beim Laden einer ETIM-Version ist kein Modell beteiligt — dann auch
            # keins anzeigen, sonst steht dort faelschlich "Gemini".
            "classifier_label": "" if self.kind == "load-etim"
                                else LABELS.get(self.classifier, self.classifier),
            "etim_version": self.etim_version,
            "state": self.state,
            "stage": self.stage, "stage_label": self.stage_label,
            "done": self.done, "total": self.total, "message": self.message,
            "error": self.error, "started": self.started, "finished": self.finished,
            "log": self.log[-40:],
            "stages": [{"key": k, "label": lbl, "active": k in self.stages}
                       for k, lbl in STAGES],
        }


RUNS: dict[str, RunState] = {}
_LOCK = threading.Lock()
STAGE_LABELS = dict(STAGES)


def active(job: str) -> bool:
    r = RUNS.get(job)
    return bool(r and r.state == "running")


def cancel(job: str) -> dict:
    """Abbruch anfordern. Der Lauf endet zwischen zwei Artikeln, nicht mittendrin."""
    r = RUNS.get(job)
    if not r or r.state != "running":
        raise UploadError(f"Für '{job}' läuft gerade kein Lauf.")
    r.cancel = True
    r.message = "Abbruch angefordert — der laufende Artikel wird noch zu Ende gebracht …"
    return r.as_dict()


def run_state(job: str) -> dict | None:
    r = RUNS.get(job)
    return r.as_dict() if r else None


def start(job_dir: Path, *, kind: str = "compare", reuse_gemini: bool = False,
          with_features: bool = False, pages: tuple[int, int] | None = None,
          classifier: str | None = None, etim_version: str | None = None,
          fresh: bool = False) -> dict:
    """Einen Lauf im Hintergrund starten. Je Job laeuft hoechstens einer."""
    job = job_dir.name
    classifier = (classifier or config.CLASSIFIER).lower()
    if classifier not in config.CLASSIFIERS:
        raise UploadError(f"Unbekanntes Modell '{classifier}'.")
    etim_version = versions.normalize(etim_version)
    st_v = versions.status(etim_version)
    if not st_v["ready"]:
        raise UploadError(f"ETIM {etim_version} ist nicht einsatzbereit. {st_v['missing']}")
    with _LOCK:
        if active(job):
            raise UploadError(f"Fuer '{job}' laeuft bereits ein Lauf.")
        # Nur die Stufen anzeigen, die auch laufen — sonst wartet man auf Gemini,
        # obwohl nur Jev gefragt wird.
        stages = ["retrieval"]
        stages += ["gemini", "jev"] if classifier == "both" else [classifier]
        if kind == "full":
            stages.insert(0, "ingest")
        if with_features:
            stages.append("features")
        st = RunState(job=job, kind=kind, classifier=classifier,
                      etim_version=etim_version, stages=stages, _t0=time.monotonic(),
                      started=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      stage=stages[0], stage_label=STAGE_LABELS[stages[0]],
                      message="Lauf gestartet")
        RUNS[job] = st

    t = threading.Thread(target=_worker, name=f"etim-run-{job}", daemon=True,
                         args=(job_dir, st, kind, reuse_gemini, with_features, pages,
                               classifier, etim_version, fresh))
    t.start()
    return st.as_dict()


def start_load_etim(version: str) -> dict:
    """Eine ETIM-Version im Hintergrund laden — damit das nicht ins Terminal muss.

    Laeuft ueber dieselbe Fortschrittsanzeige wie ein Vergleichslauf; der
    Schluessel ist die Version, nicht ein Job.
    """
    from . import versions

    v = versions.normalize(version)
    if v not in versions.SUPPORTED:
        raise UploadError(f"ETIM {v} wird nicht unterstuetzt.")
    key = etim_key(v)
    with _LOCK:
        if active(key):
            raise UploadError(f"ETIM {v} wird bereits geladen.")
        st = RunState(job=key, kind="load-etim", etim_version=v,
                      stages=["etim"], _t0=time.monotonic(),
                      started=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      stage="etim", stage_label=f"ETIM {v} laden",
                      message=f"ETIM {v} wird geladen")
        RUNS[key] = st

    threading.Thread(target=_load_etim_worker, name=f"etim-load-{v}", daemon=True,
                     args=(st, v)).start()
    return st.as_dict()


def _load_etim_worker(st: RunState, version: str) -> None:
    from . import versions
    from .classify import _class_matrix
    from .model import EtimModel, build_sqlite

    def say(text: str) -> None:
        st.message = text
        st.log.append(text)

    try:
        folder = versions.data_dir(version)
        if not folder:
            say(f"Archiv fuer ETIM {version} suchen …")
            folder = versions.unpack(version)
        say(f"CSV aus {folder.name} nach SQLite laden …")
        build_sqlite(folder, versions.db_path(version), version)
        model = EtimModel(versions.db_path(version), version)
        counts = model.count()
        say(f"{counts['classes']} Klassen geladen — Embeddings berechnen "
            f"(dauert einige Minuten und kostet ein paar Cent) …")
        ids, emb = _class_matrix(model)
        st.state = "done"
        say(f"ETIM {version} einsatzbereit: {len(ids)} Klassen, Embeddings {emb.shape}")
    except BaseException as e:  # noqa: BLE001 — der Grund gehoert ins Dashboard
        st.state = "error"
        st.error = f"{type(e).__name__}: {e}"
        st.log.append(st.error)
        traceback.print_exc()
    finally:
        st._t1 = time.monotonic()
        st.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _worker(job_dir: Path, st: RunState, kind: str, reuse_gemini: bool,
            with_features: bool, pages: tuple[int, int] | None, classifier: str,
            etim_version: str, fresh: bool = False) -> None:
    def tick(stage: str, done: int, total: int, message: str) -> None:
        if st.cancel:
            # Zwischen zwei Artikeln: der Zwischenstand ist geschrieben, der
            # naechste Lauf setzt genau hier wieder auf.
            raise RunCancelled()
        st.stage, st.stage_label = stage, STAGE_LABELS.get(stage, stage)
        st.done, st.total, st.message = done, total, message
        if message and (not st.log or st.log[-1] != message):
            st.log.append(message)

    try:
        if kind == "full":
            meta_path = job_dir / "job.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            src = job_dir / meta.get("source", "")
            if not meta.get("source") or not src.exists():
                raise FileNotFoundError(
                    f"Kein Katalog im Job '{st.job}' hinterlegt — bitte erneut hochladen.")
            tick("ingest", 0, 0, f"{src.name} wird gelesen")
            from . import ingest

            products = ingest.run(src, job_dir, pages)
            tick("ingest", len(products), len(products), f"{len(products)} Artikel extrahiert")
            if not products:
                raise ValueError("Aus dem Katalog wurde kein Artikel extrahiert — "
                                 "Seitenbereich oder Dateiformat pruefen.")

        from .model import EtimModel

        etim_model = EtimModel(version=etim_version)
        if classifier == "both":
            from . import compare

            comp = compare.run(job_dir, etim_model, reuse_gemini=reuse_gemini,
                               with_features=with_features, progress=tick, fresh=fresh)
            st.message = (f"{len(comp.items)} Artikel verglichen — ohne Klasse: "
                          f"Gemini {comp.metrics['gemini'].no_class}, Jev {comp.metrics['jev'].no_class}")
        else:
            from . import classify

            tick(classifier, 0, 0, f"Klassifizieren mit {LABELS[classifier]} gegen ETIM {etim_version}")
            rows = classify.run(job_dir, etim_model, classifier=classifier,
                                progress=tick, fresh=fresh)
            n_none = sum(1 for r in rows if not r.decision.class_id)
            tick(classifier, len(rows), len(rows), "fertig")
            st.message = (f"{len(rows)} Artikel mit {LABELS[classifier]} klassifiziert — "
                          f"{n_none} ohne Klasse, {sum(r.needs_review for r in rows)} zur Prüfung")
        st.log.append(st.message)
        st.state = "done"
    except RunCancelled:
        st.state = "cancelled"
        st.message = (f"Abgebrochen nach {st.done} von {st.total} Artikeln. "
                      "Der Zwischenstand ist gesichert — ein neuer Lauf setzt dort auf.")
        st.log.append(st.message)
    except BaseException as e:  # noqa: BLE001 — der Fehler muss ins Dashboard, nicht ins Nichts
        st.state = "error"
        st.error = f"{type(e).__name__}: {e}"
        st.log.append(st.error)
        traceback.print_exc()
    finally:
        st._t1 = time.monotonic()
        st.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
