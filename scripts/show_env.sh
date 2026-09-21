#!/usr/bin/env bash
# Zeigt, was in der .env steht — Secrets nur maskiert.
#
#   make env-show
#
# Beantwortet die Frage "wo ist das eigentlich eingetragen?", ohne dass ein
# Schluessel auf dem Bildschirm landet oder in der Shell-Historie auftaucht.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
# shellcheck source=lib_env.sh
. "$ROOT/scripts/lib_env.sh"

if [ ! -f "$ENV_FILE" ]; then
  printf '\n  Es gibt noch keine .env in %s\n  Anlegen mit:  make env\n\n' "$ROOT"
  exit 1
fi

printf '\n\033[1m%s\033[0m\n' "$ENV_FILE"
# GNU zuerst, dann BSD/macOS. Umgekehrt waere falsch: unter Linux ist "stat -f"
# der Dateisystem-Status und liefert erfolgreich voellig anderen Text.
perms="$(stat -c %a "$ENV_FILE" 2>/dev/null || stat -f %Lp "$ENV_FILE" 2>/dev/null || echo '?')"
printf '  Rechte: %s  (600 = nur du kannst sie lesen)\n\n' "$perms"

show() {  # show SCHLUESSEL "Erklaerung"
  local v; v="$(env_get "$1")"
  case "$1" in
    *KEY|*TOKEN|*SECRET) v="$(env_mask "$v")" ;;
    *) [ -n "$v" ] || v="(leer)" ;;
  esac
  printf '  %-26s %-46s %s\n' "$1" "$v" "$2"
}

transport="$(env_get ETIM_JEV_TRANSPORT)"
printf '\033[1m  Jev-Weg: %s\033[0m\n' "${transport:-(nicht gesetzt)}"
case "$transport" in
  worker)
    printf '  Die Aufrufe laufen ueber deinen Cloudflare-Worker.\n'
    printf '  Die Zugangsdaten zu Workers AI liegen dort als Secret, nicht hier.\n\n'
    show ETIM_JEV_WORKER_URL    "Adresse des Workers"
    show ETIM_JEV_WORKER_SECRET "von 'make jev' erzeugt, auch beim Worker hinterlegt"
    printf '  %-26s %-46s %s\n' "CLOUDFLARE_API_TOKEN" "wird auf diesem Weg nicht gebraucht" ""
    ;;
  cloudflare)
    printf '  Die Aufrufe gehen direkt an Workers AI, ohne eigenen Worker.\n\n'
    show CLOUDFLARE_ACCOUNT_ID "Account ID aus dem Dashboard"
    show CLOUDFLARE_API_TOKEN  "API-Token aus dem Dashboard"
    ;;
  *)
    printf '  Noch nicht festgelegt. Einrichten mit:  make jev   oder   make env\n'
    ;;
esac

printf '\n\033[1m  Uebriges\033[0m\n'
show GEMINI_API_KEY  "liest die Kataloge, fuellt Merkmale"
show ETIM_CLASSIFIER "Vorauswahl des Modells (im Cockpit je Lauf umschaltbar)"
show ETIM_DRY_RUN    "1 = keine echten Aufrufe"

printf '\n  Ob Jev auch antwortet, zeigt:  make jev-check\n\n'
