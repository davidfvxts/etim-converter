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

- [ ] **BLOCKER: echte ETIM-Daten fehlen weiterhin.** `scripts/download_etim.sh` scheitert in der
      Remote-Umgebung an der Egress-Policy, nicht am Server: `curl -sS "$HTTPS_PROXY/__agentproxy/status"`
      meldet für alle fünf Dateien `connect_rejected` / "gateway answered 403 to CONNECT
      (policy denial)". Der TLS-Tunnel kommt also gar nicht erst zustande — die Domain
      `www.etim-international.com` steht nach wie vor nicht in der Allowlist (auch nicht ohne
      `www.`). Die Proxy-Doku (`/root/.ccr/README.md`) verbietet ausdrücklich, das zu umgehen.
      **Nächster Schritt für David:** entweder die Domain in der Environment-Allowlist freischalten
      (Session-Environment-Einstellungen) oder die ZIPs manuell nach `data/downloads/` legen —
      `etim10-csv.zip` und `bmecat-guideline.zip` reichen; das Skript entpackt vorhandene ZIPs
      auch ohne Download.
      **Solange dieser Punkt offen ist, sind die vier folgenden Punkte technisch nicht bearbeitbar.**
- [ ] Spaltennamen des echten Release bestätigen (`inspect`): `TABLE_ALIASES`/`COLUMN_ALIASES` in
      `etim/model.py` sind bis heute nur gegen die handgebaute 6-Klassen-Fixture geprüft.
- [ ] `load-model` gegen den echten Release (~5.500 Klassen statt 6). Achtung: ~5.500 Embedding-Calls,
      auf dem Free Tier vermutlich nicht durchführbar.
- [ ] **Retrieval messen (wichtigste offene Frage).** Bei 6 Fixture-Klassen ist Top-20 die ganze
      Liste, Retrieval wird also nie geprüft. Bei ~5.500 Klassen entscheidet sich hier alles:
      landet die richtige Klasse nicht in den Top-20, kann kein nachgelagertes Modell das
      reparieren. Geplant: 4 Fixture-Artikel + 10–15 realistische SHK-/Elektro-Artikel,
      Recall@5/@20, `gemini-embedding-001` gegen `gemini-embedding-2`.
- [ ] Modellwahl gegen echte Daten: `gemini-3.5-flash-lite` gegen `gemini-3.8-flash` auf denselben
      Artikeln (Trefferquote, Laufzeit, Kosten je Artikel). Gegen die Fixture waren alle Modelle
      ununterscheidbar; die Guardrails in `features.py` (EV-Code-Whitelist, Quellzitat-Pflicht)
      tragen mehr als die Modellwahl.
- [ ] BMEcat-XSD aus der Guideline-ZIP nach `data/schema/` → `validate` mit echter XSD-Prüfung
      statt nur Strukturregeln. Hängt am selben Download.
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
