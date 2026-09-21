"""products.json -> classified.json

1. Embeddings aller ETIM-Klassen (einmalig, Cache data/cache/class_emb.npz)
2. Varianten gruppieren: Artikel mit gleichem Basisnamen werden einmal klassifiziert
3. je Gruppe: Query-Embedding -> Top-K Kandidaten (Cosinus)
4. Modell wählt aus Top-K die Klasse, gibt Konfidenz + Runner-up
5. EC-Codes in der Begründung gegen die Klassentabelle prüfen
6. needs_review, wenn Konfidenz < Schwelle oder Klasse nicht in Kandidaten
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import numpy as np

from . import checkpoint, config, jev, llm, versions
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


# --- Varianten -------------------------------------------------------------
# Kataloge listen dasselbe Produkt mehrfach, nur mit anderem verbauten Bauteil:
# "FBR-Regelgruppe 130/6 mit Grundfos UPM3 Auto 15-50 130" neben derselben Gruppe
# mit Wilo-Pumpe. Das ist ein Produkttyp, also eine ETIM-Klasse. Der Trockenlauf
# gegen strawa zeigte, dass das LLM je Variante anders entschied — fuer einen
# Grosshaendler-Datencheck der auffaelligste Fehler. Statt das per Prompt zu bitten,
# wird einmal je Gruppe klassifiziert und das Ergebnis kopiert: deterministisch,
# und es spart die Aufrufe der uebrigen Varianten.
#
# Deutsch und englisch gleichermassen: Herstellerlisten kommen in beiden Sprachen,
# und die Trennung darf nicht an der Katalogsprache haengen (CLAUDE.md, DE/EN).
#
# Diese Definition ist die einzige im Projekt. compare.py hatte bis 21.9.2026 eine
# zweite, die bereits abwich (sie trennte zusaetzlich bei "inkl."): der Vergleich
# haette also eine andere Gruppierung gemessen als die Pipeline benutzt. Die
# reichere Trennerliste ist uebernommen, compare importiert jetzt von hier.
_VARIANT_SPLIT = re.compile(r"\s+(?:mit|inkl\.?|inklusive|with|incl\.?|including)\s+", re.IGNORECASE)


def base_name(name: str) -> str:
    """Der Produkttyp ohne die verbaute Komponente: alles vor dem ersten Trenner.

    Schreibweise bleibt erhalten — dieser Name geht so in Prompt und Query-Embedding.
    Als Gruppenschluessel dient normalized_base().
    """
    head = _VARIANT_SPLIT.split(name, maxsplit=1)[0]
    head = head.strip(" ,;:-–—\t")
    # Zu kurz heisst: das Trennwort stand am Anfang und trennt hier keine Variante ab.
    return head if len(head) >= 3 else name.strip()


def normalized_base(name: str) -> str:
    """Basisname als Gruppenschluessel: klein, einfache Leerzeichen."""
    return re.sub(r"\s+", " ", base_name(name)).strip().lower()


def variant_key(p: Product) -> str:
    return normalized_base(p.name)


def group_variants(products: list[Product]) -> list[tuple[str, list[int]]]:
    """[(Gruppenschluessel, [Index in products])], in Reihenfolge des ersten Auftretens."""
    groups: dict[str, list[int]] = {}
    for i, p in enumerate(products):
        groups.setdefault(variant_key(p), []).append(i)
    return list(groups.items())


def representative(p: Product) -> Product:
    """Der Artikel, wie er stellvertretend fuer die Gruppe gefragt wird.

    Der Basisname ersetzt den Katalognamen — sowohl im Query-Embedding als auch
    im Prompt. Das Komponentenrauschen ("mit Grundfos UPM3 Auto 15-50 130") zieht
    das Retrieval sonst zur Pumpenklasse statt zur Baugruppe.
    """
    b = base_name(p.name)
    return p if b == p.name else p.model_copy(update={"name": b})


# --- EC-Codes in der Begruendung -------------------------------------------
_EC_CODE = re.compile(r"\bEC\d{6}\b")


def check_reasoning_codes(model: EtimModel, d: ClassDecision, cands: list[ClassCandidate]) -> list[str]:
    """EC-Codes aus der LLM-Antwort gegen die Klassentabelle pruefen.

    Beobachtet im strawa-Trockenlauf: zur Rechtfertigung von class_id = null nannte
    das Modell sechs Klassen als "die eigentlich passende". Eine davon (EC010091)
    existiert nicht, die uebrigen bezeichnen etwas voellig anderes (EC011609 = "Bath").
    Beides liest sich im Pruef-Cockpit wie ein ETIM-Befund. Also:
      - erfundener Code -> als nicht existent markiert (und als class_id verworfen),
      - echter Code ausserhalb der Kandidatenliste -> bekommt seine wirkliche
        Beschreibung angehaengt, damit die Fehlbehauptung neben der Wahrheit steht.
    Rueckgabe: die erfundenen Codes, fuer classified.json und die Konsolenmeldung.
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
            return f"{code} [existiert nicht in {versions.label(model.version)}]"
        return code if code in known else f'{code} ("{desc}")'

    if d.reasoning:
        d.reasoning = _EC_CODE.sub(annotate, d.reasoning)
    # Erst nach der Ersetzung anhaengen, sonst annotiert die Regex die eigene Notiz mit.
    if dropped:
        d.reasoning = (d.reasoning or "").rstrip() + (
            f" [gewaehlter Code {dropped} existiert nicht in {versions.label(model.version)} — verworfen]"
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
        "language_note": ("The article text is in the manufacturer's language (often German); "
                          "the class descriptions are English."),
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
    except jev.JevUnavailable:
        raise  # dauerhafter Ausfall: den ganzen Lauf abbrechen, nicht 250-mal wiederholen
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



def is_accessory_class(model: EtimModel, class_id: str | None) -> bool:
    return bool(class_id) and bool(ACCESSORY_RE.match(model.class_desc(class_id) or ""))


# Ab welcher Wahrscheinlichkeit die unabhaengige Zubehoerfrage als klare Aussage gilt.
ACCESSORY_CONFLICT = 0.7


def accessory_conflict(model: EtimModel, class_id: str | None, p_accessory: float | None) -> bool:
    """Jev beantwortet die Zubehoerfrage unabhaengig von der Klassenwahl. Widersprechen
    sich beide klar (Zubehoer-Artikel in Hauptproduktklasse oder umgekehrt), ist genau
    der haeufigste ETIM-Fehler wahrscheinlich — das muss in die Pruefung, statt nur im
    Begruendungstext zu stehen."""
    if not class_id or p_accessory is None:
        return False
    acc_cls = is_accessory_class(model, class_id)
    return (p_accessory >= ACCESSORY_CONFLICT and not acc_cls) or (p_accessory <= 1 - ACCESSORY_CONFLICT and acc_cls)


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
    if accessory_conflict(model, a.class_id, a.is_accessory):
        why += " WIDERSPRUCH Zubehör/Hauptprodukt — zur Prüfung."
    return ClassDecision(class_id=a.class_id, confidence=a.confidence,
                         reasoning=why, runner_up=a.runner_up), a


def _apply_group_decision(rows: list[ClassifiedProduct]) -> list[ClassifiedProduct]:
    """Innerhalb einer Variantengruppe die Entscheidung des ersten Artikels durchsetzen.

    Fuer den Weg, der die Artikel einzeln gefragt hat (Vergleichslauf). Die
    Einzelantworten bleiben in compare.json erhalten; hier geht es nur darum, dass
    die Lieferdatei nicht baugleiche Artikel in verschiedene Klassen schreibt.
    """
    erste: dict[str, ClassifiedProduct] = {}
    for r in rows:
        erste.setdefault(variant_key(r.product), r)
    zahl = {k: sum(1 for r in rows if variant_key(r.product) == k) for k in erste}
    for r in rows:
        key = variant_key(r.product)
        if zahl[key] < 2:
            continue
        kopf = erste[key]
        r.variant_group = key
        r.variant_of = None if r is kopf else kopf.product.supplier_pid
        if r is not kopf:
            abweichend = r.decision.class_id != kopf.decision.class_id
            r.decision = kopf.decision.model_copy(deep=True)
            r.needs_review = kopf.needs_review
            r.invented_codes = list(kopf.invented_codes)
            if abweichend:
                # Nicht verschweigen: die Modelle waren sich uneins, und genau das
                # ist ein Pruefgrund — auch wenn die Gruppe jetzt einheitlich ist.
                r.needs_review = True
                erste[key].needs_review = True
    return rows


def run(out_dir: Path, model: EtimModel | None = None, *,
        classifier: str | None = None,
        progress: Callable[[str, int, int, str], None] | None = None,
        fresh: bool = False) -> list[ClassifiedProduct]:
    """products.json -> classified.json mit dem gewaehlten Modell.

    classifier: "gemini" | "jev" | "both". Bei "both" laufen beide, der Vergleich
    landet in compare.json — fuer classified.json und damit fuer den Export zaehlt
    Gemini. Ein Vergleichslauf soll messen, nicht heimlich die Lieferdatei aendern.
    """
    classifier = (classifier or config.CLASSIFIER).lower()
    if classifier not in config.CLASSIFIERS:
        raise SystemExit(f"Unbekanntes Modell '{classifier}' — erlaubt: {', '.join(config.CLASSIFIERS)}")
    model = model or EtimModel()
    jev.reset_failures()

    if classifier == "both":
        # Lazy, weil compare seinerseits classify braucht.
        from . import compare

        comp = compare.run(out_dir, model, progress=progress, fresh=fresh)
        result = []
        for it in comp.items:
            a = it.answers["gemini"]
            d = ClassDecision(class_id=a.class_id, confidence=a.confidence,
                              reasoning=a.reasoning, runner_up=a.runner_up)
            cands = it.retrieval[: config.TOP_K_CLASSES]
            # Auch auf diesem Weg darf kein erfundener EC-Code in classified.json.
            invented = check_reasoning_codes(model, d, cands)
            result.append(ClassifiedProduct(
                product=it.product, candidates=cands[:5], decision=d,
                needs_review=_review_flag(model, d, cands),
                model="gemini", etim_version=model.version, simulated=a.simulated,
                invented_codes=invented))
        # Der Vergleich fragt jeden Artikel einzeln — genau das misst er ja:
        # ob ein Modell baugleiche Artikel von sich aus gleich einordnet. Fuer die
        # Lieferdatei gilt trotzdem die Zusage "eine Klasse je Produkttyp", sonst
        # haette ein both-Lauf andere Ergebnisse als ein gemini-Lauf.
        result = _apply_group_decision(result)
        return _write(out_dir, result, "gemini (Vergleich in compare.json)")

    tick = progress or (lambda *_: None)
    data = json.loads((out_dir / "products.json").read_text())
    products = [Product.model_validate(p) for p in data["products"]]

    # Varianten zusammenfassen, bevor irgendetwas bezahlt wird: eine Gruppe ist
    # ein Produkttyp und bekommt genau eine Klasse.
    groups = group_variants(products)
    n_multi = sum(1 for _, idx in groups if len(idx) > 1)
    if n_multi:
        print(f"  {len(products)} Artikel → {len(groups)} Gruppen ({n_multi} mit Varianten), "
              f"{len(products) - len(groups)} LLM-Aufrufe gespart")

    # Zwischenstand: ein abgebrochener Lauf soll die bezahlten Antworten behalten.
    # Die Gruppenzahl gehoert zu den Bedingungen — aendert sich die Gruppierung,
    # passen die alten Antworten nicht mehr zu den neuen Stellvertretern.
    cp = checkpoint.Checkpoint(out_dir, "classify", {
        "classifier": classifier, "etim_version": model.version,
        "top_k": config.JEV_TOP_K if classifier == "jev" else config.TOP_K_CLASSES,
        "dry_run": config.DRY_RUN, "n": len(products), "gruppen": len(groups)})
    if fresh:
        cp.clear()
    elif (wieder := cp.load()):
        print(f"  Zwischenstand gefunden: {wieder} Artikel schon fertig, "
              f"{len(products) - wieder} offen")

    tick("retrieval", 0, len(products), "Kandidaten suchen")
    ids, emb = _class_matrix(model)
    top_k = config.JEV_TOP_K if classifier == "jev" else config.TOP_K_CLASSES
    # Offen ist eine Gruppe, solange nicht jedes ihrer Mitglieder im Zwischenstand steht.
    offen = [(key, idxs) for key, idxs in groups
             if any(products[i].supplier_pid not in cp.done for i in idxs)]
    fertig = len(cp.done)
    invented_total: list[str] = []

    batch = 50
    for i in range(0, len(offen), batch):
        chunk = offen[i : i + batch]
        reps = [representative(products[idxs[0]]) for _, idxs in chunk]
        cands = candidates_for(model, ids, emb, reps, top_k)
        for (key, idxs), rep, c in zip(chunk, reps, cands):
            tick(classifier, fertig, len(products), rep.name[:60])
            conflict = False
            if classifier == "jev":
                d, a = decide_jev(model, rep, c)
                simulated = a.simulated
                conflict = accessory_conflict(model, d.class_id, a.is_accessory)
            else:
                d, simulated = decide(model, rep, c), config.DRY_RUN
            # Vor dem Review-Flag: ein erfundener Code wird hier zu None und muss
            # danach als "keine Klasse" in die Pruefung laufen.
            invented = check_reasoning_codes(model, d, c)
            invented_total += [x for x in invented if x not in invented_total]
            review = _review_flag(model, d, c) or conflict
            for j, pi in enumerate(idxs):
                cp.add(products[pi].supplier_pid, ClassifiedProduct(
                    product=products[pi], candidates=c[:5],
                    decision=d.model_copy(deep=True),
                    needs_review=review,
                    model=classifier, etim_version=model.version,
                    simulated=simulated,
                    variant_group=key if len(idxs) > 1 else None,
                    variant_of=None if j == 0 else products[idxs[0]].supplier_pid,
                    invented_codes=list(invented)).model_dump())
                fertig += 1
        print(f"  klassifiziert {fertig}/{len(products)}")
    cp.save()
    tick(classifier, len(products), len(products), "fertig")

    # In der Reihenfolge des Katalogs ausgeben, nicht in der des Zwischenstands.
    result = [ClassifiedProduct.model_validate(cp.done[p.supplier_pid])
              for p in products if p.supplier_pid in cp.done]

    # Die Variantengruppen einzeln nennen: das ist die Zeile, an der man ohne
    # Diff sieht, ob baugleiche Artikel dieselbe Klasse tragen.
    by_pid = {r.product.supplier_pid: r for r in result}
    multi = [(key, idxs) for key, idxs in groups if len(idxs) > 1]
    for key, idxs in multi[:15]:
        r = by_pid.get(products[idxs[0]].supplier_pid)
        cid = r.decision.class_id if r else None
        print(f'  Gruppe "{key[:60]}" ({len(idxs)} Artikel) → {cid or "keine Klasse"}')
    if len(multi) > 15:
        print(f"  … und {len(multi) - 15} weitere Gruppen")
    if invented_total:
        print(f"  Achtung: {len(invented_total)} erfundene EC-Codes in Begründungen markiert: "
              + ", ".join(invented_total[:10]))

    out = _write(out_dir, result, classifier)
    cp.clear()
    return out


def _write(out_dir: Path, result: list[ClassifiedProduct], label: str) -> list[ClassifiedProduct]:
    (out_dir / "classified.json").write_text(
        json.dumps([r.model_dump() for r in result], ensure_ascii=False, indent=2))
    n_rev = sum(r.needs_review for r in result)
    n_none = sum(1 for r in result if not r.decision.class_id)
    v = result[0].etim_version if result else ""
    print(f"classify [{label}{f' · ETIM {v}' if v else ''}]: {len(result)} Artikel, {n_none} ohne Klasse, "
          f"{n_rev} zur Prüfung → {out_dir / 'classified.json'}")
    return result
