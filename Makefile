.PHONY: setup check env env-show test lint eval jev jev-check versions load-model studio compare reference

# Alles laeuft ueber das venv. Auf macOS gibt es kein "python", nur "python3" —
# der direkte Pfad ins venv erspart sowohl das Aktivieren als auch den Unterschied.
PY := .venv/bin/python
JOB ?= demo
ETIM ?=

check:
	@test -x $(PY) || { \
	  echo ""; \
	  echo "  Es gibt noch keine Arbeitsumgebung in diesem Ordner."; \
	  echo "  Einmalig ausfuehren:  make setup"; \
	  echo ""; exit 1; }

setup:
	python3 -m venv .venv
	./.venv/bin/pip install -U pip
	./.venv/bin/pip install -r requirements.txt
	@echo ""
	@echo "  Fertig. Weiter mit:  make jev"
	@echo ""

test: check
	ETIM_DRY_RUN=1 $(PY) -m pytest -q

lint: check
	$(PY) -m pyflakes etim tests || true

eval: check
	$(PY) scripts/eval_retrieval.py --verbose

# --- Der Weg zum Modellvergleich, in der Reihenfolge -------------------------

env:                        ## Zugangsdaten gefuehrt eintragen (verdeckte Eingabe)
	bash scripts/setup_env.sh

env-show:                   ## Zeigen, was in der .env steht (Secrets maskiert)
	bash scripts/show_env.sh

jev:                        ## Cloudflare-Worker deployen und .env einrichten
	bash scripts/setup_jev.sh

jev-check: check            ## Jev-Zugang mit einem echten Mini-Aufruf pruefen
	$(PY) -m etim jev-check

versions: check             ## Welche ETIM-Versionen liegen vor?
	$(PY) -m etim versions

load-model: check           ## ETIM-CSV nach SQLite + Embeddings — make load-model ETIM=9.0
	$(PY) -m etim load-model $(if $(ETIM),--etim $(ETIM))

studio: check               ## Cockpit im Browser: Katalog einspielen, Lauf starten
	$(PY) -m etim studio

compare: check              ## Gemini gegen Jev — make compare JOB=strawa [ETIM=9.0]
	$(PY) -m etim compare --job $(JOB) $(if $(ETIM),--etim $(ETIM))

reference: check            ## Geruest fuer reference.json — make reference JOB=strawa
	$(PY) -m etim reference out/$(JOB)
