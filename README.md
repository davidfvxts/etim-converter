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
| `.checkpoint.*.json` | Zwischenstand eines laufenden/abgebrochenen Laufs; wird nach Erfolg gelöscht |
| `review.decisions.json` | Freigaben und Korrekturvermerke aus dem Prüf-Cockpit |

## Ohne Terminal arbeiten

**`ETIM-Cockpit starten.command`** im Projektordner doppelklicken. Der Starter richtet beim
ersten Mal die Arbeitsumgebung ein, startet das Cockpit und öffnet den Browser; läuft schon
eins, öffnet er nur den Browser. Zum Beenden das Fenster schließen.

Für den Schnellzugriff die Datei **nicht kopieren** — sie würde am neuen Ort kein Projekt
finden. Stattdessen aus dem Projektordner **ins Dock ziehen** (rechter Bereich) oder per
Rechtsklick → *Alias erzeugen* einen Alias anlegen und nur den verschieben. Liegt der Starter
trotzdem woanders, sucht er das Projekt an den üblichen Stellen und sagt, was er gefunden hat.

**Nach jedem `git pull` das Cockpit neu starten.** Die Oberfläche wird bei jedem Aufruf frisch
gelesen, der Python-Code nur beim Start — sonst stehen neue Knöpfe an einem alten Server. Das
Cockpit erkennt das und zeigt oben ein Warnband.

Im Cockpit selbst geht dann der Rest: Katalog einspielen, Lauf starten, **fehlende ETIM-Version
laden** (Knopf auf der Versionskarte — sucht das ZIP, entpackt, lädt, baut die Embeddings) und
**Jev prüfen** (echter Mini-Aufruf, zeigt Modell, Laufzeit und Kosten oder den Fehlergrund).

Terminal braucht es nur noch für das Einrichten der Zugangsdaten (`make env`) und für
`git pull`.

## Prüf-Cockpit

`python -m etim studio` öffnet die Oberfläche unter http://127.0.0.1:8000 — Übersicht, Katalog,
Vergleich, Artikelliste, Prüfansicht und Exportstatus. In der Prüfansicht steht je ETIM-Merkmal
der Wert mit Einheit und Konfidenz und darunter das wörtliche Katalogzitat, das ihn belegt; ohne
Beleg steht dort der Grund statt eines Wertes. `python -m etim ui out/<job>` startet dasselbe
Cockpit direkt auf einem Job.

Unter **Katalog** lässt sich ein PDF, XLSX oder CSV per Drag & Drop einspielen: daraus entsteht
ein neuer Job. **Der Lauf startet nicht von selbst** — nach dem Hochladen steht der Job auf
„bereit zum Start“, und erst der Knopf schickt ihn los. So lässt sich vorher noch das Modell
(Gemini, Jev oder beide) und die ETIM-Version umstellen. Angenommen wird nur, was inhaltlich ein
PDF, eine Excel-Mappe oder eine CSV ist — die Endung allein zählt nicht. Bestehende Jobs werden
nie überschrieben, und solange ein Lauf aktiv ist, startet auf demselben Job kein zweiter.
Der Server hört nur auf 127.0.0.1.

### Fortschritt, Abbrechen, Zwischenstand

Während ein Lauf läuft, zeigt die Oberfläche Prozent, `x von y` Artikeln, die verstrichene Zeit
und eine hochgerechnete Restzeit, dazu die aktuelle Stufe (Artikel extrahieren → Retrieval →
Gemini → Jev → Merkmale) und den Namen des Artikels, der gerade bearbeitet wird. Fehler stehen
im Cockpit, nicht nur im Terminal.

**Abbrechen** stoppt zwischen zwei Artikeln — die laufende Anfrage wird zu Ende geführt, danach
endet der Lauf. Was bis dahin fertig war, ist gesichert.

**Der Zwischenstand wird mitgeschrieben** (alle 5 Artikel, `out/<job>/.checkpoint.*.json`).
Ein neuer Lauf auf demselben Job setzt dort auf, statt von vorn zu beginnen: nach einem Abbruch,
nach einem Absturz und auch dann, wenn Jev mitten im Lauf ausfällt — Geminis bereits bezahlte
Antworten bleiben erhalten. Wer wirklich neu rechnen will, nimmt **„Von vorn beginnen“**
(im Terminal: `--fresh`). Ändern sich Modell oder ETIM-Version, wird der Zwischenstand von selbst
verworfen — er gehört dann nicht mehr zu diesem Lauf. `ETIM_CHECKPOINT_EVERY` stellt den Abstand
ein.

Der Server kommt aus der Standardbibliothek, die Oberfläche ist statisches CSS/JS in `web/` —
kein Build, keine npm-Abhängigkeit, läuft offline. `web/assets/tokens.css` ist der einzige Ort
für Farb-, Typo- und Rasterwerte. `python scripts/build_preview.py` erzeugt daraus eine einzelne
HTML-Datei zum Weitergeben (zeigt Beispieldaten, keinen Job).

## ETIM-Version wählen

Mehrere Versionen liegen nebeneinander. Jede bekommt eine eigene Datenbank **und** eigene
Klassen-Embeddings — Klassen-IDs sind zwischen ETIM-Versionen nicht stabil, ein geteilter
Cache würde stillschweigend falsch zuordnen.

```
data/etim/8.0/    CSV-Release ETIM 8.0     entpackt von etim-international.com
data/etim/9.0/    CSV-Release ETIM 9.0
data/etim/10.0/   CSV-Release ETIM 10.0    (oder direkt in data/etim/)
```

Das ZIP muss nicht von Hand entpackt werden: `load-model` sucht in `data/downloads/`,
`data/etim/` und `data/` nach einem Release, dessen Dateiname die Version nennt — so wie die
Dateien von etim-international.com heißen — und packt es selbst an die richtige Stelle.

```bash
make versions                 # was liegt vor, was fehlt
make load-model ETIM=9.0      # ZIP finden, entpacken, laden, Embeddings bauen
make load-model               # Vorgabeversion aus ETIM_VERSION
python -m etim classify --job demo --etim 9.0
```

Im Cockpit stehen die Versionen unter **Katalog** als Karten; nicht geladene sind ausgegraut
und nennen beim Überfahren, was fehlt. Welche Version einen Job klassifiziert hat, steht in der
Kopfzeile, auf der Übersicht und als Spalte in der Jobliste.

**Durchgesetzt wird die Trennung an drei Stellen:** `classified.json` und `enriched.json` tragen
`etim_version` je Artikel; `features` bricht ab, wenn die geladene Version nicht zur
Klassenentscheidung passt; und der BMEcat-Export schreibt die Version des Jobs, nicht die
gerade eingestellte — ein gegen ETIM 9 klassifizierter Katalog darf sich nicht als ETIM 10
ausgeben.

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
4. Eintragen — geführt, ohne die Datei zu öffnen:

```bash
make env
```

Der Dialog fragt Gemini-Schlüssel, Cloudflare-Zugang und Klassifizierungsmodell ab. Eingaben mit
Passwortcharakter werden **verdeckt** eingelesen, erscheinen also weder auf dem Bildschirm noch in
der Shell-Historie; Enter behält den vorhandenen Wert. Angeführte Anführungszeichen und
Leerzeichen aus dem Einfügen werden abgeschnitten, und eine vertauschte Account ID fällt sofort
auf. Zum Schluss läuft automatisch die Probe. Die vorherige Fassung liegt als `.env.bak` daneben.

Wer die Datei lieber selbst bearbeitet, trägt dasselbe von Hand ein:

```
ETIM_JEV_TRANSPORT=cloudflare
CLOUDFLARE_ACCOUNT_ID=<Account ID>
CLOUDFLARE_API_TOKEN=<Token>
```

Der Unterschied ist nur, wo die Zugangsdaten liegen: beim Worker als Cloudflare-Secret, hier in
der `.env` auf dem eigenen Rechner (gitignored). Für Messläufe auf dem eigenen Laptop ist das in
Ordnung; sobald jemand anderes die Pipeline bedient, ist der Worker der bessere Ort.

### Was ist eigentlich eingetragen?

```bash
make env-show
```

Zeigt den gewählten Jev-Weg und alle Werte aus der `.env` — Schlüssel und Secrets nur maskiert
(letzte vier Zeichen). Nützlich nach `make jev`: das Skript trägt Adresse und Secret selbst ein,
man tippt keine Zugangsdaten und sieht deshalb auch nie, was dort gelandet ist.

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
