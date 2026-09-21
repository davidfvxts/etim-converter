#!/usr/bin/env bash
# Doppelklick im Finder startet das Prüf-Cockpit und öffnet den Browser.
# Zum Beenden dieses Fenster schließen.
cd "$(dirname "$0")"

printf '\033]0;ETIM-Cockpit\007'
echo "ETIM-Pipeline · Prüf-Cockpit"
echo "$(pwd)"
echo

if [ ! -x .venv/bin/python ]; then
  echo "Die Arbeitsumgebung fehlt — wird einmalig eingerichtet (ein bis zwei Minuten) …"
  echo
  if ! make setup; then
    echo
    echo "Das hat nicht geklappt. Bitte die Meldung oben weitergeben."
    echo "Fenster kann geschlossen werden."
    read -r _
    exit 1
  fi
fi

PORT="${ETIM_PORT:-8000}"
# Läuft schon eins? Dann nur den Browser öffnen statt einen zweiten zu starten.
if curl -s -m 2 "http://127.0.0.1:$PORT/api/jobs" >/dev/null 2>&1; then
  echo "Das Cockpit läuft bereits — Browser wird geöffnet."
  open "http://127.0.0.1:$PORT/" 2>/dev/null || true
  echo
  echo "Fenster kann geschlossen werden."
  read -r _
  exit 0
fi

echo "Cockpit startet … der Browser geht gleich auf."
echo "Zum Beenden: dieses Fenster schließen oder Strg+C."
echo
exec .venv/bin/python -m etim studio --port "$PORT"
