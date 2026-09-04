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
```

## Stand / Nächste Schritte (aktualisiere diesen Block nach jeder Session)

- [~] `inspect` läuft; gegen die Mini-Fixture werden alle 8 Tabellen erkannt, Aliase passen.
      **Offen:** der echte ETIM-10.0-CSV-Release lag in dieser Session nicht vor (`data/etim/` ist
      gitignored, kam also weder über Git noch über das ZIP mit; `scripts/download_etim.sh` scheitert,
      weil `etim-international.com` nicht in der Netzwerk-Allowlist der Remote-Umgebung steht).
      Spaltennamen des echten Release sind damit **noch nicht bestätigt**.
- [x] `load-model` durchgelaufen: CSV → SQLite und echte Embeddings über `gemini-embedding-001`
      (6 Klassen × 3072 Dimensionen im Cache).
- [x] Echter Lauf ohne `ETIM_DRY_RUN` gegen `tests/fixtures/katalog_mini.csv`: alle 4 Artikel korrekt
      klassifiziert (inkl. Zubehör-Abgrenzung EC000006 statt EC000001), Merkmale mit Quellzitat,
      BMEcat erzeugt, `validate` ohne Fehler.
- [ ] Erster echter Katalog (20 Seiten) durch `run` → Trefferquote der Klassen manuell geprüft
- [ ] **Review-Regel-Lücke (Geschäftsentscheidung für David):** `needs_review` prüft nur *gefüllte*
      Werte unter der Schwelle. Ein Artikel, bei dem *kein einziges* Merkmal befüllt wurde, gilt
      als freigabefähig und wird exportiert — im Testlauf mit gemini-3.5-flash traf das GE-RS-20.
      `validate` warnt zwar ("ETIM-Klasse ohne Merkmale"), aber die Warnung blockiert nichts.
      Soll ein Artikel ohne Merkmale automatisch in die Review-Queue?
- [ ] **Konfidenz ist schwach als Signal.** Die Modelle melden fast durchgehend 0.90–1.00 selbst
      bei strittigen Fällen; die Schwelle 0.75 greift auf Klassenebene praktisch nie. Belastbarer
      wäre ein Counter-Check mit einem zweiten, unabhängigen Modell — Uneinigkeit als Review-Signal
      (CLAUDE.md budgetiert dafür bereits den dritten LLM-Call pro Artikel).
- [ ] **Modellwahl gegen echte ETIM-Daten messen.** Bei 6 Fixture-Klassen ist Top-20 die ganze
      Liste — Retrieval wird nicht geprüft. Erst bei ~5.500 Klassen entscheidet sich, ob
      `gemini-3.5-flash-lite` (5x schneller, keine Thinking-Tokens) reicht oder ob es 3.8-flash
      braucht. Ebenso `gemini-embedding-2` gegen `gemini-embedding-001` vergleichen.
- [ ] **Durchsatz:** ~25 s/Artikel seriell. Bei 5.000 Artikeln sind das ~35 h. Für Vollkataloge
      Gemini Batch API (50 % Rabatt, 24-h-Ziel) oder Parallelisierung vorsehen. Ausserdem fehlt
      Checkpointing: bricht ein Lauf spät ab, ist alles verloren.
- [ ] BMEcat-XSD aus der ETIM-Guideline-ZIP nach `data/schema/` → `validate` mit XSD
- [ ] Review-UI (später; erst wenn ein Kunde zahlt)

## Dateien

- `etim/model.py` — lädt ETIM-CSV in SQLite; Klassen, Merkmale, Werte, Einheiten, Synonyme
- `etim/llm.py` — Gemini-Wrapper (JSON-Antworten mit Pydantic-Schema, Embeddings, DRY_RUN)
- `etim/ingest.py` — PDF in Seitenblöcke, Gemini extrahiert Artikel
- `etim/classify.py` — Retrieval + Entscheidung
- `etim/features.py` — Merkmalsbefüllung
- `etim/export_bmecat.py` — XML-Writer
- `etim/validate.py` — XSD/Strukturprüfung
- `etim/report.py` — Markdown-Report + Review-CSV
- `etim/cli.py` — Befehle
- `tests/` — läuft offline mit Mini-ETIM-Fixture
