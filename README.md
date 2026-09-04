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

## Lizenz-Hinweise

ETIM-Klassifikation © ETIM International, lizenziert unter ODC-By 1.0. Die deutsche
Sprachversion ist eine Mitgliederleistung von ETIM Deutschland e. V. und wird hier nicht
mitgeliefert. BMEcat ist ein Standard des BME e. V.
