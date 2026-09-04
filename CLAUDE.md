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
- [~] `load-model` lädt die CSV korrekt nach SQLite (mit `--no-embed` grün). Der Embedding-Schritt
      braucht `GEMINI_API_KEY` — in dieser Session nicht gesetzt.
- [ ] Erster echter Katalog (20 Seiten) durch `run` → Trefferquote der Klassen manuell geprüft
- [ ] Echter Lauf ohne `ETIM_DRY_RUN` — blockiert durch fehlenden `GEMINI_API_KEY` in der Remote-Umgebung
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
