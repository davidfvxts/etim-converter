# Gemeinsame .env-Helfer fuer die Einrichtungsskripte.
# Wird mit `source` eingebunden, nicht direkt ausgefuehrt.
#
# Werte werden ueber die Umgebung an awk gereicht, nie ueber die Kommandozeile:
# so taucht kein Secret in der Prozessliste auf. Und nie ueber sed — ein Token
# mit & oder / wuerde dort als Muster gelesen und zerlegt.

env_file() { printf '%s' "${ENV_FILE:?ENV_FILE nicht gesetzt}"; }

env_ensure() {  # .env anlegen, falls sie fehlt
  local f; f="$(env_file)"
  [ -f "$f" ] || cp "$(dirname "$f")/.env.example" "$f"
  chmod 600 "$f"
}

env_get() {  # env_get SCHLUESSEL -> Wert auf stdout (leer, wenn nicht gesetzt)
  local f; f="$(env_file)"
  [ -f "$f" ] || return 0
  grep -E "^$1=" "$f" | head -1 | cut -d= -f2- || true
}

env_set() {  # env_set SCHLUESSEL WERT
  local key="$1" value="$2" f tmp
  f="$(env_file)"; tmp="$(mktemp)"
  if grep -qE "^${key}=" "$f"; then
    KEY="$key" VALUE="$value" awk '
      BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"] }
      $0 ~ "^" k "=" { print k "=" v; next } { print }
    ' "$f" > "$tmp"
  else
    cat "$f" > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  mv "$tmp" "$f"
  chmod 600 "$f"
}

env_mask() {  # Nur die letzten vier Zeichen zeigen
  local v="$1"
  [ -n "$v" ] || { printf '(leer)'; return; }
  if [ "${#v}" -le 4 ]; then printf '****'; else printf '…%s' "${v: -4}"; fi
}

# env_ask SCHLUESSEL "Beschriftung" [secret] — fragt, behaelt den alten Wert bei Enter
env_ask() {
  local key="$1" label="$2" secret="${3:-}" current input
  current="$(env_get "$key")"
  if [ -n "$current" ]; then
    printf '  %s [aktuell: %s] — Enter behaelt: ' "$label" "$(env_mask "$current")"
  else
    printf '  %s: ' "$label"
  fi
  if [ -n "$secret" ]; then
    read -rs input; printf '\n'
  else
    read -r input
  fi
  # Leerzeichen und Anfuehrungszeichen abschneiden: beim Einfuegen rutscht oft was mit
  input="$(printf '%s' "$input" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
  if [ -z "$input" ]; then
    [ -n "$current" ] || return 1
    return 0
  fi
  env_set "$key" "$input"
}
