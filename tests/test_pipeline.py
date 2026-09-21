import json
from pathlib import Path

from lxml import etree

from etim import classify, config, export_bmecat, features, ingest, report, validate
from etim.export_bmecat import NS

FIX = Path(__file__).parent / "fixtures"


def test_model_loads(model):
    c = model.count()
    assert c["classes"] == 6 and c["features"] == 7
    feats = model.features_for("EC000001")
    assert [f.feature_id for f in feats] == ["EF000001", "EF000005", "EF000003", "EF000004"]
    assert feats[1].type == "R" and feats[1].unit_desc == "mm"
    assert ("EV000011", "M8/M10") in feats[2].values
    assert "Rohrschelle" in model.class_text("EC000001")


def test_full_pipeline(model, tmp_path):
    out = tmp_path / "job"
    products = ingest.run(FIX / "katalog_mini.csv", out)
    assert len(products) == 4
    assert products[0].supplier_pid == "RS-2025-M8" and products[0].gtin == "4012345678901"
    assert any(a.name == "Spannbereich" for a in products[0].attributes)

    classified = classify.run(out, model)
    by_pid = {c.product.supplier_pid: c for c in classified}
    assert by_pid["RS-2025-M8"].decision.class_id == "EC000001"
    assert by_pid["KH-20"].decision.class_id == "EC000003"
    assert by_pid["GE-RS-20"].decision.class_id == "EC000006"

    enriched = features.run(out, model)
    e = {x.product.supplier_pid: x for x in enriched}["RS-2025-M8"]
    vals = {f.feature_id: f for f in e.features}
    assert vals["EF000001"].value == "EV000001" and vals["EF000001"].source
    assert vals["EF000005"].value == "20" and vals["EF000005"].value_max == "25"
    assert vals["EF000004"].value == "true"
    assert not e.needs_review
    # Gummieinlage: Material nur mit 0.6 -> Review
    assert {x.product.supplier_pid: x for x in enriched}["GE-RS-20"].needs_review

    xml = export_bmecat.run(out, "Muster GmbH", "4012345000006")
    tree = etree.parse(str(xml))
    prods = tree.getroot().findall(f".//{{{NS}}}PRODUCT")
    assert len(prods) == 3  # GE-RS-20 im Review, nicht exportiert
    p0 = prods[0]
    assert p0.findtext(f"{{{NS}}}SUPPLIER_PID") == "RS-2025-M8"
    pf = p0.find(f"{{{NS}}}PRODUCT_FEATURES")
    assert pf.findtext(f"{{{NS}}}REFERENCE_FEATURE_SYSTEM_NAME") == config.ETIM_VERSION
    assert pf.findtext(f"{{{NS}}}REFERENCE_FEATURE_GROUP_ID") == "EC000001"
    rng = [f for f in pf.findall(f"{{{NS}}}FEATURE") if f.findtext(f"{{{NS}}}FNAME") == "EF000005"][0]
    assert [v.text for v in rng.findall(f"{{{NS}}}FVALUE")] == ["20", "25"]
    assert rng.findtext(f"{{{NS}}}FUNIT") == "EU570001"

    xml_all = export_bmecat.run(out, "Muster GmbH", include_review=True)
    assert len(etree.parse(str(xml_all)).getroot().findall(f".//{{{NS}}}PRODUCT")) == 4

    res = validate.run(out)
    errors = [i for i in res["issues"] if i["level"] == "error"]
    assert not errors, errors
    assert any("GTIN" in i["msg"] for i in res["issues"])  # GE-RS-20 ohne GTIN -> Warnung

    rep = report.run(out, "Muster GmbH")
    text = rep.read_text()
    assert "Artikel erkannt: **4**" in text and "EC000001" in text
    rows = (out / "review.csv").read_text().splitlines()
    assert len(rows) >= 2 and "GE-RS-20" in rows[1]


def test_gtin_check():
    assert validate.gtin_ok("4012345678901")
    assert not validate.gtin_ok("4012345678902")
    assert not validate.gtin_ok("abc")


def test_products_json_roundtrip(tmp_path):
    out = tmp_path / "j"
    ingest.run(FIX / "katalog_mini.csv", out)
    data = json.loads((out / "products.json").read_text())
    assert data["products"][2]["name"].startswith("Kugelhahn")


def test_min_coverage_forces_review(model, tmp_path, monkeypatch):
    """Ein Artikel, bei dem fast nichts befüllt wurde, muss in die Review-Queue —
    auch wenn die wenigen befüllten Werte über der Konfidenzschwelle liegen."""
    out = tmp_path / "cov"
    ingest.run(FIX / "katalog_mini.csv", out)
    classify.run(out, model)

    monkeypatch.setattr(config, "MIN_COVERAGE", 0.0)
    by_pid = {e.product.supplier_pid: e for e in features.run(out, model)}
    ref = by_pid["KH-20"]
    assert not ref.needs_review and 0 < ref.coverage < 0.9

    monkeypatch.setattr(config, "MIN_COVERAGE", 0.99)
    by_pid = {e.product.supplier_pid: e for e in features.run(out, model)}
    assert by_pid["KH-20"].needs_review

    # der Artikel muss auch in Davids Arbeitsliste auftauchen, nicht nur im Flag
    validate_free = report.run(out, "Muster GmbH")
    assert "der Merkmale befüllt" in validate_free.read_text()
    csv_rows = (out / "review.csv").read_text()
    assert "ABDECKUNG" in csv_rows and "KH-20" in csv_rows

    # und er darf nicht im Standard-Export landen
    xml = export_bmecat.run(out, "Muster GmbH")
    pids = [p.findtext(f"{{{NS}}}SUPPLIER_PID") for p in etree.parse(str(xml)).getroot().findall(f".//{{{NS}}}PRODUCT")]
    assert "KH-20" not in pids


def _write_products(out: Path, rows: list[tuple[str, str]]):
    out.mkdir(parents=True, exist_ok=True)
    (out / "products.json").write_text(json.dumps({
        "products": [{"supplier_pid": pid, "name": name, "description": "", "attributes": []} for pid, name in rows],
        "notes": "",
    }, ensure_ascii=False))


def test_base_name_splits_variants():
    from etim.classify import base_name, group_variants, representative
    from etim.schemas import Product

    assert base_name("FBR-Regelgruppe 130/6 mit Grundfos UPM3 Auto 15-50 130") == "FBR-Regelgruppe 130/6"
    assert base_name("Kugelhahn DN 20 IG/IG") == "Kugelhahn DN 20 IG/IG"          # kein ' mit '
    assert base_name("Rohrschelle 20-25, mit Gummieinlage") == "Rohrschelle 20-25"  # Komma weg
    assert base_name("Rohrschelle Mit Einlage") == "Rohrschelle"                    # Groß-/Kleinschreibung egal
    assert base_name("Dämmung mit Nut") == "Dämmung"
    assert base_name("mit Pumpe") == "mit Pumpe"        # ' mit ' am Anfang trennt keine Variante ab
    assert "Schallschutz" not in base_name("Schelle mit Schallschutz mit Zubehör")  # nur der erste Treffer zählt
    # Englische Herstellerlisten müssen genauso gruppieren (CLAUDE.md: DE/EN gleich gut)
    assert base_name("Pipe clamp 20-25 with rubber inlay") == "Pipe clamp 20-25"
    assert base_name("Ball valve DN 20") == "Ball valve DN 20"

    ps = [Product(supplier_pid=str(i), name=n) for i, n in enumerate(
        ["Gruppe A mit Pumpe X", "Gruppe A mit Pumpe Y", "Gruppe B", "gruppe a  mit  Pumpe Z"])]
    assert group_variants(ps) == [("gruppe a", [0, 1, 3]), ("gruppe b", [2])]
    assert representative(ps[0]).name == "Gruppe A"       # Komponentenrauschen raus aus Query und Prompt
    assert representative(ps[2]) is ps[2]                 # ohne ' mit ' unveraendert


def test_variants_get_one_decision(model, tmp_path):
    """Vier Varianten derselben Baugruppe: ein LLM-Aufruf, vier identische Ergebnisse."""
    from etim import llm

    out = tmp_path / "var"
    _write_products(out, [
        ("P-1", "Rohrschelle 20-25 mm M8/M10 mit Grundfos UPM3 Auto 15-50"),
        ("P-2", "Rohrschelle 20-25 mm M8/M10 mit Wilo Para 15-130"),
        ("P-3", "Rohrschelle 20-25 mm M8/M10 mit Halmoor HEP 15-50"),
        ("P-4", "Rohrschelle 20-25 mm M8/M10 mit Grundfos UPM4"),
        ("K-1", "Kugelhahn DN 20 IG/IG"),
    ])

    calls = []
    inner = llm._fake_handlers["decide"]
    llm.register_fake("decide", lambda p: (calls.append(p), inner(p))[1])
    try:
        res = classify.run(out, model, fresh=True)
    finally:
        llm.register_fake("decide", inner)

    assert len(calls) == 2, "eine Entscheidung je Gruppe, nicht je Artikel"
    assert "Bezeichnung: Rohrschelle 20-25 mm M8/M10\n" in calls[0], "Prompt bekommt den Basisnamen"

    variants = [r for r in res if r.product.supplier_pid.startswith("P-")]
    assert len({v.decision.class_id for v in variants}) == 1
    assert variants[0].decision.class_id == "EC000001"
    assert len({(v.decision.confidence, v.needs_review) for v in variants}) == 1
    assert [v.variant_group for v in variants] == ["rohrschelle 20-25 mm m8/m10"] * 4
    assert [v.variant_of for v in variants] == [None, "P-1", "P-1", "P-1"]
    # Der Einzelartikel bleibt ungruppiert und behaelt seine eigene Entscheidung
    solo = [r for r in res if r.product.supplier_pid == "K-1"][0]
    assert solo.variant_group is None and solo.decision.class_id == "EC000003"
    # Reihenfolge aus products.json bleibt erhalten (export/features verlassen sich darauf)
    assert [r.product.supplier_pid for r in res] == ["P-1", "P-2", "P-3", "P-4", "K-1"]
    # und die Entscheidungen sind eigene Objekte, nicht dieselbe Instanz
    variants[0].decision.confidence = 0.01
    assert variants[1].decision.confidence != 0.01


def test_variants_get_one_decision_with_jev(model, tmp_path):
    """Dieselbe Zusage fuer den Jev-Pfad — er laeuft durch denselben run()."""
    out = tmp_path / "varjev"
    _write_products(out, [
        ("P-1", "Rohrschelle 20-25 mm M8/M10 mit Grundfos UPM3"),
        ("P-2", "Rohrschelle 20-25 mm M8/M10 mit Wilo Para"),
    ])
    res = classify.run(out, model, classifier="jev", fresh=True)
    assert len({r.decision.class_id for r in res}) == 1
    assert [r.variant_of for r in res] == [None, "P-1"]
    assert all(r.model == "jev" for r in res)


def test_check_reasoning_codes(model):
    """Der Kern: erfunden -> markiert, echt-aber-nicht-angeboten -> Gegenbeleg, angeboten -> unberuehrt."""
    from etim.classify import check_reasoning_codes
    from etim.schemas import ClassCandidate, ClassDecision

    cands = [ClassCandidate(class_id="EC000001", description="Rohrschelle", score=0.5)]
    d = ClassDecision(
        class_id="EC000001",
        confidence=0.9,
        reasoning="EC000001 passt; nicht EC000006, und EC010091 gibt es gar nicht.",
        runner_up="EC000006",
    )
    assert check_reasoning_codes(model, d, cands) == ["EC010091"]
    assert d.class_id == "EC000001" and d.runner_up == "EC000006"  # beide existieren -> bleiben
    assert d.reasoning.startswith("EC000001 passt")                 # Kandidat bleibt unkommentiert
    assert 'EC000006 ("Accessories for pipe clamp")' in d.reasoning, "echter Code ausserhalb der Kandidaten bekommt seinen wahren Text"
    assert "EC010091 [existiert nicht in ETIM-10.0]" in d.reasoning
    assert "verworfen" not in d.reasoning


def test_invented_ec_codes_are_flagged(model, tmp_path):
    """Erfundene EC-Codes duerfen weder als Klasse noch als Befund stehen bleiben."""
    from etim import llm

    out = tmp_path / "ec"
    _write_products(out, [("X-1", "Irgendwas")])

    inner = llm._fake_handlers["decide"]
    llm.register_fake("decide", lambda p: {
        "class_id": "EC999999",
        "confidence": 0.95,
        "reasoning": "Keine passt; eigentlich waere es EC010091, Rest siehe EC999998.",
        "runner_up": "EC888888",
    })
    try:
        r = classify.run(out, model, fresh=True)[0]
    finally:
        llm.register_fake("decide", inner)

    d = r.decision
    assert d.class_id is None, "erfundener Code darf nicht als Klasse durchgehen"
    assert d.runner_up is None
    assert set(r.invented_codes) == {"EC999999", "EC888888", "EC010091", "EC999998"}
    assert "EC010091 [existiert nicht in" in d.reasoning and "EC999998 [existiert nicht in" in d.reasoning
    # die Verworfen-Notiz darf nicht ihrerseits annotiert werden
    assert d.reasoning.endswith("[gewaehlter Code EC999999 existiert nicht in ETIM-10.0 — verworfen]")
    assert r.needs_review and d.confidence <= 0.5
