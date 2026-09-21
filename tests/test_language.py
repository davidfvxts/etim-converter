"""Deutsch und englisch gleich behandeln.

Kundenkataloge kommen deutsch, ETIM-Klassentexte sind englisch, und manche
Hersteller liefern ihre Liste schon auf Englisch. Was die Pipeline deterministisch
entscheidet — Spaltenzuordnung, Variantentrennung, Zahlen- und Einheitenlesen —
darf nicht an der Katalogsprache haengen. Die Trefferquote selbst haengt am Modell
und wird gemessen, nicht hier behauptet (docs/testlauf-lts.md).
"""
from etim import units
from etim.classify import base_name, group_variants, product_state, query_text
from etim.ingest import map_columns
from etim.schemas import Product, RawAttribute


def test_column_mapping_both_languages():
    de = map_columns(["Artikelnummer", "GTIN", "Typbezeichnung", "Kurzbeschreibung", "Langbeschreibung"])
    en = map_columns(["Article Number", "EAN", "Type", "Short Description", "Long Description"])
    assert de == {"pid": "Artikelnummer", "gtin": "GTIN", "name": "Kurzbeschreibung", "desc": "Langbeschreibung"}
    assert en == {"pid": "Article Number", "gtin": "EAN", "name": "Short Description", "desc": "Long Description"}
    # In beiden Sprachen gilt: der Typcode ist nie die Bezeichnung.
    assert de["name"] != "Typbezeichnung" and en["name"] != "Type"


def test_variant_split_both_languages():
    paare = [
        ("Regelgruppe 130/6 mit Grundfos UPM3", "Control station 130/6 with Grundfos UPM3"),
        ("Rohrschelle 20-25 mit Gummieinlage", "Pipe clamp 20-25 with rubber inlay"),
    ]
    for de, en in paare:
        # In beiden Sprachen faellt die verbaute Komponente weg, der Produkttyp bleibt.
        assert " mit " not in base_name(de) and " with " not in base_name(en)
        assert base_name(de) != de and base_name(en) != en

    ps = [Product(supplier_pid=str(i), name=n) for i, n in enumerate([
        "Pipe clamp 20-25 with Grundfos", "Pipe clamp 20-25 with Wilo", "Ball valve DN 20"])]
    assert group_variants(ps) == [("pipe clamp 20-25", [0, 1]), ("ball valve dn 20", [2])]


def test_query_and_state_do_not_assume_a_language():
    en = Product(supplier_pid="E-1", name="Ball valve DN 20", description="Brass body",
                 attributes=[RawAttribute(name="Material", value="Brass")])
    de = Product(supplier_pid="D-1", name="Kugelhahn DN 20", description="Messingkörper",
                 attributes=[RawAttribute(name="Material", value="Messing")])
    for p in (en, de):
        q = query_text(p)
        assert p.name in q and "Material" in q
    # Der Sprachhinweis an Jev darf keine Sprache behaupten, die nicht feststeht.
    hinweis = product_state(en)["language_note"].lower()
    assert "german" in hinweis and "often" in hinweis, "Deutsch als Regel, nicht als Tatsache"


def test_numbers_and_units_in_both_notations():
    # Dezimalkomma (deutsch) und Dezimalpunkt (englisch) lesen sich gleich
    assert units.parse_quantities("Länge 2,5 m") == units.parse_quantities("Length 2.5 m")
    # Zoll deutsch wie englisch: dieselbe Zahl, die Schreibweise bleibt wie im Katalog
    assert [v for v, _ in units.parse_quantities('Gewinde 1/2 Zoll')] \
        == [v for v, _ in units.parse_quantities('Thread 1/2 inch')] == [0.5]
    assert units.check("12.7", "mm", "Gewinde 1/2 Zoll") == units.check("12.7", "mm", "Thread 1/2 inch")
    assert units.check("2.5", "mm", "Länge 2,5 m")[0] == units.check("2.5", "mm", "Length 2.5 m")[0] == "verwerfen"
