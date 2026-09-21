"""classified.json -> enriched.json  (Merkmale je Artikel aus der ETIM-Klasse befüllen)"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config, llm, units
from .model import ClassFeature, EtimModel
from .schemas import ClassifiedProduct, EnrichedProduct, FeatureFill, FeatureValue

FILL_PROMPT = """Du befüllst ETIM-Merkmale für einen Artikel. Nur belegte Werte, nichts raten.

ARTIKEL
Bezeichnung: {name}
Beschreibung: {description}
Katalogattribute (wörtlich): {attributes}
Katalogzeile: {quote}

ETIM-KLASSE {class_id} — {class_desc}
MERKMALE (feature_id | Beschreibung | Typ | Einheit | erlaubte Werte):
{features}

Typen: A = Werteliste (value = EV-Code aus der Liste, nie Freitext); N = Zahl (value = Zahl mit Punkt,
in der angegebenen ETIM-Einheit — Katalogeinheit ggf. umrechnen und in source die Originalangabe zitieren);
L = logisch (value = "true"/"false"); R = Bereich (value = untere Zahl, value_max = obere Zahl).

Regeln:
- Jeder Wert braucht source = wörtliches Zitat aus Attributen/Beschreibung/Katalogzeile.
- Steht der Wert nicht im Katalog: value = null, reason angeben. Kein Branchenwissen einsetzen,
  außer es ist zwingend aus dem Produkttyp ableitbar (z. B. "Material: Stahl" bei "Stahlrohrschelle") — dann confidence ≤ 0.6.
- Gib für JEDES Merkmal der Liste einen Eintrag zurück (auch null).
Antworte als JSON nach Schema."""


# Wie viele Werte einer Werteliste in einen Prompt passen. Frueher stand hier fest 60
# mit einem "… (+N)" dahinter: alles ab dem 61. Wert war fuer das Modell schlicht
# unerreichbar, und der EV-Code-Schutz weiter unten verwarf jeden Versuch, ihn doch
# zu nennen. Bei ETIM-Merkmalen mit langen Listen (Farben, Gewindearten, Normen) hiess
# das: der richtige Wert existiert, kommt aber nie zur Sprache. Jetzt ist die Grenze
# grosszuegig, sie wird ausgesprochen, und was dahinter liegt, wird nachgefragt.
VALUES_PER_FEATURE = int(os.getenv("ETIM_VALUES_PER_FEATURE", "150"))


def _feature_line(f: ClassFeature, values: list[tuple[str, str]], total: int) -> str:
    vals = "; ".join(f"{code}={txt}" for code, txt in values) if f.type == "A" else ""
    if f.type == "A" and len(values) < total:
        vals += f"; … (nur {len(values)} von {total} Werten gezeigt)"
    return f"{f.feature_id} | {f.feature_desc} | {f.type} | {f.unit_desc or '-'} | {vals}"


def _feature_lines(feats: list[ClassFeature], part: str = "head") -> str:
    """Merkmalszeilen fuer den Prompt.

    part='head'  die ersten VALUES_PER_FEATURE Werte (erster Durchgang)
    part='tail'  nur der bisher verschwiegene Rest (Nachfrage)
    """
    lines = []
    for f in feats:
        vals = f.values[:VALUES_PER_FEATURE] if part == "head" else f.values[VALUES_PER_FEATURE:]
        lines.append(_feature_line(f, vals, len(f.values)))
    return "\n".join(lines)


def trimmed(f: ClassFeature) -> bool:
    return f.type == "A" and len(f.values) > VALUES_PER_FEATURE


def _ask(model: EtimModel, p, class_id: str, feats: list[ClassFeature], part: str) -> FeatureFill:
    attrs = "; ".join(f"{a.name}={a.value}" for a in p.attributes) or "—"
    return llm.generate_json(
        "fill",
        FILL_PROMPT.format(
            name=p.name,
            description=p.description or "—",
            attributes=attrs,
            quote=p.source_quote or "—",
            class_id=class_id,
            class_desc=model.class_desc(class_id),
            features=_feature_lines(feats, part),
        ),
        FeatureFill,
    )


def _guard(fv: FeatureValue, f: ClassFeature) -> FeatureValue:
    """Ein einzelner Merkmalswert, gegen die ETIM-Werteliste geprueft.

    Ein EV-Code, den es in dieser Klasse nicht gibt, darf nicht in Report oder Export
    gelangen — auch dann nicht, wenn er plausibel aussieht. Freitext statt Code wird
    einmal ueber den Wertetext aufgeloest, sonst faellt der Wert weg.
    """
    if f.type == "A" and fv.value is not None and fv.value not in {c for c, _ in f.values}:
        match = [c for c, t in f.values if t.lower() == str(fv.value).lower()]
        if match:
            fv.value = match[0]
        else:
            fv.reason = f"Wert '{fv.value}' nicht in Werteliste"
            fv.value = None
            fv.confidence = 0.0
    if fv.value is not None and not fv.source:
        fv.confidence = min(fv.confidence, 0.5)
    # Einheit nachrechnen, statt dem Prompt zu glauben. Der Beleg ist das Zitat —
    # steht dort eine andere Einheit als die ETIM-Einheit, muss der Wert dazu passen.
    if fv.value is not None and f.type in ("N", "R") and f.unit_desc:
        befund, warum = units.check(fv.value, f.unit_desc, fv.source)
        if befund == "verwerfen":
            fv.reason = warum
            fv.value = None
            fv.value_max = None
            fv.confidence = 0.0
        elif befund == "pruefen":
            fv.reason = warum
            fv.confidence = min(fv.confidence, 0.5)
    return fv


def fill(model: EtimModel, cp: ClassifiedProduct) -> EnrichedProduct:
    p = cp.product
    class_id = cp.decision.class_id or ""
    feats = model.features_for(class_id) if class_id else []
    values: list[FeatureValue] = []
    invented: list[str] = []
    if feats:
        allowed = {f.feature_id: f for f in feats}
        res = _ask(model, p, class_id, feats, "head")
        for fv in res.features:
            f = allowed.get(fv.feature_id)
            if not f:
                # Erfundenes Merkmal: nicht stillschweigend schlucken, sondern nennen.
                if fv.feature_id not in invented:
                    invented.append(fv.feature_id)
                continue
            values.append(_guard(fv, f))
        got = {v.feature_id for v in values}
        for f in feats:
            if f.feature_id not in got:
                values.append(FeatureValue(feature_id=f.feature_id, value=None, confidence=0.0, reason="vom Modell nicht beantwortet"))

        # Nachfrage: Merkmale, deren Werteliste gekuerzt war und die leer blieben.
        # Ohne diesen zweiten Durchgang waere der richtige Wert fuer das Modell nie
        # sichtbar gewesen — es haette ihn nicht finden koennen, nicht uebersehen.
        pos = {v.feature_id: i for i, v in enumerate(values)}
        nachfragen = [f for f in feats if trimmed(f) and values[pos[f.feature_id]].value is None]
        offen = {f.feature_id for f in nachfragen}
        if nachfragen:
            rest = _ask(model, p, class_id, nachfragen, "tail")
            for fv in rest.features:
                if fv.feature_id not in offen:
                    continue
                fv = _guard(fv, allowed[fv.feature_id])
                if fv.value is not None:
                    values[pos[fv.feature_id]] = fv
            # Was auch danach leer bleibt, ist eine bekannte Luecke, keine saubere Fehlanzeige.
            for f in nachfragen:
                v = values[pos[f.feature_id]]
                if v.value is None:
                    v.reason = ((v.reason or "nicht im Katalog")
                                + f" (Werteliste mit {len(f.values)} Werten, in zwei Schritten gefragt)")

    meta = {f.feature_id: {"desc": f.feature_desc, "type": f.type, "unit_id": f.unit_id,
                           "unit_desc": f.unit_desc, "n_values": len(f.values),
                           "values_split": trimmed(f)} for f in feats}
    low = [v for v in values if v.value is not None and v.confidence < config.REVIEW_THRESHOLD]
    # Merkmalsabdeckung: ein Artikel, bei dem kaum ein Merkmal befuellt wurde, faellt beim
    # Grosshaendler-Datencheck durch, auch wenn die wenigen befuellten Werte sicher sind.
    n_filled = sum(1 for v in values if v.value is not None)
    coverage = n_filled / len(values) if values else 0.0
    thin = bool(values) and config.MIN_COVERAGE > 0 and coverage < config.MIN_COVERAGE
    return EnrichedProduct(
        product=p,
        class_id=class_id,
        class_desc=model.class_desc(class_id) if class_id else "",
        class_confidence=cp.decision.confidence,
        features=values,
        feature_meta=meta,
        needs_review=cp.needs_review or bool(low) or thin or bool(invented),
        coverage=coverage,
        model=cp.model,
        etim_version=cp.etim_version or model.version,
        invented_codes=invented,
    )


def run(out_dir: Path, model: EtimModel | None = None) -> list[EnrichedProduct]:
    items = [ClassifiedProduct.model_validate(x) for x in json.loads((out_dir / "classified.json").read_text())]
    # Die Merkmalslisten muessen aus derselben ETIM-Version kommen wie die Klasse.
    # Sonst befuellt man Merkmale, die es in dieser Klasse gar nicht gibt.
    job_version = next((i.etim_version for i in items if i.etim_version), None)
    model = model or EtimModel(version=job_version)
    if job_version and model.version != job_version:
        raise SystemExit(
            f"Der Job wurde gegen ETIM {job_version} klassifiziert, geladen ist ETIM "
            f"{model.version}. Merkmale aus einer anderen Version waeren falsch.")
    out = []
    for i, cp in enumerate(items, 1):
        out.append(fill(model, cp))
        if i % 25 == 0:
            print(f"  Merkmale {i}/{len(items)}")
    (out_dir / "enriched.json").write_text(json.dumps([e.model_dump() for e in out], ensure_ascii=False, indent=2))
    print(f"features: {len(out)} Artikel → {out_dir / 'enriched.json'}")
    return out
