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
- [x] **Schritt 1 gebaut: Varianten werden gruppiert** (14.9.2026, `etim/classify.py`).
      `base_name()` schneidet alles ab dem ersten " mit " ab, `group_variants()` fasst
      gleiche Basisnamen zusammen (normalisiert: Kleinschreibung, Mehrfach-Leerzeichen,
      Satzzeichen am Ende), `representative()` schickt **den Basisnamen statt des
      Katalognamens** in Query-Embedding *und* Prompt. Entschieden wird einmal je Gruppe,
      das Ergebnis (Kandidaten, Entscheidung, needs_review) wird auf alle Varianten kopiert —
      als eigene Objekte, nicht als geteilte Instanz. Neu in `ClassifiedProduct`:
      `variant_group`, `variant_of`. Die Konsole nennt jede Mehrfachgruppe mit ihrer Klasse,
      man sieht die Determinismusfrage also ohne Diff.
      **Zweiter Effekt, absichtlich:** das Komponentenrauschen ("mit Grundfos UPM3 Auto
      15-50 130") faellt damit auch aus dem Retrieval-Query — genau die Ursache Nr. 2 der
      Ernuechterung oben. Das ist kein Prompt-Tuning, sondern eine Eingabekuerzung.
- [x] **Schritt 2 gebaut: EC-Codes in `reasoning` werden geprueft**
      (`check_reasoning_codes()`). Drei Faelle: (a) Code existiert nicht → `[existiert nicht
      in ETIM-10.0]` dahinter, und wenn es der *gewaehlte* Code war, wird er auf `null`
      gesetzt und die Konfidenz gedeckelt; (b) Code existiert, stand aber nicht in der
      Kandidatenliste → seine **wirkliche** Beschreibung wird angehaengt, damit
      `EC011609 ("Bath")` als das dasteht, was es ist; (c) Kandidat oder gewaehlte Klasse →
      unveraendert. Ungueltige `runner_up` werden verworfen. Alle gefundenen Fantasiecodes
      stehen in `ClassifiedProduct.invented_codes` und in der Schlusszeile der Konsole.
      Das Pruef-Cockpit zeigt `decision.reasoning` direkt an, der Hinweis erreicht also
      den Menschen, der freigibt.
- [ ] **Noch nicht gemessen: der Gegenlauf gegen `out/strawa` steht aus.** Er konnte in der
      Cloud-Session nicht laufen — `data/etim/`, `data/cache/`, `out/` und die Katalog-PDF
      sind gitignored und liegen nur auf Davids Geraet, und dort ist auch der API-Key.
      Lokal reproduzieren (die Konsole beantwortet beide Fragen direkt):
      `python -m etim load-model data/etim` (einmalig), dann
      `ETIM_TOP_K=50 python -m etim classify --job strawa`.
      Vorher `out/strawa/classified.json` als `classified.vorher.json` wegkopieren.
      Zu vergleichen: Zahl der Artikel mit Klasse (vorher 4/17), ob die vier Varianten der
      Regelgruppe jetzt in einer Gruppe stehen, und ob `ETIM_TOP_K=50` gegenueber 20
      ueberhaupt noch etwas aendert, wenn der Basisname statt des Variantennamens abgefragt wird.
- [ ] **Schritt 3: Retrieval fuer Hausbezeichnungen haerten.** `TOP_K` auf 50 ist die billige
      Haelfte. Die eigentliche Antwort ist wahrscheinlich ein Normalisierungsschritt: den
      Artikelnamen vor dem Embedding auf einen generischen Produkttyp bringen (ein zusaetzlicher
      billiger Call je Artikel) oder lexikalisches Matching auf die Synonymtabelle danebenlegen.
      Die Variantengruppierung nimmt davon bereits ein Stueck vorweg (Komponentenrauschen weg),
      aber "FBR-Regelgruppe" bleibt fuer das Embedding eine Hausabkuerzung.
- [ ] **Schritt 4: Testset um echte Katalogartikel erweitern** — die 17 strawa-Artikel sind
      bereits ein besserer Massstab als die 20 konstruierten. Ground Truth mit David klaeren.
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
- `etim/ui.py` — Prüf-Cockpit: stdlib-Server, liefert den Job als JSON, nimmt Freigaben entgegen
- `web/` — Oberfläche. `assets/tokens.css` ist der einzige Ort für Farb-/Typo-/Rasterwerte,
  `assets/components.js` die Komponentenschicht. Kein Build, keine npm-Abhängigkeit.
- `docs/cowork-prompt-etim-download.md` — Prompt für die lokale Cowork-Session (ETIM-Download)
- `etim/cli.py` — Befehle
- `tests/` — läuft offline mit Mini-ETIM-Fixture
