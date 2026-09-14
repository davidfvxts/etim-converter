"""products.json -> classified.json

1. Embeddings aller ETIM-Klassen (einmalig, Cache data/cache/class_emb.npz)
2. je Artikel: Query-Embedding -> Top-K Kandidaten (Cosinus)
3. LLM wählt aus Top-K die Klasse, gibt Konfidenz + Runner-up
4. needs_review, wenn Konfidenz < Schwelle oder Klasse nicht in Kandidaten
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import config, llm
from .model import EtimModel
from .schemas import ClassCandidate, ClassDecision, ClassifiedProduct, Product

DECIDE_PROMPT = """Du ordnest einen Artikel aus einem Herstellerkatalog einer ETIM-Klasse zu.

ARTIKEL
Bezeichnung: {name}
Beschreibung: {description}
Attribute: {attributes}

KANDIDATEN (ETIM {version}, englische Klassentexte; Format: Code: Beschreibung | group | synonyms | features)
{candidates}

Regeln:
- Wähle die spezifischste Klasse, deren Merkmalsliste zum Artikel passt (ETIM-Klassen sind Produkttypen, keine Anwendungsbereiche).
- Zubehör/Ersatzteile haben oft eigene Klassen ("accessories for…", "spare part…") — nicht die Hauptproduktklasse wählen.
- Wenn keine Kandidatenklasse passt: class_id = null und in reasoning sagen, welche Art von Klasse fehlt.
- confidence: 0.9+ nur wenn eindeutig; 0.6–0.8 wenn zwei Klassen plausibel; darunter wenn geraten.
Antworte als JSON nach Schema."""


def _class_matrix(model: EtimModel) -> tuple[list[str], np.ndarray]:
    cache = config.CACHE / "class_emb.npz"
    ids = [r["id"] for r in model.classes()]
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        # Zeilenzahl mitpruefen: ein Cache aus einem fehlerhaften Lauf haette sonst
        # stillschweigend falsche Klassen geliefert (siehe llm.embed).
        if list(z["ids"]) == ids and (config.DRY_RUN == bool(z["dry"])) and z["emb"].shape[0] == len(ids):
            return ids, z["emb"]
    print(f"Embeddings für {len(ids)} Klassen berechnen …")
    texts = [model.class_text(i) for i in ids]
    emb = llm.embed(texts)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, ids=np.array(ids), emb=emb, dry=np.array(config.DRY_RUN))
    return ids, emb


def query_text(p: Product) -> str:
    attrs = "; ".join(f"{a.name}: {a.value}" for a in p.attributes[:12])
    return f"{p.name}. {p.description[:300]} {attrs}".strip()


def candidates_for(model: EtimModel, ids: list[str], emb: np.ndarray, products: list[Product], k: int) -> list[list[ClassCandidate]]:
    q = llm.embed([query_text(p) for p in products])
    sims = q @ emb.T
    out = []
    for row in sims:
        top = np.argsort(-row)[:k]
        out.append([ClassCandidate(class_id=ids[i], description=model.class_desc(ids[i]), score=float(row[i])) for i in top])
    return out


def decide(model: EtimModel, p: Product, cands: list[ClassCandidate]) -> ClassDecision:
    cand_text = "\n".join(model.class_text(c.class_id) for c in cands)
    attrs = "; ".join(f"{a.name}={a.value}" for a in p.attributes) or "—"
    return llm.generate_json(
        "decide",
        DECIDE_PROMPT.format(name=p.name, description=p.description or "—", attributes=attrs, version=config.ETIM_VERSION, candidates=cand_text),
        ClassDecision,
    )


def run(out_dir: Path, model: EtimModel | None = None) -> list[ClassifiedProduct]:
    model = model or EtimModel()
    data = json.loads((out_dir / "products.json").read_text())
    products = [Product.model_validate(p) for p in data["products"]]
    ids, emb = _class_matrix(model)
    result: list[ClassifiedProduct] = []
    batch = 50
    for i in range(0, len(products), batch):
        chunk = products[i : i + batch]
        cands = candidates_for(model, ids, emb, chunk, config.TOP_K_CLASSES)
        for p, c in zip(chunk, cands):
            d = decide(model, p, c)
            in_cands = d.class_id in {x.class_id for x in c}
            if d.class_id and not in_cands and model.class_desc(d.class_id):
                # LLM hat eine Klasse außerhalb der Kandidaten genannt, die es gibt: erlauben, aber Review
                in_cands = True
                d.confidence = min(d.confidence, 0.7)
            needs_review = (d.class_id is None) or (d.confidence < config.REVIEW_THRESHOLD) or not in_cands
            result.append(ClassifiedProduct(product=p, candidates=c[:5], decision=d, needs_review=needs_review))
        print(f"  klassifiziert {min(i + batch, len(products))}/{len(products)}")
    (out_dir / "classified.json").write_text(json.dumps([r.model_dump() for r in result], ensure_ascii=False, indent=2))
    n_rev = sum(r.needs_review for r in result)
    print(f"classify: {len(result)} Artikel, {n_rev} zur Prüfung → {out_dir / 'classified.json'}")
    return result
