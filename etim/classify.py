"""products.json -> classified.json

1. Embeddings aller ETIM-Klassen (einmalig, Cache data/cache/class_emb.npz)
2. Varianten gruppieren: Artikel mit gleichem Basisnamen werden einmal klassifiziert
3. je Gruppe: Query-Embedding -> Top-K Kandidaten (Cosinus)
4. LLM wählt aus Top-K die Klasse, gibt Konfidenz + Runner-up
5. EC-Codes in der Begründung gegen die Klassentabelle prüfen
6. needs_review, wenn Konfidenz < Schwelle oder Klasse nicht in Kandidaten
"""
from __future__ import annotations

import json
import re
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


# --- Varianten -------------------------------------------------------------
# Kataloge listen dasselbe Produkt mehrfach, nur mit anderem verbauten Bauteil:
# "FBR-Regelgruppe 130/6 mit Grundfos UPM3 Auto 15-50 130" neben derselben Gruppe
# mit Wilo-Pumpe. Das ist ein Produkttyp, also eine ETIM-Klasse. Der Trockenlauf
# gegen strawa zeigte, dass das LLM je Variante anders entschied — für einen
# Großhändler-Datencheck der auffälligste Fehler. Statt das per Prompt zu bitten,
# wird einmal je Gruppe klassifiziert und das Ergebnis kopiert: deterministisch,
# und es spart die Aufrufe der übrigen Varianten.
_VARIANT_SPLIT = re.compile(r"\s+mit\s+", re.IGNORECASE)


def base_name(name: str) -> str:
    """Der Produkttyp ohne die verbaute Komponente: alles vor dem ersten ' mit '."""
    head = _VARIANT_SPLIT.split(name, maxsplit=1)[0]
    head = head.strip(" ,;:-–—\t")
    # Zu kurz heißt: ' mit ' stand am Anfang und trennt hier keine Variante ab.
    return head if len(head) >= 3 else name.strip()


def variant_key(p: Product) -> str:
    return re.sub(r"\s+", " ", base_name(p.name)).strip().lower()


def group_variants(products: list[Product]) -> list[tuple[str, list[int]]]:
    """[(Gruppenschlüssel, [Index in products])], in Reihenfolge des ersten Auftretens."""
    groups: dict[str, list[int]] = {}
    for i, p in enumerate(products):
        groups.setdefault(variant_key(p), []).append(i)
    return list(groups.items())


def representative(p: Product) -> Product:
    """Der Artikel, wie er stellvertretend für die Gruppe gefragt wird.

    Der Basisname ersetzt den Katalognamen — sowohl im Query-Embedding als auch
    im Prompt. Das Komponentenrauschen ("mit Grundfos UPM3 Auto 15-50 130") zieht
    das Retrieval sonst zur Pumpenklasse statt zur Baugruppe.
    """
    b = base_name(p.name)
    return p if b == p.name else p.model_copy(update={"name": b})


# --- EC-Codes in der Begründung -------------------------------------------
_EC_CODE = re.compile(r"\bEC\d{6}\b")


def check_reasoning_codes(model: EtimModel, d: ClassDecision, cands: list[ClassCandidate]) -> list[str]:
    """EC-Codes aus der LLM-Antwort gegen die Klassentabelle prüfen.

    Beobachtet im strawa-Trockenlauf: zur Rechtfertigung von class_id = null nannte
    das Modell sechs Klassen als "die eigentlich passende". Eine davon (EC010091)
    existiert nicht, die übrigen bezeichnen etwas völlig anderes (EC011609 = "Bath").
    Beides liest sich im Prüf-Cockpit wie ein ETIM-Befund. Also:
      - erfundener Code -> als nicht existent markiert (und als class_id verworfen),
      - echter Code außerhalb der Kandidatenliste -> bekommt seine wirkliche
        Beschreibung angehängt, damit die Fehlbehauptung neben der Wahrheit steht.
    Rückgabe: die erfundenen Codes, für classified.json und die Konsolenmeldung.
    """
    invented: list[str] = []

    def note(code: str) -> None:
        if code not in invented:
            invented.append(code)

    dropped = None
    if d.class_id and not model.class_desc(d.class_id):
        dropped = d.class_id
        note(dropped)
        d.class_id = None
        d.confidence = min(d.confidence, 0.5)

    if d.runner_up and not model.class_desc(d.runner_up):
        note(d.runner_up)
        d.runner_up = None

    # Die Kandidatenliste und die gewaehlte Klasse hat das Modell im Klartext gesehen;
    # nur alles Uebrige ist eine Behauptung, die einen Gegenbeleg braucht.
    known = {c.class_id for c in cands} | {d.class_id}

    def annotate(m: re.Match) -> str:
        code = m.group(0)
        desc = model.class_desc(code)
        if not desc:
            note(code)
            return f"{code} [existiert nicht in {config.ETIM_VERSION}]"
        return code if code in known else f'{code} ("{desc}")'

    if d.reasoning:
        d.reasoning = _EC_CODE.sub(annotate, d.reasoning)
    # Erst nach der Ersetzung anhaengen, sonst annotiert die Regex die eigene Notiz mit.
    if dropped:
        d.reasoning = (d.reasoning or "").rstrip() + (
            f" [gewaehlter Code {dropped} existiert nicht in {config.ETIM_VERSION} — verworfen]"
        )
    return invented


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

    groups = group_variants(products)
    n_multi = sum(1 for _, idx in groups if len(idx) > 1)
    if n_multi:
        print(f"  {len(products)} Artikel → {len(groups)} Gruppen ({n_multi} mit Varianten), "
              f"{len(products) - len(groups)} LLM-Aufrufe gespart")
    reps = [representative(products[idx[0]]) for _, idx in groups]

    result: list[ClassifiedProduct | None] = [None] * len(products)
    invented_total: list[str] = []
    batch = 50
    for i in range(0, len(reps), batch):
        chunk = reps[i : i + batch]
        cands = candidates_for(model, ids, emb, chunk, config.TOP_K_CLASSES)
        for (key, idxs), rep, c in zip(groups[i : i + batch], chunk, cands):
            d = decide(model, rep, c)
            invented = check_reasoning_codes(model, d, c)
            invented_total += [x for x in invented if x not in invented_total]
            in_cands = d.class_id in {x.class_id for x in c}
            if d.class_id and not in_cands:
                # Klasse außerhalb der Kandidaten, die es gibt: erlauben, aber Review.
                # (Erfundene Codes sind in check_reasoning_codes schon auf None gesetzt.)
                in_cands = True
                d.confidence = min(d.confidence, 0.7)
            needs_review = (d.class_id is None) or (d.confidence < config.REVIEW_THRESHOLD) or not in_cands
            for j, pi in enumerate(idxs):
                result[pi] = ClassifiedProduct(
                    product=products[pi],
                    candidates=c[:5],
                    decision=d.model_copy(deep=True),
                    needs_review=needs_review,
                    variant_group=key if len(idxs) > 1 else None,
                    variant_of=None if j == 0 else products[idxs[0]].supplier_pid,
                    invented_codes=list(invented),
                )
        print(f"  klassifiziert {min(i + batch, len(reps))}/{len(reps)} Gruppen")

    final = [r for r in result if r is not None]
    assert len(final) == len(products)

    # Die Variantengruppen einzeln nennen: das ist die Zeile, an der man ohne
    # Diff sieht, ob baugleiche Artikel jetzt dieselbe Klasse tragen.
    multi = [(key, idxs) for key, idxs in groups if len(idxs) > 1]
    for key, idxs in multi[:15]:
        cid = final[idxs[0]].decision.class_id
        print(f'  Gruppe "{key[:60]}" ({len(idxs)} Artikel) → {cid or "keine Klasse"}')
    if len(multi) > 15:
        print(f"  … und {len(multi) - 15} weitere Gruppen")

    (out_dir / "classified.json").write_text(json.dumps([r.model_dump() for r in final], ensure_ascii=False, indent=2))
    n_rev = sum(r.needs_review for r in final)
    n_cls = sum(1 for r in final if r.decision.class_id)
    print(f"classify: {len(final)} Artikel, {n_cls} mit Klasse, {n_rev} zur Prüfung → {out_dir / 'classified.json'}")
    if invented_total:
        print(f"  Achtung: {len(invented_total)} erfundene EC-Codes in Begründungen markiert: "
              + ", ".join(invented_total[:10]))
    return final
