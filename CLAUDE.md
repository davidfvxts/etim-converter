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
classify products  -> classified.json        (Embedding-Retrieval Top-20 -> LLM wählt Klasse -> Konfidenz)
features classified -> enriched.json         (LLM füllt ETIM-Merkmale aus Wertelisten, mit Quellzitat)
export   enriched  -> catalog.bmecat.xml     (BMEcat 2005 + ETIM)
validate xml       -> validation.json        (XSD wenn vorhanden, sonst Strukturregeln)
report   enriched  -> report.md              (Vollständigkeit, Konfidenzen, Review-Queue)
run      = alles nacheinander
ui       enriched  -> Prüf-Cockpit im Browser (Freigaben -> review.decisions.json)
```

## Wichtige Regeln

- **ETIM-Daten liegen in `data/etim/`** (CSV-Export von etim-international.com, ETIM 10.0 English,
  Lizenz ODC-By — Attribution in README behalten). Die deutsche Sprachversion ist
  Mitgliederleistung von ETIM Deutschland: nicht herunterladen, nicht einbauen, bis David
  die Lizenzfrage geklärt hat. Prompts dürfen deutsch sein; Klassen-/Merkmalstexte sind englisch.
- **Nie Merkmalswerte erfinden.** Jede gefüllte Eigenschaft braucht `source` (Zitat aus dem Katalog)
  oder wird `null` mit `reason`. Lieber leer als falsch — Großhändler prüfen.
- **Konfidenz < 0.75 → Review-Queue**, nicht exportieren ohne Freigabe.
- Alle LLM-Aufrufe laufen über `etim/llm.py`. Kein direkter SDK-Aufruf woanders.
  `ETIM_DRY_RUN=1` ersetzt LLM-Aufrufe durch deterministische Fakes (für Tests).
- Kosten: pro Artikel ≤ 3 LLM-Calls (classify, features, ggf. counter-check). Batching bevorzugen.
- Keine neuen Abhängigkeiten ohne Grund. Stack: python 3.11+, google-genai, pydantic, lxml, pypdf, numpy.

## Befehle

```
make setup                      # venv + deps
make test                       # pytest mit DRY_RUN (läuft ohne API-Key)
python -m etim inspect data/etim           # zeigt, welche CSV-Dateien/Spalten da sind
python -m etim load-model data/etim        # baut data/cache/etim.sqlite + Embeddings
python -m etim run katalog.pdf --job demo  # ganze Pipeline
python -m etim review out/demo             # Review-Tabelle (CSV) für Artikel unter Schwelle
python -m etim ui out/demo                 # Prüf-Cockpit im Browser (stdlib-Server, kein Build)
python scripts/make_demo_data.py           # Beispieldaten der Oberfläche neu erzeugen
python scripts/build_preview.py            # Oberfläche als einzelne HTML-Datei zum Teilen
```

## Stand / Nächste Schritte (aktualisiere diesen Block nach jeder Session)

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
- [ ] **Erste echte Retrieval-Stichprobe** (5 Anfragen gegen alle 5.640 Klassen, Top-3):
      Umwaelzpumpe, Kabelbinder und Heizkoerperventil sauber auf Platz 1. Kugelhahn landete auf
      **Platz 2** hinter "Gas valve", LED-Panel gar nicht in den Top-3 ("Pendant luminaire" vorn).
      Lesart: **Top-1 ist nicht verlaesslich, Top-20 (der Pipeline-Wert) sehr wahrscheinlich schon.**
      Das stuetzt die bestehende Architektur — Retrieval breit, Entscheidung beim LLM. Die
      geplante Recall@5/@20-Messung bleibt trotzdem der naechste inhaltliche Schritt.
- [x] **Modelle auf den neuesten Stand gesetzt** (14.9.2026, Entscheidung David: neuestes und
      effizientestes Modell). Modellliste live gegen die API geprüft:
      `GEMINI_MODEL=gemini-3.8-flash` (neuestes Flash; darüber liegt nur noch
      `gemini-3.1-pro-preview`) und `GEMINI_EMBED_MODEL=gemini-embedding-2` (seit kurzem GA,
      löst `gemini-embedding-001` ab, ebenfalls 3072 Dim — Cache-Format bleibt gleich,
      der `.npz`-Cache muss aber neu gebaut werden).
      **Free-Tier-Vorbehalt bleibt:** `gemini-3.8-flash` hat dort 20 Anfragen/Tag.
- [ ] **Retrieval messen (wichtigste offene Frage).** Bei 6 Fixture-Klassen ist Top-20 die ganze
      Liste, Retrieval wird also nie geprüft. Bei ~5.500 Klassen entscheidet sich hier alles:
      landet die richtige Klasse nicht in den Top-20, kann kein nachgelagertes Modell das
      reparieren. Geplant: 4 Fixture-Artikel + 10–15 realistische SHK-/Elektro-Artikel,
      Recall@5/@20, `gemini-embedding-001` gegen `gemini-embedding-2`.
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
- [ ] **Billing im Google-AI-Studio-Projekt aktivieren (Geschäftsentscheidung für David).**
      Der Key läuft auf dem Free Tier: `gemini-3.8-flash` hat dort 20 Anfragen/Tag, die
      übrigen Flash-Modelle teilen sich knappe Kapazität und antworten zeitweise mit 503.
      Ein Kundenkatalog mit 200–5.000 Artikeln braucht 400–10.000 Calls — auf dem Free Tier
      unmöglich, unabhängig vom Modell. Der reine Token-Preis wäre mit 20–40 $ je
      Vollkatalog (Batch-API: die Hälfte) nicht das Problem.
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
- `etim/ui.py` — Prüf-Cockpit: stdlib-Server, liefert den Job als JSON, nimmt Freigaben entgegen
- `web/` — Oberfläche. `assets/tokens.css` ist der einzige Ort für Farb-/Typo-/Rasterwerte,
  `assets/components.js` die Komponentenschicht. Kein Build, keine npm-Abhängigkeit.
- `docs/cowork-prompt-etim-download.md` — Prompt für die lokale Cowork-Session (ETIM-Download)
- `etim/cli.py` — Befehle
- `tests/` — läuft offline mit Mini-ETIM-Fixture
