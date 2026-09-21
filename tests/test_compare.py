"""Modellvergleich Gemini gegen Jev.

Geprüft wird der Auswertungspfad, nicht die Modellqualität: dass die
Retrieval-Liste bei beiden ankommt, dass erfundene EC-Codes auffallen, dass
ohne Referenz keine Trefferquote entsteht und dass ein von Jev gewählter Wert
ohne Katalogbeleg nicht exportfähig wird.
"""
import json
from pathlib import Path

import pytest

from etim import classify, compare, config, ingest, jev
from etim.schemas import ClassCandidate, ComparisonItem, ModelAnswer, Product

FIX = Path(__file__).parent / "fixtures"


def test_base_name_groups_variants():
    """Varianten desselben Produkts müssen auf dieselbe Basisbezeichnung fallen."""
    a = compare.base_name("FBR-Regelgruppe 130/6 mit Grundfos UPM3 Auto 15-50 130")
    b = compare.base_name("FBR-Regelgruppe 130/6 mit Wilo Para 15-130/7")
    assert a == b == "fbr-regelgruppe 130/6"
    assert compare.base_name("Kugelhahn DN 20") != a
    assert compare.base_name("Pumpe inkl. Dämmschale") == "pumpe"


def test_reasoning_codes_flag_invented(model):
    """Ein EC-Code, den es nicht gibt, muss als unbelegt markiert werden."""
    codes = compare.reasoning_codes(model, "Passender wäre EC000001, nicht EC999999.")
    by_code = {c.code: c for c in codes}
    assert by_code["EC000001"].known and by_code["EC000001"].desc
    assert not by_code["EC999999"].known
    assert compare.reasoning_codes(model, "") == []


def test_class_criteria_structure_and_budget(model):
    """Optionsbeschreibungen trennen Hauptprodukt und Zubehör — und passen ins Fenster."""
    cands = [ClassCandidate(class_id=c["id"], description=c["description"], score=0.5)
             for c in model.classes()]
    crit, trim, n = compare.class_criteria(model, cands, budget=24_000)

    assert compare.NONE_OPTION in crit, "ohne 'keine passt' kann Jev nichts ablehnen"
    assert n == len(cands) and trim == "voll"
    # EC000006 ist die Zubehörklasse der Fixture, EC000001 das Hauptprodukt.
    assert "not_for" in crit["EC000006"] and "not the complete product" in crit["EC000006"]["not_for"].lower()
    assert "not_for" in crit["EC000001"] and "accessor" in crit["EC000001"]["not_for"].lower()
    assert crit["EC000001"]["what"] == model.class_desc("EC000001")

    # Enges Budget kürzt die Beschreibungen, statt das Feld stillschweigend zu leeren.
    tight, trim2, n2 = compare.class_criteria(model, cands, budget=120)
    assert n2 >= 1 and trim2 != "voll" and compare.NONE_OPTION in tight


def test_class_criteria_respects_option_limit(model):
    """Mehr Optionen als Jev erlaubt darf gar nicht erst abgeschickt werden."""
    with pytest.raises(ValueError, match="255"):
        jev.choice("x", {f"EC{i:06d}": "y" for i in range(300)})


def test_compare_run_without_reference(model, tmp_path):
    """Ohne reference.json: keine Trefferquote, aber Konsistenz und Codeprüfung."""
    out = tmp_path / "cmp"
    ingest.run(FIX / "katalog_mini.csv", out)
    comp = compare.run(out, model)

    assert len(comp.items) == 4
    assert set(comp.metrics) == {"gemini", "jev"}
    assert comp.has_reference is False
    for m in comp.metrics.values():
        assert m.hit_rate is None and m.hits is None, "ohne Referenz darf es keine Quote geben"
        assert m.simulated is True, "im Trockenlauf muss jede Zahl als simuliert gelten"
    assert any("keine reference.json" in n.lower() for n in comp.notes)
    assert any("simuliert" in n.lower() for n in comp.notes)

    # beide Modelle haben denselben Artikel gesehen
    it = comp.items[0]
    assert set(it.answers) == {"gemini", "jev"}
    assert it.answers["gemini"].candidates_seen <= config.TOP_K_CLASSES
    assert it.answers["jev"].candidates_seen >= it.answers["gemini"].candidates_seen
    assert it.answers["jev"].is_accessory is not None, "die Zubehörfrage muss mitlaufen"
    assert it.answers["jev"].reasoning == "", "Jev erzeugt keinen Text"

    saved = json.loads((out / "compare.json").read_text())
    assert saved["job"] == "cmp" and len(saved["items"]) == 4


def test_reference_skeleton_leaves_the_decision_open(tmp_path):
    """Das Gerüst nimmt das Abtippen ab, nicht die Entscheidung."""
    out = tmp_path / "skel"
    products = ingest.run(FIX / "katalog_mini.csv", out)

    target = compare.reference_skeleton(out)
    data = json.loads(target.read_text())

    pids = [p.supplier_pid for p in products]
    assert sorted(data["reference"]) == sorted(pids)
    assert set(data["reference"].values()) == {""}, "keine vorbelegten Klassen"
    assert data["_artikel"][pids[0]] == products[0].name, "Bezeichnung als Lesehilfe"

    # Ein leeres Gerüst ergibt keine Referenz — und damit keine Trefferquote.
    assert compare.load_reference(out) == {}

    # Teilweise ausgefüllt: nur die gefüllten Zeilen zählen.
    data["reference"][pids[0]] = "EC000001"
    target.write_text(json.dumps(data))
    assert compare.load_reference(out) == {pids[0]: "EC000001"}

    # Eine bestehende Datei wird nie überschrieben.
    with pytest.raises(FileExistsError):
        compare.reference_skeleton(out)


def test_compare_run_with_reference(model, tmp_path):
    """Mit Referenz entstehen Trefferquote, Kandidatenfenster und Konfidenzsplit."""
    out = tmp_path / "cmpref"
    ingest.run(FIX / "katalog_mini.csv", out)
    (out / "reference.json").write_text(json.dumps({
        "RS-2025-M8": "EC000001", "KH-20": "EC000003", "GE-RS-20": "EC000006",
    }))
    comp = compare.run(out, model)

    assert comp.has_reference and comp.n_reference == 3
    for key, m in comp.metrics.items():
        assert m.hits is not None and 0 <= m.hit_rate <= 1, key
        assert m.reference_in_window == 3, "die Fixture hat nur 6 Klassen — alle im Fenster"
    # Der Rang der Referenzklasse wird über die ganze Liste bestimmt, nicht nur Top-K.
    ranks = [it.reference_rank for it in comp.items if it.reference_class]
    assert len(ranks) == 3 and all(r and r >= 1 for r in ranks)


def test_compare_reuses_gemini(model, tmp_path):
    """--reuse-gemini fragt Gemini nicht erneut und sagt das im Ergebnis."""
    out = tmp_path / "reuse"
    ingest.run(FIX / "katalog_mini.csv", out)
    classify.run(out, model)
    comp = compare.run(out, model, reuse_gemini=True)

    assert comp.reused_gemini is True
    assert any("letzten lauf" in n.lower() for n in comp.notes)
    assert comp.metrics["gemini"].cost_usd == 0.0
    by_pid = {it.product.supplier_pid: it for it in comp.items}
    g = by_pid["RS-2025-M8"].answers["gemini"]
    assert g.class_id == "EC000001"
    # Der Rang muss aus dem frischen Retrieval kommen: classified.json speichert
    # nur die besten fünf Kandidaten, ein Rang daraus wäre eine andere Zahl.
    ranks = {c.class_id: i + 1 for i, c in enumerate(by_pid["RS-2025-M8"].retrieval)}
    assert g.rank_of_choice == ranks["EC000001"]


def test_jev_feature_value_needs_gemini_source(model, tmp_path):
    """Die Quellzitat-Pflicht gilt auch für Jev: ohne Beleg nicht exportfähig."""
    out = tmp_path / "feat"
    products = ingest.run(FIX / "katalog_mini.csv", out)
    p = {x.supplier_pid: x for x in products}["RS-2025-M8"]

    belegt = compare.compare_features(model, p, "EC000001", [
        {"feature_id": "EF000001", "value": "EV000001", "source": "Material=Stahl verzinkt", "confidence": 0.95},
    ])
    ohne = compare.compare_features(model, p, "EC000001", [])

    def by_id(fc, who):
        return {a.feature_id: a for a in fc.answers[who]}

    j_belegt = by_id(belegt, "jev")["EF000001"]
    j_ohne = by_id(ohne, "jev")["EF000001"]
    assert j_belegt.value is not None
    assert j_belegt.exportable and j_belegt.source_from == "gemini", "Beleg kommt von Gemini"
    assert j_ohne.value == j_belegt.value, "die Wahl selbst ändert sich nicht"
    assert not j_ohne.exportable and not j_ohne.source, "ohne Beleg geht der Wert ins Review"


def test_jev_skips_numeric_features(model, tmp_path):
    """Zahlen und Bereiche bleiben bei Gemini — mit nachlesbarer Begründung."""
    out = tmp_path / "skip"
    products = ingest.run(FIX / "katalog_mini.csv", out)
    p = products[0]
    fc = compare.compare_features(model, p, "EC000001", [])

    # EF000005 ist in der Fixture ein Bereichsmerkmal (Typ R).
    assert "EF000005" in fc.jev_skipped
    assert "numerisch" in fc.jev_skipped["EF000005"]
    asked = {a.feature_id for a in fc.answers["jev"]}
    assert "EF000005" not in asked
    types = {fid: fc.feature_meta[fid]["type"] for fid in asked}
    assert set(types.values()) <= {"A", "L"}, "Jev bekommt nur Wertelisten und logische Merkmale"


def test_metrics_variant_consistency():
    """Gleiche Basisbezeichnung, verschiedene Klassen → Gruppe zählt als inkonsistent."""
    def item(pid, name, cls):
        return ComparisonItem(
            product=Product(supplier_pid=pid, name=name), base_name=compare.base_name(name),
            answers={"jev": ModelAnswer(model="jev", class_id=cls, confidence=0.9)})

    items = [
        item("A", "Regelgruppe 130 mit Wilo", "EC000001"),
        item("B", "Regelgruppe 130 mit Grundfos", "EC000002"),
        item("C", "Kugelhahn DN 20", "EC000003"),
    ]
    m = compare.metrics(items, [], "jev", "Jev")
    assert m.variant_groups == 1 and m.variant_consistent == 0
    assert m.variant_consistency == 0.0
    assert m.hit_rate is None

    items[1].answers["jev"].class_id = "EC000001"
    m2 = compare.metrics(items, [], "jev", "Jev")
    assert m2.variant_consistent == 1 and m2.variant_consistency == 1.0


def test_jev_error_does_not_break_the_run(model, tmp_path, monkeypatch):
    """Ein Jev-Ausfall landet als Meldung am Artikel, nicht als Absturz des Laufs."""
    out = tmp_path / "err"
    ingest.run(FIX / "katalog_mini.csv", out)

    def boom(*a, **kw):
        raise jev.JevError("Jev lehnt die Zugangsdaten ab (401).")

    monkeypatch.setattr(jev, "ask", boom)
    comp = compare.run(out, model)

    assert len(comp.items) == 4
    assert comp.metrics["jev"].errors == 4
    assert comp.metrics["gemini"].errors == 0, "Gemini läuft weiter"
    assert all("401" in it.answers["jev"].error for it in comp.items)
    assert all(it.answers["jev"].class_id is None for it in comp.items)
