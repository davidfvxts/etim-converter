# etim-pipeline

Herstellerkatalog rein → ETIM-klassifizierte Produktdaten als BMEcat raus.

## Quickstart (Tag 1)

**Alle Befehle im Projektordner ausführen** — nicht im Home-Verzeichnis.

```bash
cd /pfad/zu/etim-converter
make setup                      # einmalig: virtuelle Umgebung + Abhängigkeiten
cp .env.example .env            # GEMINI_API_KEY eintragen
bash scripts/download_etim.sh   # ETIM 10.0 (EN) + BMEcat-Guideline nach data/
make jev                        # Cloudflare-Worker deployen, .env für Jev einrichten
make load-model                 # ETIM-CSV -> SQLite + Klassen-Embeddings
make studio                     # Cockpit im Browser
```

Die `make`-Ziele rufen Python aus `.venv/` auf. Du musst die Umgebung also **nicht**
aktivieren — und auf macOS nicht daran denken, dass `python` dort `python3` heißt.
Wer lieber direkt arbeitet: `source .venv/bin/activate`, danach funktioniert
`python -m etim …` wie in dieser Datei beschrieben.

Weitere Ziele: `make compare JOB=strawa`, `make reference JOB=strawa`, `make test`, `make eval`.

Ohne API-Key: `make test` läuft die ganze Pipeline mit Fakes gegen die Mini-Fixture.

### Wenn etwas hakt (macOS)

| Meldung | Ursache und Abhilfe |
|---|---|
| `You have not agreed to the Xcode and Apple SDKs license` | Apples Kommandozeilen-Werkzeuge (auch `git` und `make`) sind gesperrt, bis die Lizenz bestätigt ist: `sudo xcodebuild -license accept` |
| `No such file or directory` bei `scripts/…` | Du bist nicht im Projektordner. Erst `cd` dorthin. |
| `command not found: python` | macOS hat nur `python3`. Die `make`-Ziele lösen das; sonst `source .venv/bin/activate`. |
| `command not found: make` | Kommandozeilen-Werkzeuge fehlen: `xcode-select --install` |

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

## Klassifizierungsmodell wählen

Die ETIM-Klasse kann Gemini wählen, Jev, oder beide zum Vergleich:

```bash
python -m etim classify --job demo --model gemini   # Top-20 Kandidaten, mit Quellzitaten
python -m etim classify --job demo --model jev      # bis zu 254 Kandidaten, keine erfundenen Codes
python -m etim classify --job demo --model both     # beide, plus compare.json
```

Im Cockpit steht die Wahl unter **Katalog** als drei Karten und gilt für den nächsten Lauf —
auch für einen frisch hochgeladenen Katalog. Welches Modell einen Job klassifiziert hat, steht
in der Kopfzeile, auf der Übersicht und in der Jobliste; `classified.json` trägt es an jedem
Artikel mit (`model`, `simulated`).

Die Vorauswahl kommt aus `ETIM_CLASSIFIER` (Vorgabe `gemini`). Wer überwiegend vergleicht,
setzt dort `both`.

**Bei `both` zählt für den Export weiterhin Gemini.** Ein Vergleichslauf soll messen, nicht
unbemerkt die Lieferdatei ändern — fällt die Entscheidung für Jev, wird mit `--model jev`
neu klassifiziert.

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

### Zugang über Cloudflare — einmal einrichten

```bash
bash scripts/setup_jev.sh
```

Das Skript meldet bei Cloudflare an (falls nötig), erzeugt ein Shared Secret, hinterlegt es beim
Worker, veröffentlicht ihn, trägt Adresse und Secret in die `.env` ein und prüft zum Schluss, ob
er antwortet. Es ist wiederholbar: ein bereits in der `.env` stehendes Secret wird wiederverwendet,
und das Secret wird nie ausgegeben. Vor dem Schreiben entsteht `.env.bak`.

Die Jev-Aufrufe laufen dann über den Worker in `worker/` (`typesafe/jev` per Workers-AI-Binding),
die Zugangsdaten liegen als Cloudflare-Secret dort und nicht in der App. Was der Worker genau tut
und warum er kein offener Proxy ist: `worker/README.md`.

### Alternative ohne Worker: direkt an Workers AI

Wer Node.js und `wrangler` nicht einrichten will, spricht Workers AI direkt an — dann entfällt
`make jev` komplett:

1. Im Cloudflare-Dashboard auf die Seite **Workers AI**, dort **Use REST API**.
2. **Create a Workers AI API Token** → Token kopieren. (Ein selbst gebautes Token braucht
   die Berechtigungen `Workers AI – Read` **und** `Workers AI – Edit`.)
3. Auf derselben Seite die **Account ID** kopieren.
4. In die `.env`:

```
ETIM_JEV_TRANSPORT=cloudflare
CLOUDFLARE_ACCOUNT_ID=<Account ID>
CLOUDFLARE_API_TOKEN=<Token>
```

Der Unterschied ist nur, wo die Zugangsdaten liegen: beim Worker als Cloudflare-Secret, hier in
der `.env` auf dem eigenen Rechner (gitignored). Für Messläufe auf dem eigenen Laptop ist das in
Ordnung; sobald jemand anderes die Pipeline bedient, ist der Worker der bessere Ort.

### Prüfen, ob Jev antwortet

```bash
make jev-check
```

Macht einen echten Mini-Aufruf (zwei Optionen, Bruchteil eines Cents) und meldet Modell, Antwort,
Laufzeit und Kosten — oder sagt im Klartext, was in der `.env` fehlt. Egal, welcher der beiden
Wege eingerichtet ist.

### Trefferquote: Referenzklassen festlegen

```bash
python -m etim reference out/<job>     # Gerüst mit allen Artikelnummern, Klassen leer
```

Danach in `out/<job>/reference.json` je Artikel die richtige ETIM-Klasse eintragen. Teilweise
ausgefüllt ist erlaubt — leere Felder zählen nicht mit. Das Gerüst füllt die Klassen bewusst
**nicht** mit Modellvorschlägen vor: sonst misst der Vergleich das Modell gegen seine eigene
Antwort. Eine bestehende `reference.json` wird nie überschrieben.

## Lizenz-Hinweise

ETIM-Klassifikation © ETIM International, lizenziert unter ODC-By 1.0. Die deutsche
Sprachversion ist eine Mitgliederleistung von ETIM Deutschland e. V. und wird hier nicht
mitgeliefert. BMEcat ist ein Standard des BME e. V.
