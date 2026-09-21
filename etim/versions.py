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


def find_archive(version: str | None = None) -> Path | None:
    """Ein noch nicht entpacktes CSV-Release dieser Version finden.

    Gesucht wird in data/downloads/ und data/etim/ nach einem ZIP, dessen Name
    die Version nennt — so wie die Dateien von etim-international.com heissen
    (ETIM-9.0-ALL-SECTORS-CSV-METRIC-EI-2022-12-05.zip). Erspart das Entpacken
    von Hand in den richtigen Unterordner.
    """
    v = normalize(version)
    major = v.split(".")[0]
    patterns = (f"etim-{v}-", f"etim{v}-", f"etim-{major}.0-", f"etim{major}-")
    for folder in (config.DATA / "downloads", config.DATA / "etim", config.DATA):
        if not folder.is_dir():
            continue
        for zip_path in sorted(folder.glob("*.zip")):
            name = zip_path.name.lower()
            if any(name.startswith(p) or f"-{p}" in name for p in patterns):
                # "CSV" im Namen trennt das Datenrelease von IXF- und Guideline-ZIPs.
                if "csv" in name or "sectors" in name:
                    return zip_path
    return None


def unpack(version: str, archive: Path | None = None) -> Path:
    """Ein CSV-Release nach data/etim/<version>/ entpacken."""
    import zipfile

    v = normalize(version)
    archive = archive or find_archive(v)
    if not archive:
        raise SystemExit(
            f"Kein CSV-Release fuer ETIM {v} gefunden. ZIP nach {config.DATA / 'downloads'}/ "
            f"legen oder den entpackten Ordner als {config.DATA / 'etim' / v}/ anlegen.")
    target = config.DATA / "etim" / v
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        # Nur die CSV-Dateien, ohne Pfade: manche Releases haben einen Unterordner.
        for member in z.namelist():
            name = Path(member).name
            if not name or not name.lower().endswith(_DATA_SUFFIXES):
                continue
            with z.open(member) as src, (target / name).open("wb") as dst:
                dst.write(src.read())
    print(f"→ {archive.name} nach {target}/ entpackt")
    return target


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
        # Absoluter Pfad, weil diese Meldung genau dann gelesen wird, wenn
        # unklar ist, wo der Ordner ueberhaupt liegt.
        zip_here = config.DATA / "downloads"
        missing = (f"Kein CSV-Release fuer ETIM {v}. Das ZIP von etim-international.com "
                   f"unveraendert nach {zip_here}/ legen, dann: "
                   f"make load-model ETIM={v}")
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
