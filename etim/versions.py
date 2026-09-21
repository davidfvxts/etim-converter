"""Welche ETIM-Version gilt — und wo ihre Daten liegen.

Mehrere Versionen nebeneinander heisst: je Version eine eigene SQLite-Datenbank
UND eigene Klassen-Embeddings. Die Klassen-IDs sind zwischen den Versionen nicht
stabil (Klassen kommen dazu, ändern Merkmale, verschwinden), darum darf nie ein
Cache der einen Version für eine andere benutzt werden.

Ablage:
    data/etim/8.0/   CSV-Release ETIM 8.0
    data/etim/9.0/   CSV-Release ETIM 9.0
    data/etim/10.0/  CSV-Release ETIM 10.0
    data/etim/       liegt das Release direkt hier, gilt es als Vorgabeversion

    data/cache/etim-<v>.sqlite      Klassen, Merkmale, Werte
    data/cache/class_emb-<v>.npz    Embeddings der Klassentexte
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config

SUPPORTED = ("8.0", "9.0", "10.0")

# Dateiendungen, an denen ein entpacktes CSV-Release erkennbar ist.
_DATA_SUFFIXES = (".csv", ".txt")


def normalize(version: str | None) -> str:
    """'ETIM-8', '8', 'etim 8.0' -> '8.0'. Unbekanntes bleibt unveraendert."""
    if not version:
        return default()
    m = re.search(r"(\d+)(?:\.(\d+))?", str(version))
    if not m:
        return str(version)
    return f"{int(m.group(1))}.{int(m.group(2) or 0)}"


def label(version: str | None) -> str:
    """'8.0' -> 'ETIM-8.0' — so steht es im BMEcat und im Report."""
    return f"ETIM-{normalize(version)}"


def default() -> str:
    """Vorgabeversion aus ETIM_VERSION (.env), ohne Praefix."""
    m = re.search(r"(\d+)(?:\.(\d+))?", config.ETIM_VERSION or "10.0")
    return f"{int(m.group(1))}.{int(m.group(2) or 0)}" if m else "10.0"


def _has_data(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return any(p.suffix.lower() in _DATA_SUFFIXES for p in folder.rglob("*") if p.is_file())


def data_dir(version: str | None = None) -> Path | None:
    """Ordner mit dem CSV-Release dieser Version, oder None."""
    v = normalize(version)
    root = config.DATA / "etim"
    for name in (v, f"etim-{v}", f"ETIM-{v}", v.split(".")[0]):
        candidate = root / name
        if _has_data(candidate):
            return candidate
    # Release direkt unter data/etim/: gilt als Vorgabeversion.
    if v == default() and _has_data(root):
        return root
    return None


def db_path(version: str | None = None) -> Path:
    v = normalize(version)
    versioned = config.CACHE / f"etim-{v}.sqlite"
    legacy = config.CACHE / "etim.sqlite"
    # Der Cache von vor der Versionsumstellung gehoert zur Vorgabeversion.
    # Ihn weiterzubenutzen erspart einen Neubau von mehreren Minuten.
    if v == default() and not versioned.exists() and legacy.exists():
        return legacy
    return versioned


def emb_path(version: str | None = None) -> Path:
    v = normalize(version)
    versioned = config.CACHE / f"class_emb-{v}.npz"
    legacy = config.CACHE / "class_emb.npz"
    if v == default() and not versioned.exists() and legacy.exists():
        return legacy
    return versioned


def status(version: str) -> dict:
    """Was fuer diese Version vorliegt — und was noch fehlt."""
    v = normalize(version)
    folder = data_dir(v)
    db, emb = db_path(v), emb_path(v)
    ready = db.exists() and emb.exists()
    if ready:
        missing = ""
    elif not folder:
        missing = (f"Kein CSV-Release in data/etim/{v}/ — Download von "
                   f"etim-international.com dort entpacken.")
    elif not db.exists():
        missing = f"Noch nicht geladen: python -m etim load-model --etim {v}"
    else:
        missing = f"Embeddings fehlen: python -m etim load-model --etim {v}"
    return {
        "version": v,
        "label": label(v),
        "data": str(folder) if folder else "",
        "has_data": bool(folder),
        "has_db": db.exists(),
        "has_embeddings": emb.exists(),
        "ready": ready,
        "missing": missing,
        "default": v == default(),
    }


def available() -> list[dict]:
    """Zustand aller unterstuetzten Versionen, fuer CLI und Oberflaeche."""
    return [status(v) for v in SUPPORTED]


def require(version: str) -> str:
    """Version pruefen und normalisiert zurueckgeben, sonst mit Klartext abbrechen."""
    v = normalize(version)
    if v not in SUPPORTED:
        raise SystemExit(f"ETIM {v} wird nicht unterstuetzt — moeglich: {', '.join(SUPPORTED)}")
    st = status(v)
    if not st["ready"]:
        raise SystemExit(f"ETIM {v} ist nicht einsatzbereit. {st['missing']}")
    return v
