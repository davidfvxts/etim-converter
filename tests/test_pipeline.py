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
