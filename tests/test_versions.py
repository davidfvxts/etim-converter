"""Mehrere ETIM-Versionen nebeneinander.

Die eigentliche Gefahr ist nicht, dass eine Version fehlt — das faellt auf.
Gefaehrlich ist das stille Vermischen: Klassen-IDs sind zwischen ETIM-Versionen
nicht stabil, ein geteilter Embedding-Cache oder eine Merkmalsliste aus der
falschen Version wuerde ohne Fehlermeldung falsche Daten erzeugen.
"""
import json
from pathlib import Path

import pytest

from etim import classify, config, features, ingest, versions
from etim.model import EtimModel, build_sqlite

FIX = Path(__file__).parent / "fixtures"


def test_normalize_and_label():
    for raw in ("8", "8.0", "ETIM-8.0", "etim 8", "ETIM8"):
        assert versions.normalize(raw) == "8.0", raw
    assert versions.label("9") == "ETIM-9.0"
    assert versions.normalize(None) == versions.default()


def test_each_version_gets_its_own_cache():
    """Datenbank und Embeddings duerfen sich zwischen Versionen nie teilen."""
    paths = {v: (versions.db_path(v), versions.emb_path(v)) for v in versions.SUPPORTED}
    alle = [str(p) for pair in paths.values() for p in pair]
    assert len(set(alle)) == len(alle), f"Pfade doppelt vergeben: {paths}"
    for v, (db, emb) in paths.items():
        if v != versions.default():
            assert v in db.name and v in emb.name


def test_legacy_cache_is_reused_for_the_default_version(tmp_path, monkeypatch):
    """Der Cache von vor der Versionsumstellung darf nicht wertlos werden."""
    monkeypatch.setattr(config, "CACHE", tmp_path)
    v = versions.default()
    (tmp_path / "etim.sqlite").write_bytes(b"alt")
    (tmp_path / "class_emb.npz").write_bytes(b"alt")
    assert versions.db_path(v).name == "etim.sqlite"
    assert versions.emb_path(v).name == "class_emb.npz"

    # Sobald es einen versionierten Cache gibt, gilt der.
    (tmp_path / f"etim-{v}.sqlite").write_bytes(b"neu")
    assert versions.db_path(v).name == f"etim-{v}.sqlite"
    # Eine andere Version greift nie auf den alten Cache zu.
    andere = next(x for x in versions.SUPPORTED if x != v)
    assert versions.db_path(andere).name == f"etim-{andere}.sqlite"


def test_status_names_what_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    st = versions.status("9.0")
    assert not st["ready"] and "data/etim/9.0" in st["missing"]

    (tmp_path / "data" / "etim" / "9.0").mkdir(parents=True)
    (tmp_path / "data" / "etim" / "9.0" / "ETIMARTCLASS.csv").write_text("x")
    st = versions.status("9.0")
    assert st["has_data"] and not st["ready"] and "load-model" in st["missing"]


def test_require_refuses_unknown_and_unbuilt(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE", tmp_path)
    with pytest.raises(SystemExit, match="nicht unterstuetzt"):
        versions.require("7.0")
    with pytest.raises(SystemExit, match="nicht einsatzbereit"):
        versions.require("9.0")


def test_classification_records_its_version(tmp_path, monkeypatch):
    """Die Version steht am Artikel — sonst weiss spaeter niemand, wogegen geprüft wurde."""
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    db = versions.db_path("9.0")
    build_sqlite(FIX / "etim_mini", db, "9.0")
    model = EtimModel(db, "9.0")
    assert model.version == "9.0"

    out = tmp_path / "job"
    ingest.run(FIX / "katalog_mini.csv", out)
    rows = classify.run(out, model, classifier="gemini")
    assert {r.etim_version for r in rows} == {"9.0"}
    saved = json.loads((out / "classified.json").read_text())
    assert saved[0]["etim_version"] == "9.0"

    # Die Merkmale erben die Version der Klassenentscheidung.
    enriched = features.run(out, model)
    assert {e.etim_version for e in enriched} == {"9.0"}


def test_features_refuse_a_different_version(tmp_path, monkeypatch):
    """Merkmale aus der falschen Version waeren still falsch — das muss abbrechen."""
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    build_sqlite(FIX / "etim_mini", versions.db_path("9.0"), "9.0")
    build_sqlite(FIX / "etim_mini", versions.db_path("8.0"), "8.0")

    out = tmp_path / "job"
    ingest.run(FIX / "katalog_mini.csv", out)
    classify.run(out, EtimModel(versions.db_path("9.0"), "9.0"), classifier="gemini")

    with pytest.raises(SystemExit, match="ETIM 9.0"):
        features.run(out, EtimModel(versions.db_path("8.0"), "8.0"))


def test_export_uses_the_job_version_not_the_setting(tmp_path, monkeypatch):
    """Ein gegen ETIM 9 klassifizierter Katalog darf sich nicht als ETIM 10 ausgeben."""
    from lxml import etree

    from etim import export_bmecat
    from etim.export_bmecat import NS

    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    build_sqlite(FIX / "etim_mini", versions.db_path("9.0"), "9.0")
    model = EtimModel(versions.db_path("9.0"), "9.0")

    out = tmp_path / "job"
    ingest.run(FIX / "katalog_mini.csv", out)
    classify.run(out, model, classifier="gemini")
    features.run(out, model)

    # Die Einstellung sagt etwas anderes als der Job — der Job gewinnt.
    monkeypatch.setattr(config, "ETIM_VERSION", "ETIM-10.0")
    xml = export_bmecat.run(out, "Muster GmbH", "4012345000006")
    root = etree.parse(str(xml)).getroot()
    assert "ETIM-9.0" in root.findtext(f".//{{{NS}}}CATALOG_NAME")
    systems = {e.text for e in root.findall(f".//{{{NS}}}REFERENCE_FEATURE_SYSTEM_NAME")}
    assert systems == {"ETIM-9.0"}
