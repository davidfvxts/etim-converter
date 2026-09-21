"""enriched.json -> catalog.bmecat.xml  (BMEcat 2005, Struktur nach ETIM BMEcat Guideline 5.x)

Elementreihenfolge ist im XSD fest — nicht umsortieren. Pflichtprüfung gegen das XSD aus der
Guideline-ZIP macht validate.py. Range-Merkmale werden als zwei FVALUE (min, max) geschrieben.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from lxml import etree

from . import versions
from .schemas import EnrichedProduct

NS = "http://www.bmecat.org/bmecat/2005"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def _el(parent, tag, text=None, **attrs):
    e = etree.SubElement(parent, f"{{{NS}}}{tag}", **{k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def build(products: list[EnrichedProduct], supplier_name: str, catalog_id: str, supplier_gln: str | None = None, include_review: bool = False) -> etree._ElementTree:
    root = etree.Element(f"{{{NS}}}BMECAT", nsmap={None: NS, "xsi": XSI}, version="2005")
    root.set(f"{{{XSI}}}schemaLocation", f"{NS} bmecat_2005.xsd")

    # Die Version kommt aus dem Job, nicht aus der aktuellen .env: ein Katalog,
    # der gegen ETIM 8 klassifiziert wurde, darf sich nicht als ETIM 10 ausgeben,
    # nur weil inzwischen etwas anderes eingestellt ist.
    etim_version = versions.label(next((p.etim_version for p in products if p.etim_version), None))

    header = _el(root, "HEADER")
    gen = _el(header, "GENERATOR_INFO", f"etim-pipeline {datetime.now():%Y-%m-%d}")
    cat = _el(header, "CATALOG")
    _el(cat, "LANGUAGE", "deu", default="true")
    _el(cat, "CATALOG_ID", catalog_id)
    _el(cat, "CATALOG_VERSION", "001.001")
    _el(cat, "CATALOG_NAME", f"{supplier_name} – {etim_version}")
    _el(cat, "GENERATION_DATE", datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    _el(cat, "TERRITORY", "DE")
    _el(cat, "CURRENCY", "EUR")
    parties = _el(header, "PARTIES")
    party = _el(parties, "PARTY")
    _el(party, "PARTY_ID", supplier_gln or supplier_name, type="gln" if supplier_gln else "supplier_specific")
    _el(party, "PARTY_ROLE", "supplier")
    addr = _el(party, "ADDRESS")
    _el(addr, "NAME", supplier_name)
    _el(header, "SUPPLIER_IDREF", supplier_gln or supplier_name, type="gln" if supplier_gln else "supplier_specific")

    t = _el(root, "T_NEW_CATALOG")
    n = 0
    for e in products:
        if not e.class_id:
            continue
        if e.needs_review and not include_review:
            continue
        p = e.product
        prod = _el(t, "PRODUCT", mode="new")
        _el(prod, "SUPPLIER_PID", p.supplier_pid, type="supplier_specific")
        det = _el(prod, "PRODUCT_DETAILS")
        _el(det, "DESCRIPTION_SHORT", p.name[:80])
        if p.description:
            _el(det, "DESCRIPTION_LONG", p.description[:64000])
        if p.gtin:
            _el(det, "INTERNATIONAL_PID", p.gtin, type="gtin")
        _el(det, "MANUFACTURER_PID", p.supplier_pid)
        _el(det, "MANUFACTURER_NAME", supplier_name)

        pf = _el(prod, "PRODUCT_FEATURES")
        _el(pf, "REFERENCE_FEATURE_SYSTEM_NAME", etim_version)
        _el(pf, "REFERENCE_FEATURE_GROUP_ID", e.class_id)
        for fv in e.features:
            if fv.value is None:
                continue
            meta = e.feature_meta.get(fv.feature_id, {})
            f = _el(pf, "FEATURE")
            _el(f, "FNAME", fv.feature_id)
            if meta.get("type") == "R":
                _el(f, "FVALUE", fv.value)
                _el(f, "FVALUE", fv.value_max if fv.value_max is not None else fv.value)
            elif meta.get("type") == "L":
                _el(f, "FVALUE", "true" if str(fv.value).lower() in ("true", "1", "ja", "yes") else "false")
            else:
                _el(f, "FVALUE", fv.value)
            if meta.get("unit_id"):
                _el(f, "FUNIT", meta["unit_id"])

        od = _el(prod, "PRODUCT_ORDER_DETAILS")
        _el(od, "ORDER_UNIT", "C62")
        _el(od, "CONTENT_UNIT", "C62")
        _el(od, "NO_CU_PER_OU", "1")
        _el(od, "PRICE_QUANTITY", "1")
        _el(od, "QUANTITY_MIN", "1")
        _el(od, "QUANTITY_INTERVAL", "1")
        pd = _el(prod, "PRODUCT_PRICE_DETAILS")
        pp = _el(pd, "PRODUCT_PRICE", price_type="net_list")
        _el(pp, "PRICE_AMOUNT", "0.00")
        _el(pp, "PRICE_CURRENCY", "EUR")
        _el(pp, "TAX", "0.19")
        n += 1
    root.insert(0, etree.Comment(f" {n} Artikel, erzeugt von etim-pipeline; ETIM-Klassifikation (c) ETIM International, ODC-By 1.0 "))
    gen.text = f"etim-pipeline {datetime.now():%Y-%m-%d} ({n} Artikel)"
    return etree.ElementTree(root)


def run(out_dir: Path, supplier_name: str, supplier_gln: str | None = None, include_review: bool = False) -> Path:
    items = [EnrichedProduct.model_validate(x) for x in json.loads((out_dir / "enriched.json").read_text())]
    tree = build(items, supplier_name, catalog_id=out_dir.name, supplier_gln=supplier_gln, include_review=include_review)
    path = out_dir / "catalog.bmecat.xml"
    tree.write(str(path), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    n = len(tree.getroot().findall(f".//{{{NS}}}PRODUCT"))
    print(f"export: {n} Artikel → {path}" + ("" if include_review else " (Review-Artikel ausgelassen; --include-review zum Erzwingen)"))
    return path
