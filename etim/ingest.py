"""Katalog (PDF oder XLSX/CSV) -> products.json

PDF: in Blöcke von PAGES_PER_CHUNK Seiten zerlegen, jeden Block als PDF an Gemini geben
(Gemini liest Tabellen und Bilder nativ). Excel/CSV: Zeilen direkt übernehmen, LLM nur
zum Erkennen der Spalten.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from . import config, llm
from .schemas import ExtractionResult, Product, RawAttribute

EXTRACT_PROMPT = """Du bist Produktdaten-Spezialist für den technischen Großhandel (SHK/Elektro/Bau).
Vor dir liegen die Seiten {first}–{last} eines Herstellerkatalogs.

Extrahiere JEDEN bestellbaren Artikel (jede Artikelnummer = ein Artikel, auch Varianten in Tabellen).
Regeln:
- supplier_pid exakt wie gedruckt (keine Leerzeichen entfernen, keine führenden Nullen streichen).
- name: kurze deutsche Bezeichnung inkl. unterscheidendem Merkmal (z. B. "Rohrschelle M8/M10 DN 20").
- attributes: ALLE technischen Angaben, die im Katalog zu diesem Artikel stehen, so wie gedruckt
  (Tabellenspalte = Attributname; Wert inkl. Einheit). Werte aus Tabellenköpfen und Seitentiteln
  gelten für alle Zeilen der Tabelle — übernimm sie je Artikel.
- page: Seitennummer innerhalb dieses Blocks, 1 = erste Seite des Blocks.
- source_quote: die Katalogzeile wörtlich.
- Nichts erfinden. Was nicht auf den Seiten steht, wird nicht angegeben.
- Reine Textseiten, Inhaltsverzeichnisse, AGB → products leer lassen, in notes vermerken.
"""


def pdf_chunks(path: Path, pages_per_chunk: int) -> list[tuple[int, int, bytes]]:
    reader = PdfReader(str(path))
    n = len(reader.pages)
    chunks = []
    for start in range(0, n, pages_per_chunk):
        writer = PdfWriter()
        end = min(start + pages_per_chunk, n)
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append((start + 1, end, buf.getvalue()))
    return chunks


def ingest_pdf(path: Path, pages: tuple[int, int] | None = None) -> tuple[list[Product], list[str]]:
    products: list[Product] = []
    notes: list[str] = []
    for first, last, data in pdf_chunks(path, config.PAGES_PER_CHUNK):
        if pages and (last < pages[0] or first > pages[1]):
            continue
        res = llm.generate_json(
            "extract",
            EXTRACT_PROMPT.format(first=first, last=last),
            ExtractionResult,
            files=[(data, "application/pdf")],
        )
        for p in res.products:
            p.page = first + max(p.page, 1) - 1
            products.append(p)
        if res.notes:
            notes.append(f"S. {first}–{last}: {res.notes}")
        print(f"  Seiten {first}–{last}: {len(res.products)} Artikel")
    return dedupe(products), notes


def ingest_table(path: Path) -> tuple[list[Product], list[str]]:
    """Excel/CSV: jede Zeile ein Artikel. Spalten heuristisch zuordnen, Rest wird Rohattribut."""
    rows: list[dict[str, str]] = []
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = [str(h or "").strip() for h in next(it)]
        for r in it:
            if not any(r):
                continue
            rows.append({header[i]: ("" if v is None else str(v)).strip() for i, v in enumerate(r) if i < len(header)})
    else:
        from .model import read_csv

        _, rows = read_csv(path)

    def find(row, *keys):
        for k in row:
            kl = k.lower()
            if any(x in kl for x in keys):
                return k
        return None

    products = []
    for i, r in enumerate(rows, start=2):
        pid_col = find(r, "artikelnummer", "artikel-nr", "art.-nr", "artnr", "sku", "supplier_pid", "bestellnummer", "art. nr")
        name_col = find(r, "bezeichnung", "name", "titel", "kurztext")
        desc_col = find(r, "beschreibung", "langtext", "description")
        gtin_col = find(r, "ean", "gtin")
        if not pid_col or not r.get(pid_col):
            continue
        used = {pid_col, name_col, desc_col, gtin_col}
        attrs = [RawAttribute(name=k, value=v) for k, v in r.items() if k not in used and v]
        products.append(
            Product(
                supplier_pid=r[pid_col],
                name=(r.get(name_col) if name_col else "") or r[pid_col],
                description=(r.get(desc_col) if desc_col else "") or "",
                gtin=(r.get(gtin_col) if gtin_col else None) or None,
                attributes=attrs,
                page=i,
                source_quote=" | ".join(f"{k}: {v}" for k, v in r.items() if v)[:500],
            )
        )
    return dedupe(products), [f"{len(rows)} Zeilen gelesen, {len(products)} mit Artikelnummer"]


def dedupe(products: list[Product]) -> list[Product]:
    seen: dict[str, Product] = {}
    for p in products:
        key = p.supplier_pid.strip()
        if key in seen:
            # Attribute zusammenführen, längere Beschreibung behalten
            known = {a.name for a in seen[key].attributes}
            seen[key].attributes += [a for a in p.attributes if a.name not in known]
            if len(p.description) > len(seen[key].description):
                seen[key].description = p.description
        else:
            seen[key] = p
    return list(seen.values())


def run(path: Path, out_dir: Path, pages: tuple[int, int] | None = None) -> list[Product]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".pdf":
        products, notes = ingest_pdf(path, pages)
    else:
        products, notes = ingest_table(path)
    (out_dir / "products.json").write_text(
        json.dumps({"products": [p.model_dump() for p in products], "notes": notes}, ensure_ascii=False, indent=2)
    )
    print(f"ingest: {len(products)} Artikel → {out_dir / 'products.json'}")
    return products
