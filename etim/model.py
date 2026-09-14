"""ETIM-Klassifikationsmodell laden (CSV-Release) und abfragen.

Tabellen wie im echten Release ETIM-10.0-ALL-SECTORS-CSV-METRIC-EI-2024-12-05
(verifiziert am 14.9.2026; Groß-/Kleinschreibung egal, Trennzeichen wird erkannt):

  ETIMARTGROUP                (ARTGROUPID, GROUPDESC)
  ETIMARTCLASS                (ARTCLASSID, ARTGROUPID, ARTCLASSDESC, ARTCLASSVERSION, ARTCLASSVERSIONDATE)
  ETIMARTCLASSSYNONYMMAP      (ARTCLASSID, CLASSSYNONYM)
  ETIMFEATURE                 (FEATUREID, FEATUREGROUPID, FEATUREDESC)
  ETIMFEATUREGROUP            (FEATUREGROUPID, FEATUREGROUPDESC)   — derzeit ungenutzt
  ETIMUNIT                    (UNITOFMEASID, UNITDESC)
  ETIMVALUE                   (VALUEID, VALUEDESC)
  ETIMARTCLASSFEATUREMAP      (ARTCLASSFEATURENR, ARTCLASSID, FEATUREID, FEATURETYPE, UNITOFMEASID, SORTNR)
  ETIMARTCLASSFEATUREVALUEMAP (ARTCLASSFEATUREVALUENR, ARTCLASSFEATURENR, VALUEID, SORTNR)

Zwei Eigenheiten des echten Release, auf die die 6-Klassen-Fixture nicht stößt:
Die Dateien sind UTF-16LE **ohne BOM** (siehe _decode), und die Beschreibungsspalten
tragen **kein** Sprachsuffix — ARTCLASSDESC, nicht ARTCLASSDESC_EN. Beide Schreibweisen
stehen in COLUMN_ALIASES, damit sprachspezifische Releases weiter laden.

Weicht ein Release ab: `python -m etim inspect data/etim` zeigt Dateien + Spalten,
dann TABLE_ALIASES / COLUMN_ALIASES unten anpassen. Alles landet in data/cache/etim.sqlite.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import config

TABLE_ALIASES = {
    "group": ["etimartgroup", "artgroup", "group", "groups"],
    "class": ["etimartclass", "artclass", "class", "classes"],
    "synonym": [
        "etimartclasssynonymmap",
        "artclasssynonymmap",
        "classsynonymmap",
        "etimsynonym_en",
        "etimsynonym",
        "synonym",
        "synonyms",
        "etimartclasssynonym",
    ],
    "feature": ["etimfeature", "feature", "features"],
    "unit": ["etimunit", "unit", "units"],
    "value": ["etimvalue", "value", "values"],
    "classfeature": ["etimartclassfeaturemap", "artclassfeaturemap", "classfeaturemap", "classfeature"],
    "classfeaturevalue": [
        "etimartclassfeaturevaluemap",
        "artclassfeaturevaluemap",
        "classfeaturevaluemap",
        "classfeaturevalue",
    ],
}

COLUMN_ALIASES = {
    "group_id": ["artgroupid", "groupid", "groupcode", "code"],
    "group_desc": ["groupdesc_en", "groupdesc", "description", "descriptionen"],
    "class_id": ["artclassid", "classid", "classcode", "code"],
    "class_desc": ["artclassdesc_en", "artclassdesc", "classdesc", "description", "descriptionen"],
    "class_version": ["artclassversion", "version"],
    "synonym": ["classsynonym", "synonym", "description"],
    "feature_id": ["featureid", "featurecode", "code"],
    "feature_desc": ["featuredesc_en", "featuredesc", "description", "descriptionen"],
    "unit_id": ["unitofmeasid", "unitid", "unitcode", "code"],
    "unit_desc": ["unitdesc_en", "unitdesc", "description", "abbreviation"],
    "value_id": ["valueid", "valuecode", "code"],
    "value_desc": ["valuedesc_en", "valuedesc", "description", "descriptionen"],
    "cf_nr": ["artclassfeaturenr", "classfeaturenr", "id"],
    "cfv_cf_nr": ["artclassfeaturenr", "classfeaturenr"],
    "feature_type": ["featuretype", "type"],
    "sort": ["sortnr", "sort", "orderNumber", "ordernumber"],
}


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def find_tables(folder: Path) -> dict[str, Path]:
    files = {}
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() in (".csv", ".txt") and p.is_file():
            files[_norm(p.stem)] = p
    found: dict[str, Path] = {}
    for key, aliases in TABLE_ALIASES.items():
        for a in aliases:
            if _norm(a) in files:
                found[key] = files[_norm(a)]
                break
    return found


def _delimiter(text: str) -> str:
    """Trennzeichen aus der Kopfzeile bestimmen.

    Der ETIM-CSV-Release ist semikolongetrennt, Beschreibungstexte enthalten aber
    Kommas — csv.Sniffer rät darum gelegentlich falsch. Die Kopfzeile enthält keine
    Freitexte, deshalb entscheidet allein sie; erst wenn sie nichts hergibt, folgt
    der Sniffer und zuletzt das Semikolon als ETIM-Default.
    """
    header = text.split("\n", 1)[0]
    counts = {d: header.count(d) for d in (";", "\t", "|", ",")}
    best = max(counts, key=lambda d: counts[d])
    if counts[best] > 0:
        return best
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=";,\t|").delimiter
    except csv.Error:
        return ";"


def _decode(raw: bytes) -> str:
    """Bytes des Releases dekodieren.

    Der ETIM-10.0-CSV-Release ist UTF-16LE **ohne BOM**. Reines Durchprobieren
    reicht dafuer nicht: UTF-8 akzeptiert die eingestreuten NUL-Bytes klaglos und
    liefert Text, an dem erst das csv-Modul scheitert ("line contains NUL").
    Deshalb zuerst BOM pruefen, dann auf NUL-Muster testen, erst danach die
    Einbyte-Kandidaten.
    """
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    head = raw[:4096]
    if head.count(0) > len(head) // 4:
        # jedes zweite Byte NUL -> Byte-Reihenfolge aus der Position der NULs ableiten
        return raw.decode("utf-16-le" if head[1::2].count(0) >= head[0::2].count(0) else "utf-16-be")
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    text = _decode(path.read_bytes())
    reader = csv.DictReader(io.StringIO(text), delimiter=_delimiter(text))
    rows = [dict(r) for r in reader]
    return list(reader.fieldnames or []), rows


def pick(row: dict[str, str], key: str) -> str:
    cols = {_norm(k): k for k in row.keys()}
    for a in COLUMN_ALIASES[key]:
        if _norm(a) in cols:
            return (row[cols[_norm(a)]] or "").strip()
    raise KeyError(f"Spalte für '{key}' nicht gefunden in {list(row.keys())}")


SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, description TEXT);
CREATE TABLE IF NOT EXISTS classes (id TEXT PRIMARY KEY, group_id TEXT, description TEXT, version TEXT);
CREATE TABLE IF NOT EXISTS synonyms (class_id TEXT, synonym TEXT);
CREATE TABLE IF NOT EXISTS features (id TEXT PRIMARY KEY, description TEXT);
CREATE TABLE IF NOT EXISTS units (id TEXT PRIMARY KEY, description TEXT);
CREATE TABLE IF NOT EXISTS values_ (id TEXT PRIMARY KEY, description TEXT);
CREATE TABLE IF NOT EXISTS class_features (cf_nr TEXT PRIMARY KEY, class_id TEXT, feature_id TEXT, type TEXT, unit_id TEXT, sort INTEGER);
CREATE TABLE IF NOT EXISTS class_feature_values (cf_nr TEXT, value_id TEXT, sort INTEGER);
CREATE INDEX IF NOT EXISTS idx_cf_class ON class_features(class_id);
CREATE INDEX IF NOT EXISTS idx_cfv ON class_feature_values(cf_nr);
CREATE INDEX IF NOT EXISTS idx_syn ON synonyms(class_id);
"""


def build_sqlite(folder: Path, db_path: Path | None = None) -> Path:
    db_path = db_path or (config.CACHE / "etim.sqlite")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tables = find_tables(folder)
    missing = [k for k in ("class", "feature", "value", "classfeature", "classfeaturevalue") if k not in tables]
    if missing:
        raise SystemExit(f"ETIM-Tabellen fehlen: {missing}. `python -m etim inspect {folder}` ausführen und Aliase anpassen.")
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    def load(key, sql, mapper):
        if key not in tables:
            return 0
        _, rows = read_csv(tables[key])
        data = [mapper(r) for r in rows]
        con.executemany(sql, data)
        return len(data)

    n = {}
    n["groups"] = load("group", "INSERT OR REPLACE INTO groups VALUES (?,?)", lambda r: (pick(r, "group_id"), pick(r, "group_desc")))
    n["classes"] = load(
        "class",
        "INSERT OR REPLACE INTO classes VALUES (?,?,?,?)",
        lambda r: (pick(r, "class_id"), _opt(r, "group_id"), pick(r, "class_desc"), _opt(r, "class_version")),
    )
    n["synonyms"] = load("synonym", "INSERT INTO synonyms VALUES (?,?)", lambda r: (pick(r, "class_id"), pick(r, "synonym")))
    n["features"] = load("feature", "INSERT OR REPLACE INTO features VALUES (?,?)", lambda r: (pick(r, "feature_id"), pick(r, "feature_desc")))
    n["units"] = load("unit", "INSERT OR REPLACE INTO units VALUES (?,?)", lambda r: (pick(r, "unit_id"), pick(r, "unit_desc")))
    n["values"] = load("value", "INSERT OR REPLACE INTO values_ VALUES (?,?)", lambda r: (pick(r, "value_id"), pick(r, "value_desc")))
    n["class_features"] = load(
        "classfeature",
        "INSERT OR REPLACE INTO class_features VALUES (?,?,?,?,?,?)",
        lambda r: (
            pick(r, "cf_nr"),
            pick(r, "class_id"),
            pick(r, "feature_id"),
            _opt(r, "feature_type"),
            _opt(r, "unit_id"),
            _int(_opt(r, "sort")),
        ),
    )
    n["class_feature_values"] = load(
        "classfeaturevalue",
        "INSERT INTO class_feature_values VALUES (?,?,?)",
        lambda r: (pick(r, "cfv_cf_nr"), pick(r, "value_id"), _int(_opt(r, "sort"))),
    )
    con.commit()
    con.close()
    print("ETIM geladen:", ", ".join(f"{k}={v}" for k, v in n.items()))
    return db_path


def _opt(row, key):
    try:
        return pick(row, key)
    except KeyError:
        return ""


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


@dataclass
class ClassFeature:
    cf_nr: str
    feature_id: str
    feature_desc: str
    type: str  # A=alphanumeric (Werteliste), N=numeric, L=logical, R=range
    unit_id: str
    unit_desc: str
    sort: int
    values: list[tuple[str, str]]  # (EV-Code, Text)


class EtimModel:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (config.CACHE / "etim.sqlite")
        if not self.db_path.exists():
            raise SystemExit(f"{self.db_path} fehlt — zuerst `python -m etim load-model data/etim`")
        self.con = sqlite3.connect(self.db_path)
        self.con.row_factory = sqlite3.Row

    def classes(self) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT c.id, c.description, c.group_id, g.description AS group_desc FROM classes c LEFT JOIN groups g ON g.id=c.group_id ORDER BY c.id"
        ).fetchall()

    def class_text(self, class_id: str) -> str:
        """Text, der für Embedding und LLM-Kandidatenliste steht."""
        c = self.con.execute(
            "SELECT c.description, g.description AS group_desc FROM classes c LEFT JOIN groups g ON g.id=c.group_id WHERE c.id=?",
            (class_id,),
        ).fetchone()
        syn = [r[0] for r in self.con.execute("SELECT synonym FROM synonyms WHERE class_id=?", (class_id,))]
        feats = [r[0] for r in self.con.execute(
            "SELECT f.description FROM class_features cf JOIN features f ON f.id=cf.feature_id WHERE cf.class_id=? ORDER BY cf.sort LIMIT 8",
            (class_id,),
        )]
        parts = [f"{class_id}: {c['description']}"]
        if c["group_desc"]:
            parts.append(f"group: {c['group_desc']}")
        if syn:
            parts.append("synonyms: " + ", ".join(syn[:15]))
        if feats:
            parts.append("features: " + ", ".join(feats))
        return " | ".join(parts)

    def class_desc(self, class_id: str) -> str:
        r = self.con.execute("SELECT description FROM classes WHERE id=?", (class_id,)).fetchone()
        return r[0] if r else ""

    def features_for(self, class_id: str) -> list[ClassFeature]:
        rows = self.con.execute(
            """SELECT cf.cf_nr, cf.feature_id, f.description AS fdesc, cf.type, cf.unit_id, COALESCE(u.description,'') AS udesc, cf.sort
               FROM class_features cf JOIN features f ON f.id=cf.feature_id LEFT JOIN units u ON u.id=cf.unit_id
               WHERE cf.class_id=? ORDER BY cf.sort""",
            (class_id,),
        ).fetchall()
        out = []
        for r in rows:
            vals = [
                (v["id"], v["description"])
                for v in self.con.execute(
                    "SELECT v.id, v.description FROM class_feature_values cfv JOIN values_ v ON v.id=cfv.value_id WHERE cfv.cf_nr=? ORDER BY cfv.sort",
                    (r["cf_nr"],),
                )
            ]
            out.append(ClassFeature(r["cf_nr"], r["feature_id"], r["fdesc"], (r["type"] or "").upper()[:1], r["unit_id"] or "", r["udesc"], r["sort"], vals))
        return out

    def count(self) -> dict[str, int]:
        return {t: self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("classes", "features", "values_", "class_features")}


def inspect_folder(folder: Path) -> None:
    files = [p for p in sorted(folder.rglob("*")) if p.is_file()]
    if not files:
        print(f"Keine Dateien in {folder}")
        return
    print(f"{len(files)} Dateien in {folder}:")
    for p in files:
        line = f"  {p.relative_to(folder)}  ({p.stat().st_size // 1024} KB)"
        if p.suffix.lower() in (".csv", ".txt"):
            try:
                cols, rows = read_csv(p)
                line += f"\n      Spalten: {cols}\n      Zeilen: {len(rows)}"
                if rows:
                    line += f"\n      Beispiel: {rows[0]}"
            except Exception as e:  # noqa: BLE001
                line += f"  (nicht lesbar: {e})"
        print(line)
    found = find_tables(folder)
    print("\nErkannt:", {k: v.name for k, v in found.items()})
    missing = [k for k in TABLE_ALIASES if k not in found]
    if missing:
        print("Nicht erkannt:", missing, "→ TABLE_ALIASES in etim/model.py ergänzen")
