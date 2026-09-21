"""Was zwischen Klasse und Export schiefgehen kann: unerreichbare Wertelisten,
erfundene Codes, falsche Einheiten. Jeder Fall einzeln, ohne echten API-Zugriff."""
import json
from pathlib import Path

from etim import classify, features, llm
from etim.schemas import ClassDecision, ClassCandidate, ClassifiedProduct, Product


def _classified(pid: str, name: str, class_id: str, **kw) -> ClassifiedProduct:
    return ClassifiedProduct(
        product=Product(supplier_pid=pid, name=name, **kw),
        candidates=[ClassCandidate(class_id=class_id, description="", score=1.0)],
        decision=ClassDecision(class_id=class_id, confidence=0.9, reasoning="x"),
        etim_version="10.0",
    )


def test_long_value_list_is_reachable(model, monkeypatch):
    """Der Kern von Punkt 3c: ein Wert jenseits der Kuerzungsgrenze muss waehlbar sein.

    Frueher zeigte der Prompt die ersten 60 Werte und schrieb '… (+N)' dahinter.
    Alles danach war unerreichbar: das Modell konnte den Code nicht nennen, und
    haette es ihn genannt, haette die Werteliste-Pruefung ihn verworfen. Der Wert
    war damit dauerhaft verloren, ohne dass es irgendwo auffiel.
    """
    f = model.features_for("EC000001")[0]          # Werteliste-Merkmal (Typ A)
    lang = [(f"EV{i:06d}", f"Material {i}") for i in range(1, 91)]
    ziel = lang[-1]                                 # letzter Wert, also hinter der Grenze
    monkeypatch.setattr(features, "VALUES_PER_FEATURE", 40)
    monkeypatch.setattr(f, "values", lang)
    monkeypatch.setattr(model, "features_for", lambda cid: [f] if cid == "EC000001" else [])

    gesehen = []

    def fake(prompt: str):
        gesehen.append(prompt)
        # Das Modell nennt den Zielcode nur, wenn er im Prompt steht.
        val = ziel[0] if ziel[0] in prompt else None
        return {"features": [{"feature_id": f.feature_id, "value": val, "value_max": None,
                              "source": "Katalog: Material 90" if val else None,
                              "confidence": 0.9 if val else 0.0,
                              "reason": None if val else "nicht in gezeigter Liste"}]}

    inner = llm._fake_handlers["fill"]
    llm.register_fake("fill", fake)
    try:
        e = features.fill(model, _classified("L-1", "Schelle", "EC000001"))
    finally:
        llm.register_fake("fill", inner)

    assert len(gesehen) == 2, "gekuerzte Liste, leer geblieben -> genau eine Nachfrage"
    assert ziel[0] not in gesehen[0], "erster Durchgang zeigt den Rest noch nicht"
    assert "nur 40 von 90 Werten gezeigt" in gesehen[0], "die Kuerzung wird ausgesprochen"
    assert ziel[0] in gesehen[1], "die Nachfrage zeigt genau den verschwiegenen Rest"
    assert e.features[0].value == ziel[0], "der Wert hinter der Grenze ist erreichbar"
    assert e.feature_meta[f.feature_id]["values_split"] is True
    # Und der Aufwand bleibt im Budget: hoechstens ein Zusatzaufruf je Artikel.
    assert len(gesehen) <= 2


def test_short_value_list_needs_no_second_call(model):
    """Ohne Kuerzung darf kein zusaetzlicher Aufruf entstehen (Kostenregel CLAUDE.md)."""
    n = []
    inner = llm._fake_handlers["fill"]
    llm.register_fake("fill", lambda p: (n.append(p), inner(p))[1])
    try:
        features.fill(model, _classified("K-1", "Kugelhahn DN 20", "EC000003"))
    finally:
        llm.register_fake("fill", inner)
    assert len(n) == 1


def test_invented_ev_and_ef_codes_never_reach_export(model):
    """Erfundene EV- und EF-Codes: der Wert faellt weg, das Merkmal wird gemeldet."""
    feats = model.features_for("EC000001")
    ev_feat = next(f for f in feats if f.type == "A")

    def fake(prompt: str):
        return {"features": [
            {"feature_id": ev_feat.feature_id, "value": "EV999999", "value_max": None,
             "source": "frei erfunden", "confidence": 0.99, "reason": None},
            {"feature_id": "EF999999", "value": "42", "value_max": None,
             "source": "auch erfunden", "confidence": 0.99, "reason": None},
        ]}

    inner = llm._fake_handlers["fill"]
    llm.register_fake("fill", fake)
    try:
        e = features.fill(model, _classified("F-1", "Schelle", "EC000001"))
    finally:
        llm.register_fake("fill", inner)

    fids = {v.feature_id for v in e.features}
    assert "EF999999" not in fids, "erfundenes Merkmal darf nicht in enriched.json"
    assert e.invented_codes == ["EF999999"]
    ev = next(v for v in e.features if v.feature_id == ev_feat.feature_id)
    assert ev.value is None and "nicht in Werteliste" in (ev.reason or "")
    assert ev.confidence == 0.0
    assert e.needs_review
    # Kein erfundener Code steht irgendwo als Wert im Ergebnis
    roh = json.dumps(e.model_dump(), ensure_ascii=False)
    assert '"value": "EV999999"' not in roh


def test_unit_conversion_and_imperial(model):
    """Punkt 3d: metrische Massstabsfehler und unveraenderte Zollwerte fliegen raus."""
    from etim import units

    # Massstabswechsel innerhalb des metrischen Systems
    assert units.check("2500", "mm", "Länge 2,5 m") == ("ok", "")
    befund, warum = units.check("2.5", "mm", "Länge 2,5 m")
    assert befund == "verwerfen" and "2500 mm" in warum
    assert units.check("1.5", "kg", "Gewicht 1500 g") == ("ok", "")
    assert units.check("1500", "kg", "Gewicht 1500 g")[0] == "verwerfen"

    # Imperiales: umgerechnet gut, unveraendert raus
    assert units.check("76.2", "mm", "Rohr 3 inch") == ("ok", "")
    assert units.check("3", "mm", "Rohr 3 inch")[0] == "verwerfen"
    assert units.check("12.7", "mm", 'Gewinde 1/2"') == ("ok", "")           # Bruch richtig gelesen
    assert units.check("0.5", "mm", 'Gewinde 1/2"')[0] == "verwerfen"
    assert units.check("2.07", "bar", "Druck 30 psi") == ("ok", "")

    # Baugroessen sind keine Laengen: nicht verwerfen, aber vorlegen
    assert units.check("15", "mm", 'Anschluss 1/2 Zoll')[0] == "pruefen"
    # Ohne Einheit im Zitat oder ohne ETIM-Einheit wird nicht geraten
    assert units.check("20", "mm", "DN 20") == ("ok", "")
    assert units.check("20", "", "Länge 2,5 m") == ("ok", "")
    assert units.check("20", "mm", None) == ("ok", "")
    # Toleranz: Kataloge runden
    assert units.check("2495", "mm", "Länge 2,5 m") == ("ok", "")


def test_imperial_value_never_reaches_enriched(model):
    """Derselbe Fall durch die Pipeline: der Zollwert landet nicht in enriched.json."""
    f = next(x for x in model.features_for("EC000001") if x.type == "R")
    assert f.unit_desc == "mm"

    inner = llm._fake_handlers["fill"]
    llm.register_fake("fill", lambda prompt: {"features": [
        {"feature_id": f.feature_id, "value": "1", "value_max": "2",
         "source": "Spannbereich 1 inch bis 2 inch", "confidence": 0.95, "reason": None}]})
    try:
        e = features.fill(model, _classified("I-1", "Schelle", "EC000001",
                                             source_quote="Spannbereich 1 inch bis 2 inch"))
    finally:
        llm.register_fake("fill", inner)

    v = next(x for x in e.features if x.feature_id == f.feature_id)
    assert v.value is None and v.value_max is None, "unveraenderter Zollwert darf nicht bleiben"
    assert "imperiale Angabe" in (v.reason or "")
    assert v.confidence == 0.0
