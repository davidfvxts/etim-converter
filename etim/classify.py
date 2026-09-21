"""products.json -> classified.json

1. Embeddings aller ETIM-Klassen (einmalig, Cache data/cache/class_emb.npz)
2. je Artikel: Query-Embedding -> Top-K Kandidaten (Cosinus)
3. LLM wählt aus Top-K die Klasse, gibt Konfidenz + Runner-up
4. needs_review, wenn Konfidenz < Schwelle oder Klasse nicht in Kandidaten
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from . import config, jev, llm, versions
from .model import EtimModel
from .schemas import ClassCandidate, ClassDecision, ClassifiedProduct, ModelAnswer, Product

NONE_OPTION = "none"

# Aus dem Klassentext ablesbare Zubehoerklassen. ETIM fuehrt Zubehoer und
# Ersatzteile in eigenen Klassen — genau an dieser Grenze irrt die Zuordnung am
# haeufigsten, darum bekommt Jev sie als "not_for" ausdruecklich mitgegeben.
ACCESSORY_RE = re.compile(r"^\s*(accessor|spare part|mounting material|assembly material)", re.I)

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
    # Je Version ein eigener Cache. Die ID-Liste allein reicht als Schutz nicht:
    # zwischen ETIM 8.0 und 9.0 behalten 275 Klassen ihre ID und aendern ihren
    # Text (EC000024 "Installation box for underfloor-installation" wird
    # "Device installation insert for subfloor installation"). Bei gleicher
    # ID-Liste waere so ein veralteter Cache unbemerkt durchgegangen — darum
    # steht die Version mit im Cache und wird mitgeprueft.
    cache = versions.emb_path(model.version)
    ids = [r["id"] for r in model.classes()]
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        cached_version = str(z["version"]) if "version" in z.files else ""
        if (list(z["ids"]) == ids
                and cached_version == model.version
                and (config.DRY_RUN == bool(z["dry"]))
                # Zeilenzahl mitpruefen: ein Cache aus einem fehlerhaften Lauf
                # haette sonst falsche Klassen geliefert (siehe llm.embed).
                and z["emb"].shape[0] == len(ids)):
            return ids, z["emb"]
        if cached_version and cached_version != model.version:
            print(f"Cache gehoert zu ETIM {cached_version}, gebraucht wird {model.version} — neu berechnen.")
    print(f"Embeddings für {len(ids)} Klassen (ETIM {model.version}) berechnen …")
    texts = [model.class_text(i) for i in ids]
    emb = llm.embed(texts)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez(cache, ids=np.array(ids), emb=emb, dry=np.array(config.DRY_RUN),
             version=np.array(model.version))
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
        DECIDE_PROMPT.format(name=p.name, description=p.description or "—", attributes=attrs, version=versions.label(model.version), candidates=cand_text),
        ClassDecision,
    )


# ----------------------------------------------------- Klassenwahl durch Jev

def _tokens(obj) -> int:
    """Grobe Tokenschaetzung. Reicht, um unter Jevs 32k-Fenster zu bleiben."""
    return len(json.dumps(obj, ensure_ascii=False)) // 4


def _class_option(model: EtimModel, class_id: str, level: int) -> dict | str:
    """Strukturierte Optionsbeschreibung einer ETIM-Klasse.

    Jev liest JSON in den Optionsbeschreibungen. Statt den Klassentext zu einem
    Satz zu verkleben, bekommt jede Option benannte Felder — und vor allem ein
    `not_for`, das die Grenze Hauptprodukt/Zubehoer ausspricht, statt sie zu raten.
    `level` steuert, wie ausfuehrlich es wird (Kontextbudget).
    """
    desc = model.class_desc(class_id)
    row = model.con.execute(
        "SELECT g.description AS group_desc FROM classes c LEFT JOIN groups g ON g.id=c.group_id WHERE c.id=?",
        (class_id,),
    ).fetchone()
    group = row["group_desc"] if row and row["group_desc"] else ""
    is_acc = bool(ACCESSORY_RE.match(desc))

    if level >= 3:  # knappste Form: nur noch der Klassentext
        return desc

    opt: dict = {"what": desc}
    if group and level <= 1:
        opt["group"] = group
    if is_acc:
        opt["not_for"] = "Not the complete product itself — only parts and accessories sold for it."
    elif level <= 2:
        opt["not_for"] = "Not accessories, spare parts or mounting material sold for this product — ETIM has separate accessory classes for those."

    syn = [r[0] for r in model.con.execute("SELECT synonym FROM synonyms WHERE class_id=?", (class_id,))]
    cap = {0: 10, 1: 6, 2: 3}.get(level, 0)
    if syn and cap:
        opt["examples"] = syn[:cap]
    if level == 0:
        feats = [r[0] for r in model.con.execute(
            "SELECT f.description FROM class_features cf JOIN features f ON f.id=cf.feature_id "
            "WHERE cf.class_id=? ORDER BY cf.sort LIMIT 6", (class_id,))]
        if feats:
            opt["has_features"] = feats
    return opt


def class_criteria(model: EtimModel, cands: list[ClassCandidate], budget: int) -> tuple[dict, str, int]:
    """Kandidatenfeld als Choice-Optionen, passend zu Jevs Kontextfenster.

    Rueckgabe: (criteria, Kuerzungsstufe im Klartext, Zahl der Kandidaten).
    Lieber ausfuehrliche Beschreibungen fuer weniger Klassen als abgeschnittene
    fuer alle — erst wenn auch die knappste Form nicht passt, faellt das Feld.
    """
    labels = {0: "voll", 1: "ohne Merkmalslisten", 2: "knapp", 3: "nur Klassentext"}
    used = list(cands)
    while used:
        for level in (0, 1, 2, 3):
            crit = {c.class_id: _class_option(model, c.class_id, level) for c in used}
            crit[NONE_OPTION] = {
                "what": "No class in this list fits the article.",
                "use_when": "The article is a product type that none of the listed classes describes.",
            }
            if _tokens(crit) <= budget:
                note = labels[level]
                if len(used) < len(cands):
                    note += f", Feld auf {len(used)} von {len(cands)} gekuerzt"
                return crit, note, len(used)
        used = used[: int(len(used) * 0.8)]
    raise ValueError("Kandidatenfeld passt in keiner Form in das Jev-Kontextfenster")


def product_state(p: Product) -> dict:
    """Artikel als strukturierter Zustand. Jev ist auf Struktur trainiert."""
    return {
        "article_name": p.name,
        "description": p.description or None,
        "catalogue_attributes": {a.name: a.value for a in p.attributes[:24]} or None,
        "catalogue_line": (p.source_quote or None),
        "language_note": "The article text is German; the class descriptions are English.",
    }


# ------------------------------------------------------------ Klassenzuordnung

CLASS_INSTRUCTIONS = {
    "question": "Which ETIM class describes this catalogue article?",
    "rules": [
        "Pick the most specific class whose product type matches the article itself.",
        "ETIM classes are product types, not application areas.",
        "If the article is an accessory or a spare part, pick the accessory class, not the main product class.",
        f"Pick '{NONE_OPTION}' only if no listed class describes this product type.",
    ],
}

ACCESSORY_INSTRUCTIONS = {
    "question": "Is this catalogue article an accessory, spare part or mounting material sold for another product?",
    "note": "A complete, independently usable product is not an accessory, even when it is built into a larger system.",
}


def ask_jev(model: EtimModel, p: Product, cands: list[ClassCandidate]) -> ModelAnswer:
    """Klasse und Zubehoerfrage in einem einzigen Jev-Aufruf.

    Beide Fragen laufen gegen denselben Zustand und werden parallel ausgewertet;
    die zweite kostet ein paar Token und praktisch keine Zeit. Sie ist bewusst
    unabhaengig gestellt: Jev nutzt die Klassenantwort nicht als Kontext, also
    ist sie ein echtes Gegensignal und keine Nacherzaehlung.
    """
    ranks = {c.class_id: i + 1 for i, c in enumerate(cands)}
    answer = ModelAnswer(model="jev", candidates_seen=len(cands))
    try:
        crit, trim, n_used = class_criteria(model, cands, budget=24_000)
        answer.candidates_seen = n_used
        answer.trim_level = trim
        res = jev.ask("classify", product_state(p), {
            "etim_class": jev.choice(CLASS_INSTRUCTIONS, crit),
            "is_accessory": jev.noul(
                ACCESSORY_INSTRUCTIONS,
                "The article is an accessory, spare part or mounting material for another product.",
                "The article is a complete product in its own right.",
            ),
        })
    except (jev.JevError, ValueError) as e:
        answer.error = str(e)
        return answer

    choice, conf, probs = res.choice_of("etim_class")
    answer.class_id = None if choice in (None, NONE_OPTION) else choice
    answer.confidence = conf
    answer.is_accessory = res.noul_of("is_accessory")
    answer.simulated = res.simulated
    answer.latency_ms = res.latency_ms
    answer.input_tokens = res.usage.get("input_tokens", 0)
    answer.output_tokens = res.usage.get("output_tokens", 0)
    answer.cost_usd = res.cost_usd
    top = sorted(probs.items(), key=lambda kv: -kv[1])[:6]
    answer.probabilities = {k: round(float(v), 4) for k, v in top if v > 0}
    answer.runner_up = next((k for k, _ in top if k != choice), None)
    answer.rank_of_choice = ranks.get(answer.class_id or "")
    return answer



def _review_flag(model: EtimModel, d: ClassDecision, cands: list[ClassCandidate]) -> bool:
    """Gemeinsame Review-Regel fuer beide Modelle."""
    in_cands = d.class_id in {x.class_id for x in cands}
    if d.class_id and not in_cands and model.class_desc(d.class_id):
        # Eine existierende Klasse ausserhalb des Kandidatenfelds: erlauben, aber pruefen.
        in_cands = True
        d.confidence = min(d.confidence, 0.7)
    return (d.class_id is None) or (d.confidence < config.REVIEW_THRESHOLD) or not in_cands


def decide_jev(model: EtimModel, p: Product, cands: list[ClassCandidate]) -> tuple[ClassDecision, ModelAnswer]:
    """Jevs Klassenwahl als ClassDecision, damit der Rest der Pipeline sie kennt.

    `reasoning` ist kein Modelltext — Jev erzeugt keinen. Es ist ein aus der
    Wahrscheinlichkeitsverteilung gebauter Satz, damit im Report nachvollziehbar
    steht, wie knapp die Entscheidung war. Erfundene EC-Codes kann er nicht
    enthalten, weil nur Optionen aus dem Kandidatenfeld darin vorkommen.
    """
    a = ask_jev(model, p, cands)
    if a.error:
        return ClassDecision(class_id=None, confidence=0.0,
                             reasoning=f"Jev nicht erreichbar: {a.error}", runner_up=None), a
    if a.class_id is None:
        why = "Jev hat keine der Kandidatenklassen gewählt."
    else:
        top = ", ".join(f"{c} {v:.0%}" for c, v in list(a.probabilities.items())[:3])
        why = f"Jev wählte {a.class_id} ({model.class_desc(a.class_id)}). Verteilung: {top}."
    if a.is_accessory is not None and a.is_accessory >= 0.5:
        why += f" Unabhängig gefragt: zu {a.is_accessory:.0%} Zubehör/Ersatzteil."
    return ClassDecision(class_id=a.class_id, confidence=a.confidence,
                         reasoning=why, runner_up=a.runner_up), a


def run(out_dir: Path, model: EtimModel | None = None, *,
        classifier: str | None = None) -> list[ClassifiedProduct]:
    """products.json -> classified.json mit dem gewaehlten Modell.

    classifier: "gemini" | "jev" | "both". Bei "both" laufen beide, der Vergleich
    landet in compare.json — fuer classified.json und damit fuer den Export zaehlt
    Gemini. Ein Vergleichslauf soll messen, nicht heimlich die Lieferdatei aendern.
    """
    classifier = (classifier or config.CLASSIFIER).lower()
    if classifier not in config.CLASSIFIERS:
        raise SystemExit(f"Unbekanntes Modell '{classifier}' — erlaubt: {', '.join(config.CLASSIFIERS)}")
    model = model or EtimModel()

    if classifier == "both":
        # Lazy, weil compare seinerseits classify braucht.
        from . import compare

        comp = compare.run(out_dir, model)
        result = []
        for it in comp.items:
            a = it.answers["gemini"]
            d = ClassDecision(class_id=a.class_id, confidence=a.confidence,
                              reasoning=a.reasoning, runner_up=a.runner_up)
            cands = it.retrieval[: config.TOP_K_CLASSES]
            result.append(ClassifiedProduct(
                product=it.product, candidates=cands[:5], decision=d,
                needs_review=_review_flag(model, d, cands),
                model="gemini", etim_version=model.version, simulated=a.simulated))
        return _write(out_dir, result, "gemini (Vergleich in compare.json)")

    data = json.loads((out_dir / "products.json").read_text())
    products = [Product.model_validate(p) for p in data["products"]]
    ids, emb = _class_matrix(model)
    top_k = config.JEV_TOP_K if classifier == "jev" else config.TOP_K_CLASSES
    result: list[ClassifiedProduct] = []
    batch = 50
    for i in range(0, len(products), batch):
        chunk = products[i : i + batch]
        cands = candidates_for(model, ids, emb, chunk, top_k)
        for p, c in zip(chunk, cands):
            if classifier == "jev":
                d, a = decide_jev(model, p, c)
                simulated = a.simulated
            else:
                d, simulated = decide(model, p, c), config.DRY_RUN
            result.append(ClassifiedProduct(
                product=p, candidates=c[:5], decision=d,
                needs_review=_review_flag(model, d, c),
                model=classifier, etim_version=model.version, simulated=simulated))
        print(f"  klassifiziert {min(i + batch, len(products))}/{len(products)}")
    return _write(out_dir, result, classifier)


def _write(out_dir: Path, result: list[ClassifiedProduct], label: str) -> list[ClassifiedProduct]:
    (out_dir / "classified.json").write_text(
        json.dumps([r.model_dump() for r in result], ensure_ascii=False, indent=2))
    n_rev = sum(r.needs_review for r in result)
    n_none = sum(1 for r in result if not r.decision.class_id)
    v = result[0].etim_version if result else ""
    print(f"classify [{label}{f' · ETIM {v}' if v else ''}]: {len(result)} Artikel, {n_none} ohne Klasse, "
          f"{n_rev} zur Prüfung → {out_dir / 'classified.json'}")
    return result
