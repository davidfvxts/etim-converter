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
python -m etim studio            # Cockpit über allen Jobs: Katalog einspielen, Lauf starten
python -m etim ui out/demo       # dasselbe Cockpit, direkt auf einem Job
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
| `compare.json` | Modellvergleich Gemini gegen Jev: Antworten, Kennzahlen, Kandidatenränge |
| `reference.json` | optional, von Hand: `{"<artikelnr>": "EC004089"}` — ohne diese Datei wird keine Trefferquote ausgewiesen |
| `source/` | der hochgeladene Katalog (Kundendaten, gitignored) |
| `review.decisions.json` | Freigaben und Korrekturvermerke aus dem Prüf-Cockpit |

## Prüf-Cockpit

`python -m etim studio` öffnet die Oberfläche unter http://127.0.0.1:8000 — Übersicht, Katalog,
Vergleich, Artikelliste, Prüfansicht und Exportstatus. In der Prüfansicht steht je ETIM-Merkmal
der Wert mit Einheit und Konfidenz und darunter das wörtliche Katalogzitat, das ihn belegt; ohne
Beleg steht dort der Grund statt eines Wertes. `python -m etim ui out/<job>` startet dasselbe
Cockpit direkt auf einem Job.

Unter **Katalog** lässt sich ein PDF, XLSX oder CSV per Drag & Drop einspielen: daraus entsteht
ein neuer Job, die Artikel werden im Hintergrund extrahiert und direkt danach läuft der Vergleich.
Der Fortschritt (Artikel extrahieren → Retrieval → Gemini → Jev) steht in der Oberfläche, Fehler
ebenfalls. Angenommen wird nur, was inhaltlich ein PDF, eine Excel-Mappe oder eine CSV ist —
die Endung allein zählt nicht. Bestehende Jobs werden nie überschrieben, und solange ein Lauf
aktiv ist, startet auf demselben Job kein zweiter. Der Server hört nur auf 127.0.0.1.

Der Server kommt aus der Standardbibliothek, die Oberfläche ist statisches CSS/JS in `web/` —
kein Build, keine npm-Abhängigkeit, läuft offline. `web/assets/tokens.css` ist der einzige Ort
für Farb-, Typo- und Rasterwerte. `python scripts/build_preview.py` erzeugt daraus eine einzelne
HTML-Datei zum Weitergeben (zeigt Beispieldaten, keinen Job).

## Modellvergleich: Gemini gegen Jev

```bash
python -m etim compare --job demo                  # beide Modelle, Klassenzuordnung
python -m etim compare --job demo --reuse-gemini   # Gemini aus dem letzten Lauf, nur Jev neu
python -m etim compare --job demo --features       # zusätzlich die ETIM-Merkmale
```

Beide Modelle bekommen denselben Artikel und dieselbe Retrieval-Liste — Gemini die Top-20,
Jev bis zu 254 Kandidaten (Jev erlaubt 255 Optionen je Choice, eine bleibt für „keine passt“).
Verglichen werden Trefferquote gegen `reference.json`, Artikel ohne Klasse, Variantenkonsistenz,
unbelegte EC-Codes in der Begründung, Konfidenz bei richtigen gegen falsche Antworten, Latenz
und Kosten. Ohne `reference.json` gibt es keine Trefferquote, sondern Abdeckung, Konsistenz und
unbelegte Codes — eine Prozentzahl gegen eine Wahrheit, die niemand festgelegt hat, wird nicht
ausgewiesen. Ohne eingerichteten Jev-Zugang wird nichts als Jev-Ergebnis dargestellt.

Wo Jev eingesetzt wird und wo bewusst nicht, steht in `CLAUDE.md`.

### Zugang über Cloudflare

Die Jev-Aufrufe laufen über den Worker in `worker/` (`typesafe/jev` per Workers-AI-Binding), damit
die Zugangsdaten als Cloudflare-Secret dort liegen und nicht in der App. Einrichtung und
Absicherung: `worker/README.md`. Alternativ `ETIM_JEV_TRANSPORT=cloudflare` für den direkten Weg
über die REST-API.

## Lizenz-Hinweise

ETIM-Klassifikation © ETIM International, lizenziert unter ODC-By 1.0. Die deutsche
Sprachversion ist eine Mitgliederleistung von ETIM Deutschland e. V. und wird hier nicht
mitgeliefert. BMEcat ist ein Standard des BME e. V.
