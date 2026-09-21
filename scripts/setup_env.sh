#!/usr/bin/env bash
# Zugangsdaten eintragen — gefuehrt, ohne die .env von Hand zu oeffnen.
#
#   make env
#
# Eingaben mit Passwortcharakter werden verdeckt eingelesen und erscheinen weder
# auf dem Bildschirm noch in der Shell-Historie. Enter behaelt den vorhandenen Wert.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
# shellcheck source=lib_env.sh
. "$ROOT/scripts/lib_env.sh"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33m%s\033[0m\n' "$*"; }

[ -f "$ROOT/.env.example" ] || { echo "Falscher Ordner — bitte 'make env' im Projekt ausfuehren." >&2; exit 1; }
env_ensure
cp "$ENV_FILE" "$ENV_FILE.bak"

say "Google Gemini"
info "Schluessel aus dem Google AI Studio. Wird fuer das Einlesen der Kataloge gebraucht."
env_ask GEMINI_API_KEY "GEMINI_API_KEY" secret || warn "Ohne Schluessel laufen nur Trockenlaeufe."

say "Jev — welcher Weg?"
info "1) direkt ueber Cloudflare Workers AI   (Account ID + API-Token, kein Node noetig)"
info "2) ueber den eigenen Worker             (braucht 'make jev')"
info "3) jetzt nicht einrichten"
printf '  Auswahl [1]: '
read -r choice
choice="${choice:-1}"

case "$choice" in
  1)
    say "Cloudflare Workers AI"
    info "Beides steht im Dashboard unter Workers AI -> Use REST API."
    env_ask CLOUDFLARE_ACCOUNT_ID "Account ID" || { echo "Account ID fehlt." >&2; exit 1; }
    acc="$(env_get CLOUDFLARE_ACCOUNT_ID)"
    # Eine Account ID ist 32 Hexzeichen. Passt sie nicht, wurde meist der Token
    # in dieses Feld eingefuegt — lieber jetzt sagen als beim ersten Lauf.
    if ! printf '%s' "$acc" | grep -qE '^[0-9a-f]{32}$'; then
      warn "Das sieht nicht nach einer Account ID aus (erwartet: 32 Zeichen, nur 0-9 und a-f)."
      warn "Verwechselt mit dem API-Token? Der ist laenger und enthaelt Gross- und Kleinbuchstaben."
    fi
    env_ask CLOUDFLARE_API_TOKEN "API-Token (wird nicht angezeigt)" secret \
      || { echo "Token fehlt." >&2; exit 1; }
    tok="$(env_get CLOUDFLARE_API_TOKEN)"
    [ "${#tok}" -ge 20 ] || warn "Der Token wirkt kurz (${#tok} Zeichen) — vollstaendig eingefuegt?"
    [ "$tok" != "$acc" ] || { echo "Token und Account ID sind identisch — da ist etwas verrutscht." >&2; exit 1; }
    env_set ETIM_JEV_TRANSPORT cloudflare
    info "Transport auf 'cloudflare' gestellt."
    ;;
  2)
    env_set ETIM_JEV_TRANSPORT worker
    say "Worker"
    info "Transport auf 'worker' gestellt. Adresse und Secret setzt 'make jev' selbst."
    ;;
  *)
    info "Jev bleibt unveraendert."
    ;;
esac

say "Klassifizierungsmodell"
info "Womit soll die ETIM-Klasse gewaehlt werden? Im Cockpit je Lauf umschaltbar."
info "1) gemini   2) jev   3) both (beide, mit Vergleich)"
current_clf="$(env_get ETIM_CLASSIFIER)"
printf '  Auswahl [%s]: ' "${current_clf:-gemini}"
read -r clf
case "${clf:-}" in
  1|gemini) env_set ETIM_CLASSIFIER gemini ;;
  2|jev)    env_set ETIM_CLASSIFIER jev ;;
  3|both)   env_set ETIM_CLASSIFIER both ;;
  "")       env_set ETIM_CLASSIFIER "${current_clf:-gemini}" ;;
  *)        warn "Unbekannt — bleibt bei ${current_clf:-gemini}." ;;
esac

say "Eingetragen in .env"
for k in GEMINI_API_KEY ETIM_JEV_TRANSPORT CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN \
         ETIM_JEV_WORKER_URL ETIM_CLASSIFIER; do
  v="$(env_get "$k")"
  case "$k" in
    *KEY|*TOKEN|*SECRET) printf '  %-24s %s\n' "$k" "$(env_mask "$v")" ;;
    *)                   printf '  %-24s %s\n' "$k" "${v:-(leer)}" ;;
  esac
done
info "(Sicherung der vorherigen Fassung: .env.bak)"

printf '\n'
if [ "$(env_get ETIM_JEV_TRANSPORT)" = "cloudflare" ] && [ -n "$(env_get CLOUDFLARE_API_TOKEN)" ]; then
  say "Probe: antwortet Jev?"
  "$ROOT/.venv/bin/python" -m etim jev-check 2>/dev/null \
    || { warn "Die Probe schlug fehl. Falls '.venv' fehlt: erst 'make setup'."; exit 1; }
else
  info "Weiter mit:  make jev-check"
fi
