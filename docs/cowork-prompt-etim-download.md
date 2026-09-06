# Cowork-Prompt: echte ETIM-Daten beschaffen

Für eine Claude-Cowork-Session auf einem Rechner **ohne** Egress-Allowlist
(lokaler Mac/PC). Kompletten Block ab hier kopieren.

---

Im Repo `etim-converter` fehlen die echten ETIM-Stammdaten. In der Remote-Umgebung
war `www.etim-international.com` durch die Netzwerk-Policy gesperrt (403 auf CONNECT),
deshalb machen wir den Download hier lokal. Arbeite auf Branch
`claude/etim-csv-katalog-test-k9vksy`.

**Schritt 1 — Herunterladen.** Führe `bash scripts/download_etim.sh` aus. Das Skript holt
fünf ZIPs nach `data/downloads/` und entpackt sie nach `data/etim/`, `data/schema/` und
`data/xchange/`. Wichtig sind nur zwei: `etim10-csv.zip` (die Klassifikation) und
`bmecat-guideline.zip` (enthält die BMEcat-XSD). Wenn ein Link 404 liefert, sind die
URLs im Skript veraltet — öffne dann https://www.etim-international.com/downloads/,
Filter "Model releases", suche die aktuellen Dateien
(ETIM 10.0 ALL SECTORS CSV METRIC und ETIM BMEcat Guideline V5.0.2), lade sie manuell
nach `data/downloads/` unter genau diesen Dateinamen und starte das Skript erneut —
vorhandene ZIPs entpackt es ohne erneuten Download. Melde mir, falls die URLs sich
geändert haben, und aktualisiere sie im Skript.

**Schritt 2 — Spaltennamen prüfen.** `python -m etim inspect data/etim`. Die Aliase in
`etim/model.py` (`TABLE_ALIASES`, `COLUMN_ALIASES`) sind bislang nur gegen eine
handgebaute 6-Klassen-Fixture getestet, nie gegen den echten Release. Vergleiche die
tatsächlichen Datei- und Spaltennamen mit den Erwartungen im Docstring oben in
`model.py`, ergänze fehlende Aliase und sag mir **explizit, welche abgewichen sind** —
das ist der eigentliche Erkenntniswert dieses Schritts.

**Schritt 3 — Modell bauen.** `python -m etim load-model data/etim`. Erwartung: ~5.500
Klassen statt 6. Achtung: das erzeugt rund 5.500 Embedding-Calls. Wenn das am
Free-Tier-Kontingent des Google-Keys scheitert, brich ab und sag es mir — weiche nicht
auf ein schwächeres Modell aus. Mit `--no-embed` lässt sich vorab nur die SQLite bauen,
um die Spalten zu verifizieren, ohne Kontingent zu verbrennen; mach das zuerst.

**Schritt 4 — committen.** `data/` ist gitignored, die Daten bleiben also lokal. Committe
nur Code-Änderungen (Aliase in `model.py`, ggf. korrigierte URLs im Skript) und
aktualisiere den Stand-Block in `CLAUDE.md`. Der API-Key gehört ausschließlich in `.env`
und in keinen Commit.

Wenn Schritt 1 auch lokal scheitert, hör auf und sag mir das Ergebnis — arbeite nicht
ersatzweise mit der Mini-Fixture weiter, deren Aussagekraft ist null.
