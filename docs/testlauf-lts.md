# Testlauf LTS — Protokoll

Dieses Protokoll hält jede Iteration fest: was geändert wurde, warum, und was es
auf den 30 Entwicklungsartikeln gebracht hat. Am Ende steht der Holdout-Vergleich
Gemini gegen Jev und eine Empfehlung, welches Modell klassifiziert.

**Stand 21.9.2026: es ist noch keine einzige Zahl gemessen.** Warum, steht unten
unter „Blocker". Die Spalte „Ergebnis auf den 30" bleibt bis dahin leer — eine
geschätzte Zahl wäre hier schlimmer als gar keine, weil dieses Dokument die
Grundlage für die Abnahmeentscheidung ist.

---

## Blocker: in dieser Umgebung ist nichts messbar

Die Messung braucht drei Dinge. In der Remote-Session vom 21.9.2026 fehlten alle drei.

| Was | Zustand | Nachweis |
|---|---|---|
| ETIM-9.0-Daten | fehlen, für **alle** Versionen | `python -m etim versions` meldet 8.0/9.0/10.0 je „Kein CSV-Release" |
| Zugang zu etim-international.com | gesperrt | `CONNECT tunnel failed, response 403`; der Agent-Proxy führt den Host nicht in der Allowlist |
| GEMINI_API_KEY | nicht gesetzt | keine `.env` im Container; `generativelanguage.googleapis.com` antwortet `PERMISSION_DENIED — callers without established identity` |
| Jev-Zugang | nicht gesetzt **und** gesperrt | weder `ETIM_JEV_WORKER_URL` noch `CLOUDFLARE_API_TOKEN`; `api.cloudflare.com` ist ebenfalls blockiert |
| `data/testsets/*.xlsx` | fehlen | gitignored und zu Recht nicht im Repo — sie liegen nur auf Davids Rechner |

Damit sind **Ziel 1 (Jev läuft wirklich)** und **Ziel 2 (Abnahmewerte auf dem
Holdout)** in dieser Session nicht erreichbar. Kein Lauf, keine Kosten, keine
Zahlen. Das Budget von 25 $ ist unberührt — es wurde kein einziger API-Aufruf
abgesetzt.

**Was es braucht, um weiterzumachen** (eines von beidem):

1. *Lokal bei David.* Alles ist vorbereitet, die Befehle stehen unten unter
   „Ablauf". Die Testdateien und der Schlüssel liegen dort bereits.
2. *In der Cloud-Session.* Dann müssen in der Domain-Allowlist der Umgebung
   `generativelanguage.googleapis.com`, `api.cloudflare.com` und
   `www.etim-international.com` freigeschaltet sein, `GEMINI_API_KEY` sowie die
   Jev-Zugangsdaten als Umgebungsvariablen gesetzt und die vier Testdateien nach
   `data/testsets/` hochgeladen sein.

---

## Ablauf, wenn die Voraussetzungen da sind

```bash
make setup
make env                                   # Schlüssel eintragen (geführt)
make load-model ETIM=9.0                   # CSV -> SQLite + Embeddings
python -m etim ingest data/testsets/LTS_Testset_30_ohne_ETIM.xlsx --job lts30
python -m etim classify --job lts30 --model gemini --etim 9.0
python -m etim features --job lts30
make testset JOB=lts30 SOL=data/testsets/LTS_Testset_30_LOESUNG.xlsx
```

Dasselbe mit `--model jev`, danach `--model both` für den Vergleich im Cockpit.
Der Holdout (250er-Datei) wird **höchstens zweimal** angefasst: einmal zur
Abnahme, einmal nach der letzten Korrektur. Öfter gemessen ist er kein Holdout mehr.

**Prüfen, dass Jev wirklich lief** (nicht simuliert):

```bash
python -c "import json;d=json.load(open('out/lts30/classified.json'));\
print({x['model'] for x in d}, {x['simulated'] for x in d})"
# erwartet: {'jev'} {False}
```

---

## Iterationen

| # | Datum | Änderung | Warum | Ergebnis auf den 30 |
|---|---|---|---|---|
| 0 | 21.9.2026 | Patch eingespielt: Spaltenzuordnung, Jev-Zubehörwiderspruch, `eval_testset.py` | Ausgangslage | nicht gemessen (Blocker) |
| 1 | 21.9.2026 | Varianten gruppieren, erfundene EC-Codes abfangen | Befund aus dem strawa-Trockenlauf: baugleiche Artikel bekamen verschiedene Klassen; das Modell nannte EC-Codes, die es nicht gibt | nicht gemessen (Blocker) |
| 2 | 21.9.2026 | Wertelisten erreichbar machen, Einheiten nachrechnen | Werte ab dem 61. Eintrag waren für das Modell unsichtbar; Einheitenumrechnung war nur erbeten, nie geprüft | nicht gemessen (Blocker) |
| 3 | 21.9.2026 | Export gegen unbekannte Codes sichern | Freigaben aus dem Cockpit gehen an allen Prüfungen vorbei ins Kunden-XML | nicht gemessen (Blocker) |
| 4 | 21.9.2026 | Eine `base_name`-Definition; `--model both` gruppiert Varianten | Es gab zwei Fassungen mit verschiedenen Trennern — der Vergleich hätte etwas anderes gemessen, als die Pipeline tut | nicht gemessen (Blocker) |

Jede dieser Änderungen ist durch Tests abgesichert (`make test`, 97 Tests, läuft
ohne API-Schlüssel). Keine davon kennt die Lösungsdateien: es gibt keine
Zuordnung von Serien oder Artikelnummern zu Klassen, kein Few-Shot mit
Testartikeln und keine LTS-Sonderregel. Alles würde für einen SHK-Hersteller mit
deutscher Preisliste genauso gelten.

---

## Abnahme: Gemini gegen Jev auf dem Holdout (220 Artikel)

*Noch nicht gemessen.* Die Tabelle wird gefüllt, sobald ein Lauf möglich ist.

| | Gemini | Jev |
|---|---|---|
| Klassen-Trefferquote (Ziel ≥ 95 %) | — | — |
| Fehler in der Prüfliste (Ziel ≥ 80 %) | — | — |
| Zubehör/Hauptprodukt verwechselt und ungeprüft (Ziel 0) | — | — |
| Merkmalspräzision (Ziel ≥ 95 %) | — | — |
| falsch gefüllt (Ziel ≤ 3 %) | — | — |
| Merkmalsabdeckung (berichtet, kein Schwellenwert) | — | — |
| Kosten je Artikel | — | — |
| Zeit je Artikel | — | — |

**Empfehlung:** offen bis zur Messung.

---

## Was die Messung voraussichtlich zeigen wird

Kein Ersatz für Zahlen, aber die Erwartung soll vorher festliegen — sonst erklärt
man sich hinterher jedes Ergebnis passend.

- **Der Engpass wird das Retrieval sein, nicht die Modellwahl.** Im strawa-Lauf
  war die Soll-Klasse bei 14 von 17 Artikeln gar nicht im Kandidatenfeld; wo sie
  drin war, hat das Modell sie gefunden. Jev sieht mit `JEV_TOP_K=254` ein viel
  breiteres Feld als Gemini mit 20 — wenn Jev besser abschneidet, ist das
  zuerst ein Retrieval-Ergebnis und erst dann ein Modellvergleich. Die faire
  Frage lautet deshalb: *Wie oft liegt die Soll-Klasse überhaupt im Feld?*
  `eval_testset.py` weist das je Artikel als `rang_soll_in_kandidaten` aus.
- **Konfidenz wird als Signal wieder schwach sein** (0.90–0.95 auch bei Unsinn,
  siehe CLAUDE.md). Die Zahl, die zählt, ist „Fehler von der Prüfung
  aufgefangen" — sie hängt an `_review_flag` und am Zubehör-Widerspruch, nicht
  an der Konfidenz.
- **Die Merkmalsabdeckung wird niedrig aussehen, und das ist teilweise richtig.**
  Die Lösungsdatei enthält Werte, die in keinem Katalogtext stehen. „Lieber leer
  als falsch" bleibt die Regel; entscheidend ist die Präzision, nicht die Abdeckung.
