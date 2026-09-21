#!/usr/bin/env bash
# Richtet den Jev-Zugang in einem Durchgang ein: Worker deployen, Secret setzen,
# .env schreiben, Funktion pruefen.
#
#   bash scripts/setup_jev.sh
#
# Das Skript ist wiederholbar: ein bereits in der .env stehendes Secret wird
# wiederverwendet, statt ein neues zu erzeugen. Das Secret wird nie ausgegeben.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
WORKER_DIR="$ROOT/worker"
WORKER_NAME="$(grep -E '^name\s*=' "$WORKER_DIR/wrangler.toml" | head -1 | cut -d'"' -f2)"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die()  { printf '\n\033[31mAbbruch:\033[0m %s\n' "$*" >&2; exit 1; }

# --- Voraussetzungen ---------------------------------------------------------
say "1/5  Voraussetzungen"
command -v node >/dev/null || die "node ist nicht installiert (https://nodejs.org)."
command -v npx  >/dev/null || die "npx fehlt — gehoert zu Node.js."
command -v curl >/dev/null || die "curl ist nicht installiert."
info "node $(node --version)"

cd "$WORKER_DIR"
if [ ! -d node_modules ]; then
  info "wrangler wird installiert (einmalig, dauert ~1 min) …"
  npm install --silent
fi

# --- Cloudflare-Anmeldung ----------------------------------------------------
say "2/5  Cloudflare-Anmeldung"
if ! npx --no-install wrangler whoami >/dev/null 2>&1; then
  info "Noch nicht angemeldet — es oeffnet sich gleich ein Browserfenster."
  npx --no-install wrangler login
fi
ACCOUNT="$(npx --no-install wrangler whoami 2>/dev/null | grep -iE '@|account' | head -2 | tr '\n' ' ' || true)"
info "angemeldet: ${ACCOUNT:-ok}"

# --- Secret ------------------------------------------------------------------
say "3/5  Shared Secret"
SECRET=""
if [ -f "$ENV_FILE" ]; then
  SECRET="$(grep -E '^ETIM_JEV_WORKER_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
fi
if [ -n "$SECRET" ]; then
  info "Bestehendes Secret aus .env wiederverwendet."
else
  command -v openssl >/dev/null || die "openssl fehlt — Secret laesst sich nicht erzeugen."
  SECRET="$(openssl rand -hex 32)"
  info "Neues Secret erzeugt (wird nicht angezeigt)."
fi
printf '%s' "$SECRET" | npx --no-install wrangler secret put ETIM_PROXY_SECRET >/dev/null
info "Secret beim Worker '$WORKER_NAME' hinterlegt."

# --- Deploy ------------------------------------------------------------------
say "4/5  Worker veroeffentlichen"
DEPLOY_LOG="$(mktemp)"
trap 'rm -f "$DEPLOY_LOG"' EXIT
npx --no-install wrangler deploy | tee "$DEPLOY_LOG"
URL="$(grep -oE 'https://[a-z0-9.-]+\.workers\.dev' "$DEPLOY_LOG" | head -1 || true)"
[ -n "$URL" ] || die "Die Worker-Adresse liess sich nicht aus der Ausgabe lesen.
  Trag sie von Hand in die .env ein als ETIM_JEV_WORKER_URL=<adresse>/jev"
info "Adresse: $URL"

# --- .env schreiben ----------------------------------------------------------
say "5/5  .env eintragen und pruefen"
cd "$ROOT"
[ -f "$ENV_FILE" ] || cp .env.example "$ENV_FILE"
cp "$ENV_FILE" "$ENV_FILE.bak"

set_key() {  # set_key SCHLUESSEL WERT — ersetzt die Zeile oder haengt sie an
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # Wert ueber die Umgebung uebergeben: nichts landet in der Prozessliste.
    KEY="$key" VALUE="$value" awk '
      BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"] }
      $0 ~ "^" k "=" { print k "=" v; next } { print }
    ' "$ENV_FILE" > "$tmp"
  else
    cat "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

set_key ETIM_JEV_TRANSPORT worker
set_key ETIM_JEV_WORKER_URL "$URL/jev"
set_key ETIM_JEV_WORKER_SECRET "$SECRET"
info ".env aktualisiert (Sicherung: .env.bak)"

CODE="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $SECRET" "$URL/health" || echo 000)"
case "$CODE" in
  200) printf '\n\033[32mFertig.\033[0m Der Worker antwortet. Weiter mit:\n\n  python -m etim load-model data/etim\n  python -m etim studio\n\n' ;;
  401) die "Der Worker lehnt das Secret ab (401). Skript noch einmal laufen lassen." ;;
  000) die "Der Worker ist nicht erreichbar. Adresse pruefen: $URL" ;;
  *)   die "Der Worker antwortet mit HTTP $CODE statt 200." ;;
esac
