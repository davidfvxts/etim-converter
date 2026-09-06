# etim-pipeline

Herstellerkatalog rein → ETIM-klassifizierte Produktdaten als BMEcat raus.

## Quickstart (Tag 1)

```bash
make setup
cp .env.example .env            # GEMINI_API_KEY eintragen
bash scripts/download_etim.sh   # ETIM 10.0 (EN) + BMEcat-Guideline + xChange nach data/
python -m etim inspect data/etim
python -m etim load-model data/etim
python -m etim run beispiele/katalog.pdf --job demo --supplier "Muster GmbH"
open out/demo/report.md
python -m etim ui out/demo       # Prüf-Cockpit im Browser
```

Ohne API-Key: `ETIM_DRY_RUN=1 make test` läuft die ganze Pipeline mit Fakes gegen die Mini-Fixture.

## Ausgaben je Job (`out/<job>/`)

| Datei | Inhalt |
|---|---|
| `products.json` | extrahierte Artikel mit Rohattributen und Seitenreferenz |
| `classified.json` | ETIM-Klasse je Artikel, Kandidaten, Konfidenz |
| `enriched.json` | Merkmale je Artikel mit Quelle und Konfidenz |
| `catalog.bmecat.xml` | BMEcat 2005 mit ETIM-Merkmalen |
| `validation.json` | Prüfergebnis |
| `report.md` | Vollständigkeitsreport (das Dokument, das der Kunde sieht) |
| `review.csv` | Artikel/Merkmale unter Schwelle für manuelle Prüfung |
| `review.decisions.json` | Freigaben und Korrekturvermerke aus dem Prüf-Cockpit |

## Prüf-Cockpit

`python -m etim ui out/<job>` öffnet die Oberfläche unter http://127.0.0.1:8000 — Übersicht,
Artikelliste, Prüfansicht und Exportstatus. In der Prüfansicht steht je ETIM-Merkmal der Wert
mit Einheit und Konfidenz und darunter das wörtliche Katalogzitat, das ihn belegt; ohne Beleg
steht dort der Grund statt eines Wertes.

Der Server kommt aus der Standardbibliothek, die Oberfläche ist statisches CSS/JS in `web/` —
kein Build, keine npm-Abhängigkeit, läuft offline. `web/assets/tokens.css` ist der einzige Ort
für Farb-, Typo- und Rasterwerte. `python scripts/build_preview.py` erzeugt daraus eine einzelne
HTML-Datei zum Weitergeben (zeigt Beispieldaten, keinen Job).

## Lizenz-Hinweise

ETIM-Klassifikation © ETIM International, lizenziert unter ODC-By 1.0. Die deutsche
Sprachversion ist eine Mitgliederleistung von ETIM Deutschland e. V. und wird hier nicht
mitgeliefert. BMEcat ist ein Standard des BME e. V.
