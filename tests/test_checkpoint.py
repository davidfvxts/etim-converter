"""Zwischenstand und Abbruch.

Ein Lauf ueber 250 Artikel dauert ueber eine Stunde und kostet Geld. Die Zusage
ist: ein Abbruch verliert nichts, und der naechste Lauf fragt nicht noch einmal,
was schon bezahlt ist.
"""
import json
from pathlib import Path

import pytest

from etim import checkpoint, classify, config, ingest, studio, versions
from etim.model import EtimModel, build_sqlite

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    build_sqlite(FIX / "etim_mini", versions.db_path("10.0"), "10.0")
    out = tmp_path / "job"
    ingest.run(FIX / "katalog_mini.csv", out)
    return out, EtimModel(versions.db_path("10.0"), "10.0")


def test_a_finished_run_leaves_no_checkpoint(job):
    """Der Zwischenstand ist Arbeitsmaterial, kein Ergebnis."""
    out, model = job
    classify.run(out, model, classifier="gemini")
    assert not list(out.glob(".checkpoint.*")), "Zwischenstand nach Erfolg nicht aufgeräumt"
    assert (out / "classified.json").exists()


def test_an_interrupted_run_keeps_what_was_paid_for(job, monkeypatch):
    """Nach einem Abbruch muss der zweite Lauf die fertigen Artikel überspringen."""
    out, model = job
    gefragt = []
    echtes_decide = classify.decide

    def zaehlend(model_, p, cands):
        gefragt.append(p.supplier_pid)
        if len(gefragt) == 3:
            raise KeyboardInterrupt("Nutzer bricht ab")
        return echtes_decide(model_, p, cands)

    monkeypatch.setattr(checkpoint, "EVERY", 1)   # jeder Artikel sofort auf die Platte
    monkeypatch.setattr(classify, "decide", zaehlend)
    with pytest.raises(KeyboardInterrupt):
        classify.run(out, model, classifier="gemini")

    cp_datei = checkpoint.path(out, "classify")
    assert cp_datei.exists(), "nichts gesichert — der Abbruch hätte alles gekostet"
    gesichert = json.loads(cp_datei.read_text())["done"]
    assert len(gesichert) == 2, f"erwartet 2 gesicherte Artikel, waren {len(gesichert)}"

    # Zweiter Anlauf: die zwei fertigen werden nicht erneut gefragt.
    monkeypatch.setattr(classify, "decide", echtes_decide)
    gefragt.clear()
    rows = classify.run(out, model, classifier="gemini")
    assert len(rows) == 4, "am Ende müssen alle Artikel da sein"
    assert len(gefragt) == 0 or set(gesichert).isdisjoint(gefragt), \
        "ein bereits bezahlter Artikel wurde noch einmal gefragt"
    # Und die Reihenfolge ist die des Katalogs, nicht die des Zwischenstands.
    products = json.loads((out / "products.json").read_text())["products"]
    assert [r.product.supplier_pid for r in rows] == [p["supplier_pid"] for p in products]


def test_changed_settings_discard_the_checkpoint(job, monkeypatch):
    """Ein Zwischenstand aus anderen Einstellungen darf nicht weiterverwendet werden."""
    out, model = job
    monkeypatch.setattr(checkpoint, "EVERY", 1)
    classify.run(out, model, classifier="gemini")

    # Zwischenstand von Hand wiederherstellen, aber mit anderem Modell
    cp = checkpoint.Checkpoint(out, "classify", {"classifier": "jev", "etim_version": "10.0",
                                                 "top_k": 254, "dry_run": True, "n": 4})
    cp.add("RS-2025-M8", {"irgendwas": True})
    cp.save()

    frisch = checkpoint.Checkpoint(out, "classify", {"classifier": "gemini", "etim_version": "10.0",
                                                     "top_k": 20, "dry_run": True, "n": 4})
    assert frisch.load() == 0, "Zwischenstand aus anderen Einstellungen wurde übernommen"


def test_fresh_discards_the_checkpoint(job, monkeypatch):
    out, model = job
    monkeypatch.setattr(checkpoint, "EVERY", 1)
    cp = checkpoint.Checkpoint(out, "classify", {"classifier": "gemini", "etim_version": "10.0",
                                                 "top_k": 20, "dry_run": True, "n": 4})
    cp.add("RS-2025-M8", {"alt": True})
    cp.save()

    gefragt = []
    echtes = classify.decide
    monkeypatch.setattr(classify, "decide",
                        lambda m, p, c: gefragt.append(p.supplier_pid) or echtes(m, p, c))
    classify.run(out, model, classifier="gemini", fresh=True)
    assert "RS-2025-M8" in gefragt, "--fresh hat den alten Zwischenstand nicht verworfen"


def test_saving_is_atomic(tmp_path):
    """Ein Abbruch mitten im Schreiben darf den Zwischenstand nicht zerstören."""
    cp = checkpoint.Checkpoint(tmp_path, "classify", {"a": 1})
    cp.add("X", {"ok": True})
    cp.save()
    gut = cp.file.read_bytes()

    kaputt = checkpoint.Checkpoint(tmp_path, "classify", {"a": 1})
    kaputt.done = {"Y": {"nicht": "serialisierbar", "obj": object()}}
    with pytest.raises(TypeError):
        kaputt.save()
    assert cp.file.read_bytes() == gut, "die alte Fassung wurde beschädigt"
    assert not list(tmp_path.glob("*.tmp")), "Zwischendatei liegengelassen"


def test_cancel_stops_between_articles(tmp_path):
    """Abbruch wirkt zwischen zwei Artikeln — der laufende wird zu Ende gebracht."""
    job_dir = tmp_path / "läuft"
    job_dir.mkdir()
    st = studio.RunState(job="läuft", kind="compare", state="running")
    studio.RUNS["läuft"] = st
    try:
        assert studio.cancel("läuft")["cancel_requested"] is True
        assert st.cancel is True
        # Kein zweiter Abbruch auf einem Lauf, der gar nicht läuft.
        st.state = "done"
        with pytest.raises(studio.UploadError, match="kein Lauf"):
            studio.cancel("läuft")
    finally:
        studio.RUNS.pop("läuft", None)


def test_interrupted_run_with_variants(tmp_path, monkeypatch):
    """Abbruch bei gruppierten Varianten: keine halbe Gruppe, keine Doppelfrage.

    Seit der Variantengruppierung entscheidet ein Aufruf ueber mehrere Artikel.
    Ein Abbruch darf weder eine Gruppe halb gefuellt zuruecklassen noch die schon
    bezahlte Gruppe im zweiten Anlauf erneut fragen.
    """
    monkeypatch.setattr(config, "CACHE", tmp_path / "cache")
    (tmp_path / "cache").mkdir()
    build_sqlite(FIX / "etim_mini", versions.db_path("10.0"), "10.0")
    model = EtimModel(versions.db_path("10.0"), "10.0")
    out = tmp_path / "job"
    out.mkdir()
    (out / "products.json").write_text(json.dumps({"products": [
        {"supplier_pid": "A-1", "name": "Rohrschelle 20-25 mit Pumpe X", "attributes": []},
        {"supplier_pid": "A-2", "name": "Rohrschelle 20-25 mit Pumpe Y", "attributes": []},
        {"supplier_pid": "A-3", "name": "Rohrschelle 20-25 mit Pumpe Z", "attributes": []},
        {"supplier_pid": "B-1", "name": "Kugelhahn DN 20", "attributes": []},
    ], "notes": ""}, ensure_ascii=False))

    gefragt = []
    echtes_decide = classify.decide

    def zaehlend(model_, p, cands):
        gefragt.append(p.name)
        if len(gefragt) == 2:                      # nach der ersten Gruppe abbrechen
            raise KeyboardInterrupt("Nutzer bricht ab")
        return echtes_decide(model_, p, cands)

    monkeypatch.setattr(checkpoint, "EVERY", 1)
    monkeypatch.setattr(classify, "decide", zaehlend)
    with pytest.raises(KeyboardInterrupt):
        classify.run(out, model, classifier="gemini")

    gesichert = json.loads(checkpoint.path(out, "classify").read_text())["done"]
    assert set(gesichert) == {"A-1", "A-2", "A-3"}, "eine Gruppe wird ganz oder gar nicht gesichert"

    def nur_zaehlend(model_, p, cands):
        gefragt.append(p.name)
        return echtes_decide(model_, p, cands)

    monkeypatch.setattr(classify, "decide", nur_zaehlend)
    gefragt.clear()
    rows = classify.run(out, model, classifier="gemini")
    assert gefragt == ["Kugelhahn DN 20"], "nur die offene Gruppe wird gefragt"
    assert [r.product.supplier_pid for r in rows] == ["A-1", "A-2", "A-3", "B-1"]
    varianten = [r for r in rows if r.product.supplier_pid.startswith("A-")]
    assert len({v.decision.class_id for v in varianten}) == 1, "auch ueber den Abbruch hinweg eine Klasse"
    assert [v.variant_of for v in varianten] == [None, "A-1", "A-1"]
    assert not list(out.glob(".checkpoint.*"))
