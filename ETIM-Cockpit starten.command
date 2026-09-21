#!/usr/bin/env bash
# Doppelklick im Finder startet das Prüf-Cockpit und öffnet den Browser.
# Zum Beenden dieses Fenster schließen.
#
# Die Datei darf auch woanders liegen (Schreibtisch, Dock): dann sucht sie das
# Projekt an den üblichen Stellen, statt aufzugeben.

printf '\033]0;ETIM-Cockpit\007'
echo "ETIM-Pipeline · Prüf-Cockpit"
echo

# Ein gültiger Projektordner hat beides.
ist_projekt() { [ -f "$1/Makefile" ] && [ -f "$1/etim/cli.py" ]; }

PROJEKT="$(cd "$(dirname "$0")" && pwd)"
if ! ist_projekt "$PROJEKT"; then
  echo "Der Starter liegt nicht im Projektordner ($PROJEKT) — Projekt wird gesucht …"
  GEFUNDEN=""
  for k in "$HOME/Projekte/etim-converter" "$HOME/Projects/etim-converter" \
           "$HOME/etim-converter" "$HOME/Documents/etim-converter" \
           "$HOME/Developer/etim-converter" "$HOME/Desktop/etim-converter"; do
    if ist_projekt "$k"; then GEFUNDEN="$k"; break; fi
  done
  if [ -z "$GEFUNDEN" ]; then
    # Bewusst flach: eine Tiefensuche über das ganze Benutzerverzeichnis dauert zu lange.
    while IFS= read -r k; do
      if ist_projekt "$k"; then GEFUNDEN="$k"; break; fi
    done < <(find "$HOME" -maxdepth 4 -type d -name "etim-converter" 2>/dev/null)
  fi
  if [ -z "$GEFUNDEN" ]; then
    echo
    echo "  Kein Projektordner gefunden."
    echo "  Diese Datei gehört IN den Projektordner (dort, wo auch 'Makefile' liegt)."
    echo "  Für einen Schnellzugriff nicht kopieren, sondern:"
    echo "    · die Datei aus dem Projektordner ins Dock ziehen, oder"
    echo "    · Rechtsklick → 'Alias erzeugen' und nur den Alias verschieben."
    echo
    echo "  Fenster kann geschlossen werden."
    read -r _
    exit 1
  fi
  echo "Gefunden: $GEFUNDEN"
  echo "(Tipp: Diese Kopie löschen und stattdessen einen Alias oder Dock-Eintrag anlegen.)"
  PROJEKT="$GEFUNDEN"
fi

cd "$PROJEKT"
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
