"""Einheiten aus dem Katalogzitat gegen die ETIM-Einheit pruefen.

Der Prompt bittet das Modell, Katalogeinheiten in die ETIM-Einheit umzurechnen.
Bitten ist keine Pruefung: eine Zollangabe, die unveraendert in ein mm-Merkmal
laeuft, sieht in enriched.json aus wie ein sauberer Wert und faellt erst beim
Grosshaendler auf. Hier wird deshalb nachgerechnet — deterministisch, ohne Modell.

Geprueft wird nur, was eindeutig ist:
  * Massstabswechsel innerhalb des metrischen Systems (m -> mm, kg -> g, kW -> W …).
    Steht "2,5 m" im Zitat und die ETIM-Einheit ist mm, muss 2500 herauskommen.
  * Imperiale Angaben. Ein unveraendert uebernommener Zollwert wird verworfen —
    das ist die Regel "imperiale Werte bleiben draussen".

Nicht geprueft wird, was mehrdeutig ist: Gewinde- und Nennweitenangaben ("1/2 Zoll",
"DN 20") sind Baugroessen, keine Laengen. 1/2 Zoll Gewinde ist nicht 12,7 mm. Darum
fuehrt eine imperiale Angabe, die weder unveraendert noch sauber umgerechnet ist,
nur in die Pruefung und nicht in den Papierkorb.
"""
from __future__ import annotations

import re

# Kanonische Basis je Groesse, damit nur innerhalb derselben Groesse verglichen wird.
# Wert: (Groesse, Faktor zur Basis). Temperatur laeuft gesondert (Offset).
_SCALE: dict[str, tuple[str, float]] = {
    # Laenge, Basis mm
    "mm": ("laenge", 1.0), "cm": ("laenge", 10.0), "dm": ("laenge", 100.0),
    "m": ("laenge", 1000.0), "km": ("laenge", 1_000_000.0), "µm": ("laenge", 0.001),
    # Masse, Basis g
    "mg": ("masse", 0.001), "g": ("masse", 1.0), "kg": ("masse", 1000.0), "t": ("masse", 1_000_000.0),
    # Leistung, Basis W
    "mw": ("leistung", 0.001), "w": ("leistung", 1.0), "kw": ("leistung", 1000.0),
    # Volumen, Basis l
    "ml": ("volumen", 0.001), "l": ("volumen", 1.0), "m³": ("volumen", 1000.0), "m3": ("volumen", 1000.0),
    # Druck, Basis bar
    "mbar": ("druck", 0.001), "bar": ("druck", 1.0), "kpa": ("druck", 0.01), "pa": ("druck", 0.00001),
    # Elektrisch (nur Praefixe, kein Systemwechsel)
    "ma": ("strom", 0.001), "a": ("strom", 1.0),
    "mv": ("spannung", 0.001), "v": ("spannung", 1.0), "kv": ("spannung", 1000.0),
}

# Imperiale Einheiten -> (Groesse, Faktor zur metrischen Basis oben).
_IMPERIAL: dict[str, tuple[str, float]] = {
    "inch": ("laenge", 25.4), "in": ("laenge", 25.4), "zoll": ("laenge", 25.4), '"': ("laenge", 25.4),
    "ft": ("laenge", 304.8), "foot": ("laenge", 304.8), "feet": ("laenge", 304.8),
    "lb": ("masse", 453.59237), "lbs": ("masse", 453.59237), "oz": ("masse", 28.349523125),
    "psi": ("druck", 0.0689475729),
    "gal": ("volumen", 3.785411784), "gallon": ("volumen", 3.785411784),
    "hp": ("leistung", 745.699872),
}

# Reihenfolge zaehlt: laengere Schreibweisen zuerst, sonst matcht "in" in "inch".
# Das Zollzeichen steht als eigene Alternative, weil re.escape es sonst mitten in
# die Wortliste setzt, wo das abschliessende (?!\w) nicht greift.
_UNIT_ALTERNATIVES = sorted((set(_SCALE) | set(_IMPERIAL)) - {'"'}, key=len, reverse=True)
_UNIT_RE = "|".join(re.escape(u) for u in _UNIT_ALTERNATIVES)
# Zahl: Bruch ("1/2"), gemischt ("1 1/2") oder dezimal ("2,5"). Kataloge schreiben
# Zollmasse als Bruch — wer "1/2 Zoll" als "2 Zoll" liest, rechnet danach Unsinn.
_NUM = r"(?:(\d+)\s+)?(\d+)\s*/\s*(\d+)|(\d+(?:[.,]\d+)?)"
_NUM_UNIT = re.compile(rf"(?<![\w.,/])(?:{_NUM})\s*({_UNIT_RE}" + r'|")(?![\w])', re.IGNORECASE)

TOL = 0.01  # 1 % relative Toleranz: Kataloge runden


def _norm_unit(u: str) -> str:
    u = u.strip().lower()
    return {"metre": "m", "meter": "m", "litre": "l", "liter": "l"}.get(u, u)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def parse_quantities(text: str) -> list[tuple[float, str]]:
    """Alle (Zahl, Einheit) aus einem Katalogzitat, Einheiten kleingeschrieben."""
    out = []
    for ganz, zaehler, nenner, dezimal, unit in _NUM_UNIT.findall(text or ""):
        if nenner and float(nenner) != 0:
            v = float(ganz or 0) + float(zaehler) / float(nenner)
        else:
            v = _num(dezimal)
        if v is not None:
            out.append((v, _norm_unit(unit)))
    return out


def convert(value: float, frm: str, to: str) -> float | None:
    """Umrechnung zwischen zwei Einheiten derselben Groesse, sonst None."""
    frm, to = _norm_unit(frm), _norm_unit(to)
    if frm == to:
        return value
    if frm in ("°f", "f") and to in ("°c", "c"):
        return (value - 32.0) / 1.8
    if frm in ("°c", "c") and to in ("°f", "f"):
        return value * 1.8 + 32.0
    a = _SCALE.get(frm) or _IMPERIAL.get(frm)
    b = _SCALE.get(to)
    if not a or not b or a[0] != b[0]:
        return None
    return value * a[1] / b[1]


def is_imperial(unit: str) -> bool:
    return _norm_unit(unit) in _IMPERIAL


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(abs(b) * TOL, 1e-9)


def check(value: str | None, etim_unit: str, quote: str | None) -> tuple[str, str]:
    """Wert gegen Zitat und ETIM-Einheit pruefen.

    Rueckgabe: (Befund, Klartext). Befund ist einer von
      "ok"        nichts Auffaelliges oder nachgerechnet und richtig
      "verwerfen" nachweislich falsche Einheit — Wert darf nicht in den Export
      "pruefen"   nicht entscheidbar, gehoert einem Menschen vorgelegt
    """
    v = _num(value or "")
    unit = _norm_unit(etim_unit or "")
    if v is None or not unit or unit not in _SCALE or not quote:
        return "ok", ""
    mengen = parse_quantities(quote)
    if not mengen:
        return "ok", ""

    # Passt der Wert zu irgendeiner Angabe im Zitat, sauber umgerechnet? Dann fertig.
    for qv, qu in mengen:
        erwartet = convert(qv, qu, unit)
        if erwartet is not None and close(v, erwartet):
            return "ok", ""

    # Sonst: steht der Wert unveraendert da, obwohl die Einheit eine andere ist?
    for qv, qu in mengen:
        if not close(v, qv) or _norm_unit(qu) == unit:
            continue
        erwartet = convert(qv, qu, unit)
        if erwartet is None:
            continue
        if is_imperial(qu):
            return "verwerfen", (f"imperiale Angabe '{qv:g} {qu}' unveraendert als {unit} uebernommen "
                                 f"(umgerechnet waeren es {erwartet:g} {unit})")
        return "verwerfen", (f"'{qv:g} {qu}' unveraendert als {unit} uebernommen "
                             f"(umgerechnet waeren es {erwartet:g} {unit})")

    # Imperiale Angabe im Zitat, Wert weder unveraendert noch sauber umgerechnet:
    # kann eine Baugroesse sein (1/2 Zoll Gewinde), kann ein Fehler sein.
    if any(is_imperial(qu) for _, qu in mengen):
        return "pruefen", "imperiale Angabe im Zitat, Umrechnung nicht nachvollziehbar"
    return "ok", ""
