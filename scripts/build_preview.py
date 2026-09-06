#!/usr/bin/env python3
"""Baut aus web/ eine einzelne HTML-Datei — zum Teilen ohne Server.

Alle Stile und Skripte werden inline gesetzt, die ES-Module-Grenze zwischen
components.js und app.js faellt dabei weg. Die Datei laedt keinen Job und
zeigt deshalb die Beispieldaten.

    python scripts/build_preview.py            -> web/preview.html
    python scripts/build_preview.py --body     -> nur der Seiteninhalt (ohne
                                                  <!doctype>/<html>/<head>)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CSS = ["tokens.css", "base.css", "components.css", "app.css"]


def build(body_only: bool = False) -> str:
    css = "\n".join((WEB / "assets" / f).read_text(encoding="utf-8") for f in CSS)

    components = (WEB / "assets" / "components.js").read_text(encoding="utf-8")
    components = re.sub(r"^export ", "", components, flags=re.M)

    app = (WEB / "assets" / "app.js").read_text(encoding="utf-8")
    # Den Import auf components.js entfernen — die Funktionen stehen nach dem
    # Zusammenfuegen im selben Gueltigkeitsbereich.
    app = re.sub(r"^import \{[^}]*\} from '\./components\.js';\s*$", "", app, flags=re.M | re.S)

    demo = (WEB / "assets" / "demo-data.js").read_text(encoding="utf-8")

    page = f"""<title>ETIM-Prüf-Cockpit</title>
<style>
{css}
</style>
<div class="app" id="app"></div>
<script>
{demo}
</script>
<script>
{components}

{app}
</script>
"""
    if body_only:
        return page
    return ('<!doctype html>\n<html lang="de">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'</head>\n<body>\n{page}</body>\n</html>\n')


if __name__ == "__main__":
    body_only = "--body" in sys.argv
    out = WEB / ("preview.body.html" if body_only else "preview.html")
    out.write_text(build(body_only), encoding="utf-8")
    print(f"{out.relative_to(ROOT)}: {out.stat().st_size / 1024:.0f} KB")
