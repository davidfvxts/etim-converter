"""classified.json -> enriched.json  (Merkmale je Artikel aus der ETIM-Klasse befüllen)"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, llm
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


def _feature_lines(feats: list[ClassFeature]) -> str:
    lines = []
    for f in feats:
        vals = ""
        if f.type == "A":
            vals = "; ".join(f"{code}={txt}" for code, txt in f.values[:60])
            if len(f.values) > 60:
                vals += f"; … (+{len(f.values) - 60})"
        lines.append(f"{f.feature_id} | {f.feature_desc} | {f.type} | {f.unit_desc or '-'} | {vals}")
    return "\n".join(lines)


def fill(model: EtimModel, cp: ClassifiedProduct) -> EnrichedProduct:
    p = cp.product
    class_id = cp.decision.class_id or ""
    feats = model.features_for(class_id) if class_id else []
    values: list[FeatureValue] = []
    if feats:
        attrs = "; ".join(f"{a.name}={a.value}" for a in p.attributes) or "—"
        res = llm.generate_json(
            "fill",
            FILL_PROMPT.format(
                name=p.name,
                description=p.description or "—",
                attributes=attrs,
                quote=p.source_quote or "—",
                class_id=class_id,
                class_desc=model.class_desc(class_id),
                features=_feature_lines(feats),
            ),
            FeatureFill,
        )
        allowed = {f.feature_id: f for f in feats}
        for fv in res.features:
            f = allowed.get(fv.feature_id)
            if not f:
                continue
            if f.type == "A" and fv.value is not None and fv.value not in {c for c, _ in f.values}:
                # Freitext statt EV-Code -> versuchen zu mappen, sonst verwerfen
                match = [c for c, t in f.values if t.lower() == str(fv.value).lower()]
                if match:
                    fv.value = match[0]
                else:
                    fv.reason = f"Wert '{fv.value}' nicht in Werteliste"
                    fv.value = None
                    fv.confidence = 0.0
            if fv.value is not None and not fv.source:
                fv.confidence = min(fv.confidence, 0.5)
            values.append(fv)
        got = {v.feature_id for v in values}
        for f in feats:
            if f.feature_id not in got:
                values.append(FeatureValue(feature_id=f.feature_id, value=None, confidence=0.0, reason="vom Modell nicht beantwortet"))
    meta = {f.feature_id: {"desc": f.feature_desc, "type": f.type, "unit_id": f.unit_id, "unit_desc": f.unit_desc, "n_values": len(f.values)} for f in feats}
    low = [v for v in values if v.value is not None and v.confidence < config.REVIEW_THRESHOLD]
    return EnrichedProduct(
        product=p,
        class_id=class_id,
        class_desc=model.class_desc(class_id) if class_id else "",
        class_confidence=cp.decision.confidence,
        features=values,
        feature_meta=meta,
        needs_review=cp.needs_review or bool(low),
    )


def run(out_dir: Path, model: EtimModel | None = None) -> list[EnrichedProduct]:
    model = model or EtimModel()
    items = [ClassifiedProduct.model_validate(x) for x in json.loads((out_dir / "classified.json").read_text())]
    out = []
    for i, cp in enumerate(items, 1):
        out.append(fill(model, cp))
        if i % 25 == 0:
            print(f"  Merkmale {i}/{len(items)}")
    (out_dir / "enriched.json").write_text(json.dumps([e.model_dump() for e in out], ensure_ascii=False, indent=2))
    print(f"features: {len(out)} Artikel → {out_dir / 'enriched.json'}")
    return out
