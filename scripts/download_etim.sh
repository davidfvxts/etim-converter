#!/usr/bin/env bash
# Lädt die frei verfügbaren ETIM-Dateien (Stand 3.9.2026, Quelle: etim-international.com/downloads).
# Wenn ein Link 404 liefert: Download-Seite öffnen, Filter "Model releases", Datei manuell nach data/downloads/ legen.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/downloads data/etim data/schema data/xchange

BASE="https://www.etim-international.com/wp-content/uploads"
declare -A FILES=(
  ["etim10-csv.zip"]="$BASE/2024/12/ETIM-10.0-ALL-SECTORS-CSV-METRIC-EI-2024-12-05.zip"
  ["etim10-ixf.zip"]="$BASE/2024/12/ETIM-10.0-ALL-SECTORS-IXF-WITH-CHANGE-CODES-METRIC-EI-2024-12-05.zip"
  ["ixf-format.zip"]="$BASE/2024/08/ETIMIXF-3.1-format-V-2024-08.zip"
  ["bmecat-guideline.zip"]="$BASE/2024/12/ETIM-BMEcat-Guideline-V5-0-2-2024-12-12.zip"
  ["xchange-2.0.zip"]="$BASE/2026/04/ETIM-xChange_V2.0-2026-04-30.zip"
)

for name in "${!FILES[@]}"; do
  url="${FILES[$name]}"
  if [ -f "data/downloads/$name" ]; then echo "✓ $name vorhanden"; continue; fi
  echo "↓ $name"
  if ! curl -fsSL -o "data/downloads/$name" "$url"; then
    echo "  !! Download fehlgeschlagen: $url"
    echo "     → https://www.etim-international.com/downloads/ öffnen und Datei manuell nach data/downloads/$name legen"
    rm -f "data/downloads/$name"
  fi
done

[ -f data/downloads/etim10-csv.zip ] && unzip -oq data/downloads/etim10-csv.zip -d data/etim && echo "→ data/etim/ entpackt"
[ -f data/downloads/bmecat-guideline.zip ] && unzip -oq data/downloads/bmecat-guideline.zip -d data/schema && echo "→ data/schema/ entpackt (XSD suchen: find data/schema -name '*.xsd')"
[ -f data/downloads/xchange-2.0.zip ] && unzip -oq data/downloads/xchange-2.0.zip -d data/xchange && echo "→ data/xchange/ entpackt"

echo
echo "Jetzt: python -m etim inspect data/etim"
