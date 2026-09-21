# etim-pipeline — Projektanweisungen für Claude Code

Du arbeitest für David (TimeStudios). Er ist Produktmanager, kein Programmierer.
Triff technische Entscheidungen selbst, erkläre sie in zwei Sätzen, frag nur bei
Geschäftsentscheidungen nach. Antworte auf Deutsch.

## Was das hier ist

Ein Python-CLI, das Herstellerkataloge (PDF/Excel) in ETIM-klassifizierte
Produktdaten verwandelt und als BMEcat 2005 (ETIM-Guideline 5.0.2) exportiert.
Zielkunden: Hersteller mit 200–5.000 Artikeln, die für SHK-/Elektro-Großhändler
(Open Datacheck, DQR 11, Sonepar-Guideline) ETIM-Daten liefern müssen.

Pipeline (jede Stufe ist ein eigener CLI-Befehl, alle Zwischenstände sind JSON in `out/<job>/`):

```
ingest   PDF/XLSX  -> products.json          (Gemini liest Seiten, extrahiert Artikel + Rohattribute)
classify products  -> classified.json        (Retrieval -> Gemini ODER Jev ODER beide wählen die Klasse)
features classified -> enriched.json         (LLM füllt ETIM-Merkmale aus Wertelisten, mit Quellzitat)
export   enriched  -> catalog.bmecat.xml     (BMEcat 2005 + ETIM)
validate xml       -> validation.json        (XSD wenn vorhanden, sonst Strukturregeln)
report   enriched  -> report.md              (Vollständigkeit, Konfidenzen, Review-Queue)
compare  products  -> compare.json            (Gemini gegen Jev auf derselben Retrieval-Liste)
run      = alles nacheinander
ui       job       -> Prüf-Cockpit im Browser (Freigaben -> review.decisions.json)
studio   out/      -> dasselbe Cockpit über allen Jobs: Katalog einspielen, Lauf starten
```

## Wichtige Regeln

- **Mehrere ETIM-Versionen nebeneinander**: `data/etim/<version>/`, je Version eine eigene
  SQLite-Datenbank **und** eigene Embeddings (`etim/versions.py`). Klassen-IDs sind zwischen
  Versionen nicht stabil — ein geteilter Cache ordnet still falsch zu. `etim_version` steht
  an jedem Artikel; `features` bricht bei Versionsbruch ab, der Export schreibt die Version
  des Jobs, nicht die eingestellte.
- **ETIM-Daten liegen in `data/etim/`** (CSV-Export von etim-international.com, ETIM 10.0 English,
  Lizenz ODC-By — Attribution in README behalten). Die deutsche Sprachversion ist
  Mitgliederleistung von ETIM Deutschland: nicht herunterladen, nicht einbauen, bis David
  die Lizenzfrage geklärt hat. Prompts dürfen deutsch sein; Klassen-/Merkmalstexte sind englisch.
- **Nie Merkmalswerte erfinden.** Jede gefüllte Eigenschaft braucht `source` (Zitat aus dem Katalog)
  oder wird `null` mit `reason`. Lieber leer als falsch — Großhändler prüfen.
- **Konfidenz < 0.75 → Review-Queue**, nicht exportieren ohne Freigabe.
- Alle LLM-Aufrufe laufen über `etim/llm.py`. Kein direkter SDK-Aufruf woanders.
  `ETIM_DRY_RUN=1` ersetzt LLM-Aufrufe durch deterministische Fakes (für Tests).
- **Alle Jev-Aufrufe laufen über `etim/jev.py`**, genauso wie Gemini über `llm.py`. Kein
  direkter HTTP-Aufruf woanders. Im Trockenlauf trägt jede Jev-Antwort `simulated=True`;
  das Feld wird bis ins Dashboard durchgereicht. **Ohne Jev-Zugang darf nichts wie ein
  Jev-Ergebnis aussehen** — keine große Prozentzahl für ein Modell, das nicht gemessen wurde.
- **Ohne `reference.json` keine Trefferquote.** Stattdessen Abdeckung, Variantenkonsistenz
  und unbelegte Codes. Eine Quote gegen eine Wahrheit, die niemand festgelegt hat, ist keine.
- Hochgeladene Kataloge sind Kundendaten: sie liegen in `out/<job>/source/` und nie im Repo.
- Kosten: pro Artikel ≤ 3 LLM-Calls (classify, features, ggf. counter-check). Batching bevorzugen.
- Keine neuen Abhängigkeiten ohne Grund. Stack: python 3.11+, google-genai, pydantic, lxml, pypdf, numpy.

## Wo Jev eingesetzt wird — und wo nicht

Jev (TypeSafe System One, über Cloudflare Workers AI als `typesafe/jev`) beantwortet getypte
Fragen gegen einen Zustand und gibt kalibrierte Wahrscheinlichkeiten zurück. Er erzeugt **keinen
Text**. Daraus folgt die Aufteilung — nicht aus Vorliebe, sondern aus der Form der Aufgabe:
Jev urteilt, Gemini liest und schreibt.

**Jev bekommt:**

- **Klassenwahl als Choice über 254 Kandidaten.** Das ist der eigentliche Grund. Im
  strawa-Trockenlauf lag die richtige Klasse auf Rang 10–62 der Retrieval-Liste; Gemini sieht
  davon die Top-20 und konnte sie bei 14 von 17 Artikeln gar nicht wählen. Jev erlaubt 255
  Optionen je Choice — 254 Kandidaten plus `none` für „keine passt“. Damit wird aus einem
  Retrieval-Problem eine Modellentscheidung. Und: Jev kann keinen Code ausgeben, der nicht in
  der Optionsliste steht — die erfundenen EC-Codes aus dem strawa-Lauf sind hier strukturell
  ausgeschlossen, nicht nur unwahrscheinlich.
- **Strukturierte Optionsbeschreibungen (`what` / `not_for` / `examples`).** Jev liest JSON in
  den Optionen. Jede Klasse bekommt darum benannte Felder statt eines verklebten Satzes, und
  vor allem ein `not_for`, das die Grenze Hauptprodukt gegen „Accessories/spare parts for …“
  ausspricht: Zubehörklassen bekommen „nicht das Produkt selbst“, Hauptproduktklassen „nicht
  das Zubehör dazu“. Das ist die Grenze, an der die Zuordnung am häufigsten kippt.
  Passt das Feld nicht ins 32k-Fenster, werden die Beschreibungen stufenweise gekürzt
  (`voll` → `ohne Merkmalslisten` → `knapp` → `nur Klassentext`), erst danach das Feld selbst —
  und die Stufe steht am Ergebnis.
- **Zwei Fragen in einem Aufruf:** Klasse (Choice) und „ist das Zubehör/Ersatzteil?“ (Noul)
  gegen denselben Zustand. Fragen laufen parallel und sind voneinander unabhängig — Jev nutzt
  die Klassenantwort nicht als Kontext. Genau deshalb ist die Zubehörfrage ein echtes
  Gegensignal und keine Nacherzählung der ersten Antwort. Kostet ein paar Token, kaum Zeit.
- **Merkmale, soweit sie eine feste Antwortmenge haben:** logische Merkmale (Typ L) als Noul,
  Wertelisten (Typ A) als Choice über die EV-Codes, alle Merkmale eines Artikels in einem Aufruf.
- **Kalibrierte Konfidenz als Review-Signal.** Geminis Konfidenz liegt auch bei strittigen
  Fällen bei 0.90–0.95 und taugt als Schwelle nichts (siehe Stand-Block). Jevs Konfidenz kommt
  aus der Form der Wahrscheinlichkeitsverteilung. Ob sie besser trennt, misst der Vergleich —
  „Konfidenz bei richtiger gegen falsche Antwort“ ist genau diese Zahl.

**Jev bekommt nicht:**

- **`ingest`** (PDF → Artikel): Artikel aus Katalogseiten zu extrahieren heißt Text erzeugen.
  Jev wählt nur aus Vorgegebenem. Bleibt Gemini.
- **Numerische Merkmale (Typ N) und Bereiche (Typ R):** „Einbauhöhe 130 mm“ steht in keiner
  Optionsliste. Bleibt Gemini; jedes ausgelassene Merkmal trägt im Vergleich die Begründung.
- **Quellzitate:** Jev liefert keinen Beleg, weil er keinen Text erzeugt. **Die Quellzitat-Pflicht
  gilt trotzdem:** wählt Jev einen Merkmalswert, kommt der Beleg aus Geminis Antwort zum selben
  Merkmal — und wenn Gemini dafür kein Zitat hat, ist der Wert nicht exportfähig und geht ins
  Review. Ein von Jev gewählter Wert ohne Katalogbeleg landet nie im BMEcat.
- **`report.md`:** Fließtext für den Hersteller. Bleibt Gemini.

**Offene Frage, die der Vergleich beantworten soll:** Jev ist laut Modellkarte primär auf
Englisch trainiert („andere Sprachen werden verarbeitet, aber nicht gleich gut“). Kundenkataloge
sind deutsch, die ETIM-Klassentexte englisch. Der Artikelzustand geht darum unübersetzt hinein,
mit einem Sprachhinweis — gemessen wird, nicht angenommen.

## Befehle

Alle `make`-Ziele rufen Python aus `.venv/` auf — auf macOS gibt es kein `python`,
nur `python3`, und ohne aktivierte Umgebung schlägt jeder direkte Aufruf fehl.
Darum ist `make` der dokumentierte Weg; `python -m etim …` setzt ein aktiviertes
venv voraus (`source .venv/bin/activate`).

```
make setup                      # venv + deps (einmalig)
make env                        # Zugangsdaten gefuehrt eintragen (verdeckte Eingabe)
make env-show                   # zeigen, was eingetragen ist (Secrets maskiert)
make jev                        # Worker deployen, .env einrichten
make jev-check                  # echter Mini-Aufruf: antwortet Jev?
make versions                   # welche ETIM-Versionen liegen vor
make load-model ETIM=9.0        # eine bestimmte Version laden (ohne ETIM=: Vorgabe)
make studio                     # Cockpit im Browser
make compare JOB=strawa         # Gemini gegen Jev (nur Messung)
python -m etim classify --job strawa --model jev   # Jev als Klassifizierer der Pipeline
make reference JOB=strawa       # Gerüst für reference.json
make test                       # pytest mit DRY_RUN (läuft ohne API-Key)
python -m etim inspect data/etim           # zeigt, welche CSV-Dateien/Spalten da sind
python -m etim load-model data/etim        # baut data/cache/etim.sqlite + Embeddings
python -m etim run katalog.pdf --job demo  # ganze Pipeline
python -m etim review out/demo             # Review-Tabelle (CSV) für Artikel unter Schwelle
python -m etim ui out/demo                 # Prüf-Cockpit im Browser (stdlib-Server, kein Build)
python -m etim studio                      # Cockpit über allen Jobs: hochladen, Lauf starten
python -m etim compare --job demo          # Gemini gegen Jev auf derselben Kandidatenliste
python -m etim compare --job demo --reuse-gemini --features
python -m etim reference out/demo          # Gerüst für reference.json (Klassen bleiben leer)
bash scripts/setup_jev.sh                  # Worker deployen, Secret setzen, .env schreiben, prüfen
python scripts/make_demo_data.py           # Beispieldaten der Oberfläche neu erzeugen
python scripts/build_preview.py            # Oberfläche als einzelne HTML-Datei zum Teilen
```

## Stand / Nächste Schritte (aktualisiere diesen Block nach jeder Session)

- [x] **Jev als zweites Modell eingebaut, Dashboard erweitert** (21.9.2026). Neu: `etim/jev.py`
      (einziger Aufrufort, wie `llm.py` für Gemini), `etim/compare.py`, `etim/studio.py`,
      `worker/` und im Cockpit die Ansichten **Katalog** (Drag & Drop, Lauf starten, Fortschritt,
      Fehler im Browser) und **Vergleich** (Kennzahlen je Modell, beide Antworten je Artikel
      nebeneinander). Testlage: **38 statt 5 Tests**, alle grün im Trockenlauf; der Upload-Weg
      ist inklusive HTTP-Schicht getestet (falscher Inhalt, Kollision, Traversal, fremder Origin,
      zweiter Lauf auf demselben Job). Die Oberfläche ist im Browser durchgespielt worden —
      Upload → Job → Lauf → Vergleich, hell und dunkel, und die geteilte Vorschau ohne Server.
      Jev-Anfrageform gegen die Doku geprüft und in `tests/test_jev.py` festgenagelt:
      `POST /ai/run` mit `{"model":"typesafe/jev","input":{state,questions}}`, Antwort unter
      `result`; Choice max. 255 Optionen; Noul liefert **nur** `noul` (keine `confidence`);
      Score 2–10 Stufen; Kontext 32k; Output kostenlos.
- [x] **Jev ist jetzt ein echtes Klassifizierungsmodell, nicht nur Vergleichsbeiwerk**
      (21.9.2026, auf Nachfrage von David). Vorher lief Jev ausschliesslich in `compare.py`;
      `classify.run` war Gemini-only, Jev kam also nie in `classified.json` und damit nie in
      Export oder Merkmale an. Jetzt: `ETIM_CLASSIFIER` bzw. `--model gemini|jev|both`,
      im Cockpit unter **Katalog** als drei Karten je Lauf umschaltbar.
      **Strukturell aufgeraeumt:** die Jev-Klassenwahl (`ask_jev`, `class_criteria`,
      `product_state`, die Optionsbeschreibungen) ist von `compare.py` nach `classify.py`
      gewandert — sie ist Klassifizierung, nicht Vergleich, und compare haengt ohnehin schon
      an classify, die Abhaengigkeit bleibt also in einer Richtung. compare reicht die Namen
      weiter, damit der Vergleich und seine Tests unveraendert damit arbeiten.
      **Entscheidung bei `both`:** `classified.json` und damit der Export kommen weiter von
      Gemini, der Vergleich landet nur in `compare.json`. Ein Messlauf soll nicht unbemerkt
      die Lieferdatei aendern; faellt die Entscheidung fuer Jev, wird mit `--model jev` neu
      klassifiziert. Steht auch so in der Oberflaeche.
      `ClassifiedProduct` traegt jetzt `model` und `simulated`; die Oberflaeche zeigt beides
      in der Kopfzeile, auf der Uebersicht und in der Jobliste — **ohne Zugang oder im
      Trockenlauf klar als simuliert markiert**, wie bisher.
      Jevs `reasoning` ist kein Modelltext (den gibt es nicht), sondern ein aus der
      Wahrscheinlichkeitsverteilung gebauter Satz; erfundene EC-Codes kann er nicht
      enthalten, weil nur Optionen aus dem Kandidatenfeld vorkommen.
      Tests: **48 statt 45**, inklusive Jev-Ausfall waehrend eines echten Klassifizierungslaufs.
- [x] **Cockpit lieferte nach einem `git pull` die alte Oberflaeche aus** (21.9.2026).
      David meldete "Jev pruefen funktioniert nicht" — der Knopf war nach dem Pull schlicht
      nicht da. Ursache: `SimpleHTTPRequestHandler` schickt nur `Last-Modified` und kein
      `Cache-Control`. Browser leiten daraus eine eigene Haltbarkeit ab (Safari besonders
      grosszuegig) und liefern `app.js` aus dem Cache, ohne nachzufragen — neue Knoepfe fehlen
      dann ohne jede Fehlermeldung. `end_headers()` setzt jetzt `no-store, must-revalidate`
      fuer alles, statisch wie JSON; auf 127.0.0.1 bringt Zwischenspeichern ohnehin nichts.
- [x] **Weg ohne Terminal** (21.9.2026, Wunsch von David). Auf die Frage, ob sich das Dashboard
      ueber die Worker-URL bedienen laesst: **nein, und das ist auch nicht der richtige Weg** —
      die Pipeline braucht je ETIM-Version ~69 MB Embeddings und ~18 MB SQLite, numpy fuer das
      Retrieval, pypdf/lxml, und die hochgeladenen Kataloge sind Kundendaten, die laut
      Projektregel lokal bleiben. Ein Worker hat ein Skriptlimit im einstelligen MB-Bereich und
      laeuft JS/WASM. Was David eigentlich will — nicht mehr ins Terminal —, geht aber lokal:
      `ETIM-Cockpit starten.command` zum Doppelklicken (richtet beim ersten Mal ein, startet,
      oeffnet den Browser, erkennt ein bereits laufendes Cockpit), plus zwei neue Knoepfe im
      Cockpit: **ETIM-Version laden** (sucht das ZIP, entpackt, baut SQLite und Embeddings, mit
      Fortschritt) und **Jev pruefen** (echter Mini-Aufruf). Terminal bleibt noetig fuer
      `make env` und `git pull`.
- [x] **Drei Fehler aus Davids erstem 250-Artikel-Lauf behoben** (21.9.2026). Jeder Jev-Aufruf
      schlug mit `URLError` fehl, wurde dreimal mit Backoff wiederholt (~11 s je Artikel) und
      der Lauf mahlte weiter — bei 250 Artikeln rund 45 Minuten fuer 250 identische Fehler.
      1. **Der Grund war unsichtbar.** `except URLError` gab nur `type(e).__name__` aus, also
         "URLError". Der eigentliche Grund steht in `e.reason` und wurde weggeworfen. Jetzt
         nennt `_why()` ihn im Klartext und haengt bei den drei haeufigen Faellen (TLS, DNS,
         Verbindung abgelehnt) den naechsten Schritt an.
      2. **Vermuteter Ausloeser: TLS auf macOS.** Python von python.org bringt dort keinen
         eigenen Zertifikatsspeicher mit; `urllib` scheitert an der Pruefung, waehrend curl und
         `google-genai` laufen, weil die ihren eigenen mitbringen (Gemini lief in Davids Lauf
         ja durch). `_ssl_context()` nimmt jetzt certifi, falls vorhanden; `ETIM_CA_BUNDLE`
         ueberschreibt das.
      3. **Kein Abbruch bei Dauerausfall.** Neu `JevUnavailable` und ein Zaehler: nach
         `ETIM_JEV_MAX_FAILURES` (5) Fehlschlaegen in Folge bricht der Lauf mit Ansage ab
         statt weiterzulaufen. Ein Erfolg setzt den Zaehler zurueck, ein einzelner Aussetzer
         vergiftet den Lauf also nicht. Tests: **65 statt 61**.
      **Offen und wichtig:** es gibt weiterhin kein Checkpointing. Wer einen Lauf abbricht,
      verliert auch die bereits fertigen Gemini-Antworten, weil `classified.json` und
      `compare.json` erst am Ende geschrieben werden. Bei 250 Artikeln ist das teuer genug,
      um es als naechstes anzugehen.
- [ ] **NICHT GEMESSEN: der echte Lauf fehlt — diese Umgebung kann ihn nicht fahren.** Der Code
      ist vollständig und getestet, aber jede Zahl im Dashboard stammt bisher aus dem Trockenlauf
      und ist als `simuliert` gekennzeichnet. Vier Gründe, alle Umgebung, keiner Code:
      1. **Netz-Policy:** `api.cloudflare.com`, `api.typesafe.ai`, `*.workers.dev`,
         `developers.cloudflare.com` und `docs.typesafe.ai` antworten alle mit **403 auf CONNECT**.
         Ein Jev-Aufruf ist von hier aus unmöglich — weder über den Worker noch direkt.
         (`generativelanguage.googleapis.com` ist offen, Gemini ginge also.)
      2. **Kein `GEMINI_API_KEY`** — es gibt keine `.env` im Container.
      3. **Keine ETIM-Daten.** `data/etim/` und `data/cache/` sind gitignored und leer; ohne die
         5.640 Klassen und die Embeddings gibt es kein Kandidatenfeld, aus dem Jev wählen könnte.
      4. **Weder das PDF noch der Job `strawa`.** `~/Documents/etim-testkataloge/` existiert hier
         nicht, `out/` ist leer. Beides ist gitignored und kam mit dem frischen Clone nicht mit.
      **Was David tun muss, damit die Zahlen entstehen** (lokal, wo Netz und Daten da sind):
      `cd worker && npx wrangler secret put ETIM_PROXY_SECRET && npx wrangler deploy`, dann in
      `.env` `ETIM_JEV_WORKER_URL`/`ETIM_JEV_WORKER_SECRET` eintragen, `python -m etim load-model
      data/etim`, `python -m etim studio`, das strawa-PDF ins Ablagefeld ziehen — und für den
      Job `strawa` zusätzlich `out/strawa/reference.json` anlegen
      (`{"<artikelnr>": "EC004089", ...}`), sonst gibt es bewusst keine Trefferquote.
      **Bis dahin steht im Dashboard kein Jev-Ergebnis, das keines ist.**
- [x] **Cloudflare-Worker geklärt: `etim-converter` ist der richtige** (David, 21.9.2026).
      `worker/wrangler.toml` heißt jetzt so; ein Deployment ersetzt den bestehenden Worker
      absichtlich. Sein bisheriger Inhalt war aus dieser Sitzung nicht lesbar
      (`workers_get_worker_code` liefert `null`, `api.cloudflare.com` ist egress-gesperrt),
      ist laut David aber genau diese Vorschaltung. Kein offener Proxy: nur `POST /jev` und
      `GET /health`, beide mit Shared Secret (SHA-256 → `timingSafeEqual`), Modell fest im
      Quelltext, Rumpf muss die Form eines System-One-Aufrufs haben.
- [x] **macOS-Stolperstein behoben** (21.9.2026, aus Davids erstem Anlauf gelernt).
      `python -m etim …` schlaegt auf seinem Mac fehl: dort gibt es nur `python3`, und ohne
      aktiviertes venv findet die Shell gar nichts. Das Makefile rief ebenfalls blankes
      `python` auf. Jetzt laeuft alles ueber `.venv/bin/python`, und es gibt Ziele fuer den
      ganzen Weg: `make setup | jev | load-model | studio | compare JOB=x | reference JOB=x`.
      Fehlt das venv, sagt `make` das im Klartext statt mit einem Pfadfehler.
      In der README steht eine kleine Fehlertabelle fuer macOS (Xcode-Lizenz sperrt `git`
      und `make`, falscher Ordner, fehlendes `python`).
- [x] **ETIM 8.0 und 9.0 an echten Daten verifiziert** (21.9.2026, David hat die CSV-Releases
      geliefert). Der Loader war bis dahin nur gegen 10.0 geprueft — er laeuft gegen beide
      aelteren Releases **ohne eine einzige Alias-Anpassung** durch:

      | Release | Gruppen | Klassen | Synonyme | Merkmale | Werte | Klassenmerkmale |
      |---|---|---|---|---|---|---|
      | ETIM 8.0 (2020-11-09) | 168 | 5.415 | 30.604 | 15.748 | 14.796 | 68.258 |
      | ETIM 9.0 (2022-12-05) | 167 | 5.554 | 33.684 | 16.728 | 15.656 | 73.085 |
      | ETIM 10.0 (2024-12-05) | 159 | 5.640 | 37.058 | 17.377 | 16.163 | 76.625 |

      Abweichungen gegenueber 10.0, alle unkritisch: 8.0/9.0 sind UTF-16LE **mit** BOM (10.0
      ohne — `_decode` deckt beides ab); beide haben 8 statt 9 Dateien, es fehlt
      `ETIMFEATUREGROUP.csv` (ohnehin ungenutzt); `ETIMFEATURE.csv` hat nur
      `FEATUREID, FEATUREDESC` ohne `FEATUREGROUPID`.
- [x] **Die Instabilitaet der Klassen-IDs ist jetzt gemessen, nicht behauptet** (8.0 -> 9.0):
      53 Klassen entfallen, 192 kommen dazu, 5.362 bleiben. Davon:
      **275 behalten ihre ID und aendern ihre Bezeichnung** (EC000024 "Installation box for
      underfloor-installation" -> "Device installation insert for subfloor installation"),
      und **1.274 von 5.362 (24 %) behalten ihre ID und aendern ihre Merkmalsliste**.
      **Das ist die stille Luecke**, die der alte Guard nicht gesehen haette: bei gleicher
      ID-Liste waere ein Cache mit veralteten Klassentexten unbemerkt durchgegangen. Behoben —
      `_class_matrix` schreibt die Version in den `.npz` und prueft sie beim Laden; ein Cache
      aus einer anderen Version wird verworfen und neu gerechnet, mit Hinweis im Terminal.
- [x] **ZIPs werden beim Laden selbst entpackt** (21.9.2026). `load-model --etim 8.0` sucht in
      `data/downloads/`, `data/etim/` und `data/` nach einem CSV-Release, dessen Dateiname die
      Version nennt — genau so, wie die Dateien von etim-international.com heissen
      (`ETIM-9.0-ALL-SECTORS-CSV-METRIC-EI-2022-12-05.zip`). Guideline- und IXF-Archive werden
      am Namen ausgeschlossen, Unterordner im ZIP flachgezogen. Kein Entpacken von Hand mehr.
- [ ] **Befund fuer den strawa-Test: die Zielklasse heisst in 8.0/9.0 anders.** EC004089 ist dort
      **"Hydraulic control station"**, in 10.0 **"Hydronic control station"** — Synonyme und die
      56 Merkmale sind in 8.0 und 9.0 identisch. "Hydraulic" liegt naeher an "hydraulisch" als
      "Hydronic", das Retrieval fuer deutsche Hausbezeichnungen koennte gegen 8.0/9.0 also
      **besser** treffen. Das ist eine Hypothese, keine Messung: beim ersten echten Lauf
      denselben Katalog gegen alle drei Versionen laufen lassen und die Raenge vergleichen.
      Nebenbei fuer den Merkmalsvergleich: von den 56 Merkmalen der Klasse sind **42 (75 %)
      vom Typ L oder A**, also von Jev beantwortbar; die uebrigen 14 (Typ N/R) bleiben Gemini.
- [x] **ETIM 8/9/10 waehlbar** (21.9.2026, Wunsch von David: aeltere Versionen zum Testen).
      Neu `etim/versions.py`: Normalisierung (`ETIM-8`/`8`/`8.0` -> `8.0`), Ablage unter
      `data/etim/<version>/`, **je Version eigene `etim-<v>.sqlite` und `class_emb-<v>.npz`**.
      **Praezisierung nach der Messung an echten Daten (siehe naechster Punkt):** die
      urspruengliche Begruendung war zu pauschal. Der bestehende Guard in `_class_matrix`
      vergleicht die Klassen-ID-Liste; zwischen 8.0 und 9.0 unterscheidet die sich (53 Klassen
      weg, 192 neu), ein geteilter Cache haette also einen Neubau ausgeloest, keine falschen
      Daten. Die echte stille Luecke ist eine andere und jetzt geschlossen (s. u.).
      Durchgesetzt an drei Stellen: `etim_version` an jedem Artikel in `classified.json` und
      `enriched.json`; `features.run` bricht ab, wenn die geladene Version nicht zur
      Klassenentscheidung passt; `export_bmecat` schreibt die Version des Jobs, nicht die
      gerade in der `.env` stehende.
      **Bestandsschutz:** liegt nur der alte, unversionierte Cache (`etim.sqlite`,
      `class_emb.npz`) vor, gilt er weiter fuer die Vorgabeversion — Davids frisch gebaute
      5.640 Klassen muessen nicht neu gerechnet werden.
      CLI: `--etim` auf load-model/classify/run/compare, `python -m etim versions` zeigt den
      Zustand aller Versionen. Im Cockpit Karten unter **Katalog**; nicht geladene sind
      ausgegraut und nennen im Tooltip, was fehlt. Tests: **56 statt 48**.
- [x] **`make env-show`** (21.9.2026). David fragte, wo die Cloudflare-Keys eigentlich
      eingetragen seien — er hatte nie welche getippt. Genau richtig: `make jev` erzeugt das
      Secret selbst, hinterlegt es beim Worker und schreibt es in die `.env`; auf dem
      Worker-Weg wird gar kein `CLOUDFLARE_API_TOKEN` gebraucht (wrangler meldet sich per
      Browser an). Der neue Befehl zeigt den gewaehlten Weg und alle Werte, Secrets maskiert.
      **Fallstrick dabei behoben:** `stat -f %Lp` (macOS) vor `stat -c %a` (GNU) zu probieren
      ist falsch — unter Linux ist `stat -f` der Dateisystem-Status und liefert *erfolgreich*
      voellig anderen Text, der Fallback greift also nie. GNU-Form zuerst, beide Faelle geprueft.
- [x] **`make env`: Zugangsdaten gefuehrt eintragen** (21.9.2026). David hatte den
      Cloudflare-Token und wollte ihn moeglichst einfach eintragen — ohne ihn in den Chat zu
      schicken oder die `.env` von Hand zu oeffnen. Der Dialog fragt Gemini-Schluessel,
      Jev-Weg (Cloudflare direkt oder Worker) und Klassifizierungsmodell ab. Secrets werden
      mit `read -s` verdeckt eingelesen, Enter behaelt den alten Wert, Anzeige nur maskiert
      (letzte vier Zeichen). Abgefangen: vertauschte Account ID/Token (32-Hex-Pruefung plus
      Gleichheitstest), mitkopierte Anfuehrungszeichen und Leerzeichen, zu kurzer Token.
      Am Ende laeuft `jev-check` von selbst.
      Die `.env`-Helfer stehen jetzt in `scripts/lib_env.sh` und werden von beiden
      Einrichtungsskripten benutzt — vorher hatte `setup_jev.sh` eine eigene Kopie.
      Werte gehen ueber die Umgebung an `awk`, nie ueber die Kommandozeile (Prozessliste)
      und nie durch `sed` (ein Token mit `&` oder `/` wuerde dort zerlegt).
- [x] **Einrichtung auf einen Befehl eingedampft** (21.9.2026): `bash scripts/setup_jev.sh`
      macht Anmeldung, Secret, Deploy, `.env` und Funktionstest in einem Durchgang; wiederholbar,
      Secret wird nie ausgegeben, `.env.bak` als Sicherung. Dazu `python -m etim reference
      out/<job>`: Gerüst für `reference.json` mit allen Artikelnummern und Bezeichnungen, die
      **Klassen bleiben absichtlich leer** — mit Modellvorschlägen vorbelegt würde der Vergleich
      das Modell gegen seine eigene Antwort messen. Getestet in `tests/test_compare.py`.
      Der `set_key`-Teil des Skripts ist gegen Sonderzeichen im Secret geprüft (`& \ / $`),
      und `python-dotenv` liest solche Werte nachweislich wörtlich.
- [x] **Echte ETIM-Daten liegen vor** (14.9.2026). Der Blocker war reine Netz-Policy, nicht der
      Server: `www.etim-international.com` liefert sowohl in der Remote-Umgebung als auch im
      lokalen Geräte-Shell 403 auf CONNECT. Beschafft wurden die beiden nötigen ZIPs deshalb
      über den Browser (läuft nicht über den Agent-Proxy) und nach `data/downloads/` gelegt;
      `scripts/download_etim.sh` entpackt vorhandene ZIPs auch ohne Download. Release:
      `ETIM-10.0-ALL-SECTORS-CSV-METRIC-EI-2024-12-05.zip` (2,9 MB) + `ETIM-BMEcat-Guideline-V5-0-2`
      (3,2 MB). **Die drei übrigen ZIPs (IXF, IXF-Format, xChange 2.0) fehlen weiterhin** — für
      die aktuelle Pipeline nicht nötig.
      **URL-Änderung:** die BMEcat-Guideline liegt unter `/wp-content/uploads/2021/09/`, nicht
      unter `/2024/12/` — im Skript korrigiert. Die vier anderen URLs sind unverändert gültig
      (per HEAD aus dem Browser geprüft: CSV-ZIP 200).
- [x] **Spaltennamen des echten Release bestätigt** (`inspect`, 14.9.2026). Zwei Abweichungen
      gegenüber der 6-Klassen-Fixture, beide in `etim/model.py` behoben:
      1. **Encoding:** der Release ist **UTF-16LE ohne BOM**. Die alte Decoder-Kette lief auf
         UTF-8 durch (NUL-Bytes sind gültiges UTF-8), alle neun Dateien meldeten
         "line contains NUL". Neu: `_decode()` prüft BOM, dann das NUL-Muster, dann die
         Einbyte-Kandidaten.
      2. **Tabellenname:** die Synonyme heißen `ETIMARTCLASSSYNONYMMAP.csv`, nicht
         `ETIMSYNONYM_EN` — Alias ergänzt. Ohne ihn wurden 37.058 Synonyme still ignoriert,
         was das Retrieval spürbar verschlechtert hätte.
      **Nicht abgewichen:** alle `COLUMN_ALIASES` trafen auf Anhieb. Die Beschreibungsspalten
      tragen kein Sprachsuffix (`ARTCLASSDESC`, nicht `ARTCLASSDESC_EN`); beide Schreibweisen
      standen bereits in den Aliaslisten. Ungenutzt bleibt `ETIMFEATUREGROUP.csv`.
- [x] **`load-model --no-embed` gegen den echten Release durchgelaufen** (14.9.2026):
      groups=159, classes=**5.640**, synonyms=37.058, features=17.377, units=188, values=16.163,
      class_features=76.625, class_feature_values=201.284. Deckt sich mit der Erwartung (~5.500).
- [x] **Embedding-Lauf durchgelaufen** (14.9.2026, Cloud-Umgebung nach Freischaltung von
      `generativelanguage.googleapis.com` in der Domain-Allowlist): **(5640, 3072)**, ~2:45 min,
      Kosten im Cent-Bereich (474k Tokens). Cache: `data/cache/class_emb.npz` (69 MB) +
      `etim.sqlite` (18 MB) — beide gitignored und zu gross fuer den Geraete-Transfer,
      lokal mit `python -m etim load-model data/etim` in Minuten neu gebaut.
- [x] **Stiller Embedding-Bug gefunden und behoben — der wichtigste Fund dieser Session.**
      `llm.embed()` uebergab `contents=[str, str, ...]`. Die API liest das als **einen** Content
      mit mehreren Parts und liefert **ein** Embedding pro Request. Ergebnis war eine Matrix
      `(57, 3072)` — 57 = Anzahl Batches — statt `(5640, 3072)`. Nichts davon wirft einen Fehler:
      `classify.candidates_for` haette `sims` mit 57 Spalten gebildet und `ids[i]` auf die
      **ersten 57 Klassen** abgebildet, also jedem Artikel eine falsche Klasse gegeben. Bei
      `candidates_for` waere zusaetzlich die Produktliste stillschweigend auf einen Artikel je
      Batch zusammengefallen. Fix: jeder Text bekommt ein eigenes Content-Objekt, plus zwei
      Guards (Vektoranzahl == Textanzahl in `embed`, Zeilenzahl == Klassenanzahl beim
      Cache-Laden in `_class_matrix`). **Konsequenz fuer die Notiz weiter unten:** der als
      erfolgreich vermerkte Fixture-Lauf (4/4 korrekt) ist damit fraglich — bei 6 Klassen in
      einem Batch kann er nur einen Kandidaten gesehen haben. Vor Kundeneinsatz neu messen.
- [x] **Rate Limit verstanden und gedrosselt.** Das Embed-Kontingent zaehlt **jeden Text** als
      Request, nicht jeden HTTP-Aufruf: Paid Tier 3.000/min
      (`EmbedContentPerMinutePerProjectPerUserPerModel`). 5.640 Klassen am Stueck laufen nach
      Sekunden in 429. `llm.py` hat jetzt ein Token-Bucket ueber die Texte
      (`ETIM_EMBED_TEXTS_PER_MIN`, Default 2500) und respektiert die vom Server genannte
      Wartezeit. Free Tier war nie das Problem — Billing ist seit 14.9.2026 aktiv.
- [x] **Retrieval gemessen — die wichtigste offene Frage ist beantwortet** (14.9.2026).
      `make eval` bzw. `python scripts/eval_retrieval.py`; Testset:
      `tests/fixtures/retrieval_eval.json`, 20 Artikel (10 SHK, 10 Elektro), jeder
      **deutsch und englisch**, weil Kundenkataloge deutsch sind und die ETIM-Klassentexte
      englisch. Gemessen wird der echte Pfad (`classify.query_text` + `_class_matrix`).

      | Sprache | R@1 | R@3 | R@5 | R@10 | R@20 |
      |---|---|---|---|---|---|
      | deutsch | 55 % | 65 % | 90 % | **100 %** | **100 %** |
      | englisch | 70 % | 95 % | 95 % | **100 %** | **100 %** |

      Schlechtester Rang ueber alle 40 Laeufe: **8** (Wohnungswasserzaehler).
      **Befund: Top-20 traegt, und zwar mit Reserve.** Der Sprachsprung kostet Praezision
      an der Spitze (R@1 55 % statt 70 %, R@3 65 % statt 95 %), aber nicht die Abdeckung.
      Genau dafuer ist die Architektur gebaut — breit abrufen, das LLM entscheiden lassen.
      Eine Uebersetzung der Artikeltexte vor dem Retrieval ist damit **nicht** noetig.
      **Einschraenkung, die mitgelesen werden muss:** n = 20. Null Fehler bei 20 Faellen
      heisst nicht 0 % Fehlerrate — die obere 95-%-Schranke liegt bei rund 14 %. Die Zahl
      taugt als Freigabe fuer die Architektur, nicht als Qualitaetsversprechen an Kunden.
      `TOP_K_CLASSES` bleibt bei 20: R@10 war zwar ebenfalls 100 %, aber auf dieser
      Stichprobengroesse waere das Sparen am Kontext die falsche Optimierung.
- [ ] **ERNUECHTERUNG: erster echter Katalog zeigt, dass die Retrieval-Messung zu optimistisch war.**
      Trockenlauf 14.9.2026 gegen `Preisliste2025_Waermegruppen` von strawa (6 Seiten, 17 Artikel,
      Kapitel-Preisliste — genau das Format, das ein Hersteller schickt).
      **`ingest` war stark:** 17 Artikel, Variantenzeilen korrekt in einzelne Artikelnummern
      aufgeloest (dasselbe Produkt in vier Pumpen-Varianten), Preise und Einbauhoehen mitgenommen,
      Notizen melden Dublette und Leerseiten von selbst. Keine Nacharbeit noetig.
      **`classify` ist der Engpass:** 13 von 17 Artikeln → `class_id = null`, also nicht
      klassifiziert. Ursachen, in dieser Reihenfolge:
      1. **Retrieval-Fehler, nicht ETIM-Luecke.** Die richtige Klasse ist EC004089
         "Hydronic control station" — sie traegt ausdruecklich das Synonym **"Pump group"**.
         Sie landete aber nur bei **3 von 17** Artikeln in den Top-20 (Raenge 11, 16, 19), und
         genau bei diesen dreien hat das Modell sie auch korrekt gewaehlt. Bei den uebrigen 14
         konnte es sie gar nicht waehlen — `null` war dort das richtige Verhalten.
      2. **Warum das Testset das nicht gefunden hat:** `retrieval_eval.json` benutzt generische
         Produktnamen (Kugelhahn, Kabelbinder, Umwaelzpumpe), die sauber ins Englische mappen.
         Echte Kataloge benutzen **Hausabkuerzungen** ("FBR-Regelgruppe 130/6", "FBM-Mischgruppe")
         plus Komponentenrauschen im Namen ("mit Grundfos UPM3 Auto 15-50 130"). Beides kennt
         das Embedding nicht. **R@20 = 100 % gilt nur fuer generische Bezeichnungen.**
      3. **Das Modell erfindet EC-Codes in der Begruendung.** Zur Rechtfertigung von `null` nannte
         es EC011310, EC011246, EC011270, EC011609, EC011299, EC010091 als "die eigentlich
         passende Klasse". Tatsaechlich sind das: Three-way control valve, Sound-absorbing roof
         duct, Solid rubber plate, **Bath**, Single-walled flue gas pipe — und EC010091 existiert
         gar nicht. Gefaehrlich, weil es wie ein ETIM-Befund aussieht.
      4. **Identische Produkte bekommen verschiedene Antworten.** Die vier Varianten derselben
         Regelgruppe (nur andere Pumpenmarke) wurden unterschiedlich klassifiziert. Fuer einen
         Grosshaendler-Datencheck ist genau das der auffaelligste Fehler.
      5. **Konfidenz bestaetigt sich als wertlos:** 0.90–0.95 auch bei `null`.
      Gegenprobe: mit `ETIM_TOP_K=50` fanden 15 von 17 die Klasse im Kandidatenfeld, und von vier
      getesteten Varianten wurden 3 statt 1 korrekt klassifiziert — die Inkonsistenz bleibt.
- [ ] **Daraus die naechsten Schritte, in dieser Reihenfolge:**
      1. **Varianten gruppieren.** Artikel mit gleichem Basisnamen (vor " mit ") einmal
         klassifizieren, Ergebnis auf alle Varianten anwenden. Beseitigt die Inkonsistenz
         deterministisch statt per Prompt und spart hier 4/5 der classify-Calls.
      2. **EC-Codes in `reasoning` pruefen.** Jeden genannten Code gegen die Klassentabelle
         validieren; unbekannte Codes entfernen oder markieren. Kein erfundener Befund darf
         in einen Report an einen Hersteller geraten.
      3. **Retrieval fuer Hausbezeichnungen haerten.** `TOP_K` auf 50 ist die billige Haelfte.
         Die eigentliche Antwort ist wahrscheinlich ein Normalisierungsschritt: den Artikelnamen
         vor dem Embedding auf einen generischen Produkttyp bringen (ein zusaetzlicher billiger
         Call je Artikel) oder lexikalisches Matching auf die Synonymtabelle danebenlegen.
      4. **Testset um echte Katalogartikel erweitern** — die 17 strawa-Artikel sind bereits ein
         besserer Massstab als die 20 konstruierten. Ground Truth mit David klaeren.
- [ ] **Naechste Ausbaustufe der Messung:** die bekannte Falle ist die Abgrenzung
      Hauptprodukt vs. "Accessories/spare parts for …" (ETIM hat davon eigene Klassen,
      siehe DECIDE_PROMPT). Das Testset enthaelt dazu noch keinen einzigen Fall. Vor dem
      ersten Kundenkatalog 5–8 Zubehoerartikel ergaenzen und erneut messen.
- [ ] Modellwahl gegen echte Daten: `gemini-3.5-flash-lite` gegen `gemini-3.8-flash` auf denselben
      Artikeln (Trefferquote, Laufzeit, Kosten je Artikel). Gegen die Fixture waren alle Modelle
      ununterscheidbar; die Guardrails in `features.py` (EV-Code-Whitelist, Quellzitat-Pflicht)
      tragen mehr als die Modellwahl.
- [ ] BMEcat-XSD → `validate` mit echter XSD-Prüfung statt nur Strukturregeln. Der Download
      hängt nicht mehr: `data/schema/bmecat_etim_501.xsd` liegt vor (dazu vier ETIM-7/8/9/10-
      Beispielkataloge und die Guideline als PDF). Nur noch in `validate` einhängen.
- [x] **Review-Lücke geschlossen** (Geschäftsentscheidung von David, 4.9.2026: Abdeckungsschwelle).
      `ETIM_MIN_COVERAGE` (Default 0.30): ein Artikel geht in die Review-Queue, wenn weniger als
      30 % seiner Klassen-Merkmale befüllt sind — das fängt den 0-Merkmale-Fall (GE-RS-20) und
      fast leere Artikel ab, die beim Großhändler-Datencheck ohnehin durchfallen. `EnrichedProduct`
      hat jetzt `coverage`; `report.md` nennt den Grund, `review.csv` bekommt eine `ABDECKUNG`-Zeile,
      der Standard-Export lässt solche Artikel weg. `0` schaltet die Prüfung ab.
- [x] `load-model` (Fixture): CSV → SQLite und echte Embeddings über `gemini-embedding-001`.
- [x] Echter Lauf ohne `ETIM_DRY_RUN` gegen `tests/fixtures/katalog_mini.csv`: 4/4 korrekt
      klassifiziert (inkl. Zubehör-Abgrenzung EC000006 statt EC000001), BMEcat erzeugt,
      `validate` ohne Fehler.
- [ ] Erster echter Katalog (20 Seiten) durch `run` → Trefferquote der Klassen manuell geprüft
- [ ] **Konfidenz ist schwach als Signal.** Die Modelle melden fast durchgehend 0.90–1.00 selbst
      bei strittigen Fällen; die Schwelle 0.75 greift auf Klassenebene praktisch nie. Belastbarer
      wäre ein Counter-Check mit einem zweiten, unabhängigen Modell — Uneinigkeit als Review-Signal
      (CLAUDE.md budgetiert dafür bereits den dritten LLM-Call pro Artikel).
- [x] **Billing im Google-AI-Studio-Projekt aktiv** (David, 14.9.2026). Der Free Tier ist damit
      kein Thema mehr. Modelle stehen auf `gemini-3.8-flash` und `gemini-embedding-2`.
      Preisstand 14.9.2026: 3.8-flash $0,75 Input / $3,75 Output je 1M — guenstiger als
      3.5-flash ($1,50/$9,00) und neuer. **Achtung: Einfuehrungspreis, ab 1.1.2027 $1,50/$7,50.**
      Grobe Rechnung je Vollkatalog (~10k Input, ~1,5k Output je Artikel): 200 Artikel ~$3,
      1.000 ~$13, 5.000 ~$65; mit Batch API die Haelfte. Bei vierstelligen Pilotpreisen unter
      1 % Kostenanteil — der Engpass ist der Durchsatz, nicht der Token-Preis.
- [ ] **Durchsatz:** ~25 s/Artikel seriell. Bei 5.000 Artikeln sind das ~35 h. Für Vollkataloge
      Gemini Batch API (50 % Rabatt, 24-h-Ziel) oder Parallelisierung vorsehen. Ausserdem fehlt
      Checkpointing: bricht ein Lauf spät ab, ist alles verloren.
- [x] **Prüf-Cockpit gebaut** (`python -m etim ui out/<job>`). Vier Ansichten: Übersicht,
      Artikel, Prüfen, Export. Kern ist das Merkmalsregister — je ETIM-Merkmal eine Zeile mit
      Wert, Einheit, Konfidenz und darunter dem Katalogzitat, das den Wert belegt; ohne Beleg
      steht der Grund statt eines Wertes. Freigaben landen in `review.decisions.json`.
      Ohne neue Abhängigkeit: stdlib-Server, token-basiertes CSS, Komponenten in reinem DOM.
      Gegen Beispieldaten und gegen einen echten DRY_RUN-Job geprüft.
      **Offen:** die Korrekturen aus dem Cockpit fliessen noch nicht in den Export zurück —
      `review.decisions.json` wird geschrieben, aber von `export`/`report` nicht gelesen.
      Sinnvoll erst, wenn David das Cockpit einmal an einem echten Katalog benutzt hat.

## Dateien

- `etim/model.py` — lädt ETIM-CSV in SQLite; Klassen, Merkmale, Werte, Einheiten, Synonyme
- `etim/llm.py` — Gemini-Wrapper (JSON-Antworten mit Pydantic-Schema, Embeddings, DRY_RUN)
- `etim/ingest.py` — PDF in Seitenblöcke, Gemini extrahiert Artikel
- `etim/classify.py` — Retrieval + Entscheidung
- `etim/features.py` — Merkmalsbefüllung
- `etim/export_bmecat.py` — XML-Writer
- `etim/validate.py` — XSD/Strukturprüfung
- `etim/report.py` — Markdown-Report + Review-CSV
- `etim/jev.py` — Jev-Wrapper (Choice/Score/Noul, Worker- oder Cloudflare-Transport, DRY_RUN)
- `etim/compare.py` — Gemini gegen Jev: Kandidatenfeld, Kennzahlen, Merkmalsvergleich
- `etim/studio.py` — Upload, Jobanlage, Läufe im Hintergrund (ohne HTTP, darum testbar)
- `etim/ui.py` — Prüf-Cockpit: stdlib-Server, liefert Jobs als JSON, nimmt Upload und Freigaben entgegen
- `worker/` — Cloudflare-Worker als Jev-Vorschaltung (Secret dort, nicht in der App)
- `web/` — Oberfläche. `assets/tokens.css` ist der einzige Ort für Farb-/Typo-/Rasterwerte,
  `assets/components.js` die Komponentenschicht. Kein Build, keine npm-Abhängigkeit.
- `docs/cowork-prompt-etim-download.md` — Prompt für die lokale Cowork-Session (ETIM-Download)
- `etim/cli.py` — Befehle
- `tests/` — läuft offline mit Mini-ETIM-Fixture
