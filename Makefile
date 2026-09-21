.PHONY: setup check test lint eval jev load-model studio compare reference

# Alles laeuft ueber das venv. Auf macOS gibt es kein "python", nur "python3" —
# der direkte Pfad ins venv erspart sowohl das Aktivieren als auch den Unterschied.
PY := .venv/bin/python
JOB ?= demo

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

jev:                        ## Cloudflare-Worker deployen und .env einrichten
	bash scripts/setup_jev.sh

load-model: check           ## ETIM-CSV nach SQLite + Klassen-Embeddings
	$(PY) -m etim load-model data/etim

studio: check               ## Cockpit im Browser: Katalog einspielen, Lauf starten
	$(PY) -m etim studio

compare: check              ## Gemini gegen Jev — make compare JOB=strawa
	$(PY) -m etim compare --job $(JOB)

reference: check            ## Geruest fuer reference.json — make reference JOB=strawa
	$(PY) -m etim reference out/$(JOB)
