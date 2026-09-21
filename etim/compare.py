"""Gemini gegen Jev auf derselben Retrieval-Liste.

Warum es diesen Vergleich gibt: im strawa-Trockenlauf lag die richtige Klasse
auf Rang 10–62 der Retrieval-Liste, Gemini sieht davon nur die Top-20. Jev darf
bis zu 255 Optionen sehen und kann keinen Code erfinden, der nicht in der Liste
steht. Beide Modelle bekommen hier denselben Artikel und dieselbe Kandidatenliste
— nur unterschiedlich breit zugeschnitten. Alles, was danach verglichen wird,
ist damit eine Aussage ueber die Modelle, nicht ueber das Retrieval.

Ergebnis: out/<job>/compare.json
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import classify, config, jev, llm, versions
from .model import ClassFeature, EtimModel
from .schemas import (ClassCandidate, ClassDecision, Comparison, ComparisonItem,
                      FeatureAnswer, FeatureComparison, ModelAnswer, ModelMetrics,
                      Product, ReasoningCode)

EC_RE = re.compile(r"\bEC\d{6}\b")

# Die Jev-Klassenzuordnung steht in classify.py — sie ist Klassifizierung, nicht
# Vergleich, und classify.run benutzt sie auch ohne dieses Modul. Hier nur
# weitergereicht, damit der Vergleich mit denselben Bausteinen arbeitet.
from .classify import (ACCESSORY_INSTRUCTIONS, CLASS_INSTRUCTIONS,  # noqa: E402
                       NONE_OPTION, _tokens, ask_jev, class_criteria, product_state)

# Bewusst weitergereicht: der Vergleich und seine Tests sprechen diese Bausteine
# ueber compare an, auch wenn sie inzwischen in classify stehen.
REEXPORTED = (ACCESSORY_INSTRUCTIONS, CLASS_INSTRUCTIONS, class_criteria)


# ------------------------------------------------------------------ Hilfsteile

def base_name(name: str) -> str:
    """Bezeichnung ohne Variantenteil.

    Kundenkataloge haengen die Variante an den Grundtyp: "FBR-Regelgruppe 130/6
    mit Grundfos UPM3 Auto 15-50". Alles ab dem Trenner ist die Variante; davor
    steht dasselbe Produkt. Artikel mit gleichem Basisnamen muessen dieselbe
    ETIM-Klasse bekommen — sonst faellt es beim Grosshaendler-Datencheck auf.
    """
    s = re.split(r"\s+(?:mit|inkl\.?|inklusive|with)\s+", name, maxsplit=1, flags=re.I)[0]
    s = re.sub(r"[,;]\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def reasoning_codes(model: EtimModel, text: str) -> list[ReasoningCode]:
    """Jeden EC-Code aus einer Begruendung gegen die Klassentabelle pruefen.

    Im strawa-Lauf nannte Gemini sechs EC-Codes als "eigentlich passende Klasse";
    einer existierte gar nicht, die uebrigen bezeichneten Badewannen und
    Gummiplatten. Solche Saetze sehen aus wie ein ETIM-Befund und duerfen nie
    ungeprueft in einen Herstellerreport geraten.
    """
    out: list[ReasoningCode] = []
    for code in dict.fromkeys(EC_RE.findall(text or "")):
        desc = model.class_desc(code)
        out.append(ReasoningCode(code=code, known=bool(desc), desc=desc))
    return out


def ask_gemini(model: EtimModel, p: Product, cands: list[ClassCandidate]) -> ModelAnswer:
    """Gemini auf dem unveraenderten Pfad aus classify.py, nur mit Messpunkten."""
    ranks = {c.class_id: i + 1 for i, c in enumerate(cands)}
    answer = ModelAnswer(model="gemini", candidates_seen=len(cands))
    stats: dict = {}
    cand_text = "\n".join(model.class_text(c.class_id) for c in cands)
    attrs = "; ".join(f"{a.name}={a.value}" for a in p.attributes) or "—"
    try:
        d = llm.generate_json(
            "decide",
            classify.DECIDE_PROMPT.format(
                name=p.name, description=p.description or "—", attributes=attrs,
                version=versions.label(model.version), candidates=cand_text),
            ClassDecision, stats=stats,
        )
    except RuntimeError as e:
        answer.error = str(e)
        return answer
    return _from_gemini(model, d, answer, stats, ranks)


def _from_gemini(model: EtimModel, d: ClassDecision, answer: ModelAnswer,
                 stats: dict, ranks: dict[str, int]) -> ModelAnswer:
    answer.class_id = d.class_id
    answer.confidence = d.confidence
    answer.reasoning = d.reasoning
    answer.reasoning_codes = reasoning_codes(model, d.reasoning)
    answer.runner_up = d.runner_up
    answer.rank_of_choice = ranks.get(d.class_id or "")
    answer.latency_ms = stats.get("latency_ms", 0)
    answer.input_tokens = stats.get("input_tokens", 0)
    answer.output_tokens = stats.get("output_tokens", 0)
    answer.simulated = bool(stats.get("simulated"))
    answer.cost_usd = (answer.input_tokens / 1e6 * config.GEMINI_INPUT_USD_PER_M
                       + answer.output_tokens / 1e6 * config.GEMINI_OUTPUT_USD_PER_M)
    return answer


# ------------------------------------------------------------------- Merkmale

FEATURE_NOUL = "Answer for the article described in the state."


def _feature_questions(feats: list[ClassFeature], budget: int) -> tuple[dict, dict[str, str]]:
    """ETIM-Merkmale als Jev-Fragen — und die Begruendung fuer jedes ausgelassene.

    Logische Merkmale (Typ L) sind Noul-Fragen, Wertelisten (Typ A) sind Choice.
    Numerische Merkmale (N) und Bereiche (R) bleiben aussen vor: Jev waehlt aus
    vorgegebenen Optionen, er liest keine Zahl aus einem Text. Die bleiben Gemini.
    """
    questions: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    usable: list[ClassFeature] = []
    for f in feats:
        if f.type == "L":
            usable.append(f)
        elif f.type == "A" and f.values:
            if len(f.values) + 1 > jev.MAX_CHOICE_OPTIONS:
                skipped[f.feature_id] = f"Werteliste mit {len(f.values)} Eintraegen, Jev erlaubt {jev.MAX_CHOICE_OPTIONS - 1}"
            else:
                usable.append(f)
        elif f.type in ("N", "R"):
            skipped[f.feature_id] = "numerisch — Jev waehlt nur aus vorgegebenen Optionen"
        else:
            skipped[f.feature_id] = f"Typ {f.type or '?'} ohne Werteliste"

    # Grosse Wertelisten zuerst fallen lassen, wenn das Kontextfenster nicht reicht.
    usable.sort(key=lambda f: len(f.values))
    for f in usable:
        if f.type == "L":
            q = jev.noul({"question": f"Does this article have the property: {f.feature_desc}?",
                          "note": FEATURE_NOUL},
                         f"The article has: {f.feature_desc}",
                         f"The article does not have: {f.feature_desc}")
        else:
            crit = {code: text for code, text in f.values}
            crit[NONE_OPTION] = "The catalogue text does not state this property."
            q = jev.choice({"question": f"Which value does this article have for: {f.feature_desc}?",
                            "unit": f.unit_desc or None,
                            "rule": f"Pick '{NONE_OPTION}' unless the catalogue text states it."},
                           crit)
        if _tokens({**questions, f.feature_id: q}) > budget:
            skipped[f.feature_id] = "Kontextbudget des Aufrufs erschoepft"
            continue
        questions[f.feature_id] = q
    return questions, skipped


def compare_features(model: EtimModel, p: Product, class_id: str,
                     gemini_feats: list[dict]) -> FeatureComparison:
    """Merkmale beider Modelle nebeneinander — mit der Quellzitat-Pflicht.

    Jev erzeugt keinen Text und kann darum keinen Beleg liefern. Ein von Jev
    gewaehlter Wert gilt nur dann als exportfaehig, wenn Gemini fuer dasselbe
    Merkmal ein Katalogzitat geliefert hat. Sonst geht er ins Review.
    """
    feats = model.features_for(class_id)
    meta = {f.feature_id: {"desc": f.feature_desc, "type": f.type, "unit_id": f.unit_id,
                           "unit_desc": f.unit_desc, "n_values": len(f.values)} for f in feats}
    g_by_id = {g["feature_id"]: g for g in gemini_feats}
    out = FeatureComparison(supplier_pid=p.supplier_pid, class_id=class_id, feature_meta=meta)

    out.answers["gemini"] = [
        FeatureAnswer(
            feature_id=f.feature_id,
            value=(g := g_by_id.get(f.feature_id, {})).get("value"),
            confidence=float(g.get("confidence") or 0.0),
            source=g.get("source"),
            source_from="gemini" if g.get("source") else "",
            exportable=bool(g.get("value") is not None and g.get("source")),
            reason=g.get("reason"),
        ) for f in feats
    ]

    questions, skipped = _feature_questions(feats, budget=22_000)
    out.jev_skipped = skipped
    if not questions:
        out.answers["jev"] = []
        return out
    try:
        res = jev.ask("features", product_state(p), questions)
    except jev.JevUnavailable:
        raise
    except (jev.JevError, ValueError) as e:
        out.error = str(e)
        out.answers["jev"] = []
        return out

    out.jev_latency_ms = res.latency_ms
    out.jev_cost_usd = res.cost_usd
    out.jev_simulated = res.simulated
    by_id = {f.feature_id: f for f in feats}
    answers: list[FeatureAnswer] = []
    for fid in questions:
        f = by_id[fid]
        g = g_by_id.get(fid, {})
        src = g.get("source")
        if f.type == "L":
            prob = res.noul_of(fid)
            if prob is None:
                continue
            # Eine Noul-Antwort ist eine Wahrscheinlichkeit, kein confidence-Wert.
            # Abstand zur Mitte als Sicherheitsmass: 0.5 heisst "weiss nicht".
            value, conf = ("true" if prob >= 0.5 else "false"), abs(prob - 0.5) * 2
        else:
            choice, conf, _ = res.choice_of(fid)
            value = None if choice in (None, NONE_OPTION) else choice
        answers.append(FeatureAnswer(
            feature_id=fid, value=value, confidence=round(conf, 4),
            source=src if value is not None else None,
            source_from="gemini" if (value is not None and src) else "",
            exportable=bool(value is not None and src),
            reason=None if value is not None else "Jev: keine Option gewaehlt",
        ))
    out.answers["jev"] = answers
    return out


# -------------------------------------------------------------------- Metriken

def _mean(xs: Iterable[float]) -> float | None:
    xs = list(xs)
    return round(sum(xs) / len(xs), 4) if xs else None


def metrics(items: list[ComparisonItem], feats: list[FeatureComparison],
            key: str, label: str, model_id: str = "") -> ModelMetrics:
    """Die Zahlen, an denen sich ein Modell messen lassen muss."""
    ans = [(it, it.answers[key]) for it in items if key in it.answers]
    m = ModelMetrics(model=key, label=label, model_id=model_id, n=len(ans))
    if not ans:
        return m
    m.errors = sum(1 for _, a in ans if a.error)
    m.simulated = any(a.simulated for _, a in ans)
    m.no_class = sum(1 for _, a in ans if not a.class_id and not a.error)
    m.candidates_seen = max((a.candidates_seen for _, a in ans), default=0)
    m.latency_ms_total = sum(a.latency_ms for _, a in ans)
    m.latency_ms_avg = round(m.latency_ms_total / len(ans)) if ans else 0
    m.cost_usd = round(sum(a.cost_usd for _, a in ans), 6)
    m.reasoning_codes = sum(len(a.reasoning_codes) for _, a in ans)
    m.unverified_codes = sum(1 for _, a in ans for c in a.reasoning_codes if not c.known)

    # Trefferquote nur, wenn es eine Referenz gibt. Ohne Referenz keine Prozentzahl.
    ref = [(it, a) for it, a in ans if it.reference_class]
    if ref:
        m.hits = sum(1 for it, a in ref if a.class_id == it.reference_class)
        m.hit_rate = round(m.hits / len(ref), 4)
        m.reference_in_window = sum(
            1 for it, a in ref
            if it.reference_rank is not None and a.candidates_seen and it.reference_rank <= a.candidates_seen)
        m.conf_correct = _mean(a.confidence for it, a in ref if a.class_id == it.reference_class)
        m.conf_wrong = _mean(a.confidence for it, a in ref if a.class_id != it.reference_class)

    # Variantenkonsistenz: gleicher Basisname muss zur gleichen Klasse fuehren.
    groups: dict[str, list[str | None]] = {}
    for it, a in ans:
        groups.setdefault(it.base_name, []).append(a.class_id)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    m.variant_groups = len(multi)
    m.variant_consistent = sum(1 for v in multi.values() if len(set(v)) == 1)
    m.variant_consistency = round(m.variant_consistent / len(multi), 4) if multi else None

    rows = [fc for fc in feats if key in fc.answers and fc.answers[key]]
    if rows:
        m.feat_articles = len(rows)
        m.feat_total = sum(len(fc.answers[key]) for fc in rows)
        m.feat_filled = sum(1 for fc in rows for a in fc.answers[key] if a.value is not None)
        m.feat_exportable = sum(1 for fc in rows for a in fc.answers[key] if a.exportable)
        if key == "jev":
            m.feat_cost_usd = round(sum(fc.jev_cost_usd for fc in rows), 6)
            m.feat_latency_ms_total = sum(fc.jev_latency_ms for fc in rows)
    return m


# ------------------------------------------------------------------------ Lauf

def load_reference(out_dir: Path) -> dict[str, str]:
    """out/<job>/reference.json: {"<artikelnr>": "EC004089", ...}

    Ohne diese Datei gibt es keine Trefferquote — dann zeigt der Vergleich
    Abdeckung, Konsistenz und unbelegte Codes, aber keine Prozentzahl gegen
    eine Wahrheit, die niemand festgelegt hat.
    """
    p = out_dir / "reference.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("reference"), dict):
        data = data["reference"]
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def reference_skeleton(out_dir: Path) -> Path:
    """Geruest fuer reference.json aus products.json erzeugen.

    Nimmt David das Abtippen der Artikelnummern ab, aber **nicht** die
    Entscheidung: die Klassenfelder bleiben leer. Es waere verlockend, hier
    die Modellvorschlaege einzutragen — dann misst man das Modell aber gegen
    sich selbst, und die Trefferquote waere wertlos. Leere Werte werden von
    load_reference ignoriert, man kann also stueckweise ausfuellen.
    """
    target = out_dir / "reference.json"
    if target.exists():
        raise FileExistsError(
            f"{target} gibt es schon — sie wird nicht ueberschrieben. "
            "Zum Neuanlegen die Datei vorher umbenennen.")
    data = json.loads((out_dir / "products.json").read_text(encoding="utf-8"))
    products = [Product.model_validate(x) for x in data["products"]]
    if not products:
        raise ValueError(f"{out_dir}/products.json enthaelt keinen Artikel.")

    target.write_text(json.dumps({
        "_hinweis": ("Trage je Artikelnummer die richtige ETIM-Klasse ein (EC + 6 Ziffern). "
                     "Leere Felder werden ignoriert — teilweise ausgefuellt ist erlaubt. "
                     "Die Klasse selbst nachschlagen, nicht vom Modell uebernehmen: sonst "
                     "misst der Vergleich das Modell gegen seine eigene Antwort."),
        "_artikel": {p.supplier_pid: p.name for p in products},
        "reference": {p.supplier_pid: "" for p in products},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reference: {len(products)} Artikel → {target}")
    print("  Jetzt die Klassen eintragen, dann: python -m etim compare --job "
          f"{out_dir.name} --reuse-gemini")
    return target


def _retrieve(model: EtimModel, products: list[Product], k: int,
              reference: dict[str, str]) -> tuple[list[list[ClassCandidate]], list[int | None]]:
    """Top-k je Artikel plus den echten Rang der Referenzklasse in der Gesamtliste.

    Der Rang ist die wichtigste Einzelzahl des Vergleichs: er trennt
    "Retrieval hat sie nicht gefunden" von "das Modell hat sie uebersehen".
    """
    import numpy as np

    ids, emb = classify._class_matrix(model)
    pos = {cid: i for i, cid in enumerate(ids)}
    q = llm.embed([classify.query_text(p) for p in products])
    sims = q @ emb.T
    cands: list[list[ClassCandidate]] = []
    ref_ranks: list[int | None] = []
    for row, p in zip(sims, products):
        order = np.argsort(-row)
        cands.append([ClassCandidate(class_id=ids[i], description=model.class_desc(ids[i]),
                                     score=float(row[i])) for i in order[:k]])
        ref = reference.get(p.supplier_pid)
        if ref and ref in pos:
            ref_ranks.append(int(np.where(order == pos[ref])[0][0]) + 1)
        else:
            ref_ranks.append(None)
    return cands, ref_ranks


def _reused_gemini(out_dir: Path, model: EtimModel) -> dict[str, ModelAnswer]:
    """Gemini-Antworten aus classified.json uebernehmen, statt neu zu fragen.

    Der Rang bleibt hier offen: classified.json speichert nur die besten fuenf
    Kandidaten, und ein Rang aus dieser Liste waere eine andere Zahl als die
    aus dem frischen Retrieval. Er wird in run() nachgetragen.
    """
    path = out_dir / "classified.json"
    if not path.exists():
        return {}
    out: dict[str, ModelAnswer] = {}
    for row in json.loads(path.read_text(encoding="utf-8")):
        d = ClassDecision.model_validate(row["decision"])
        ans = ModelAnswer(model="gemini", candidates_seen=config.TOP_K_CLASSES)
        _from_gemini(model, d, ans, {}, {})
        out[row["product"]["supplier_pid"]] = ans
    return out


def run(out_dir: Path, model: EtimModel | None = None, *,
        reuse_gemini: bool = False, with_features: bool = False,
        progress: Callable[[str, int, int, str], None] | None = None) -> Comparison:
    """Beide Modelle ueber products.json laufen lassen und compare.json schreiben."""
    model = model or EtimModel()
    tick = progress or (lambda *_: None)

    data = json.loads((out_dir / "products.json").read_text(encoding="utf-8"))
    products = [Product.model_validate(p) for p in data["products"]]
    reference = load_reference(out_dir)
    jev_status = jev.status()
    notes: list[str] = []

    k = max(config.TOP_K_CLASSES, config.JEV_TOP_K)
    tick("retrieval", 0, len(products), f"Kandidaten fuer {len(products)} Artikel suchen")
    cands, ref_ranks = _retrieve(model, products, k, reference)
    tick("retrieval", len(products), len(products), "Kandidaten gefunden")

    items = [
        ComparisonItem(
            product=p, base_name=base_name(p.name),
            reference_class=reference.get(p.supplier_pid), reference_rank=rr,
            retrieval=c[: config.JEV_TOP_K],
        )
        for p, c, rr in zip(products, cands, ref_ranks)
    ]

    cached = _reused_gemini(out_dir, model) if reuse_gemini else {}
    if reuse_gemini:
        if cached:
            notes.append("Gemini stammt aus dem letzten Lauf (classified.json). "
                         "Laufzeit und Kosten sind darum nur fuer Jev gemessen.")
        else:
            notes.append("Gemini sollte wiederverwendet werden, aber classified.json fehlt — neu gefragt.")

    for i, (it, cand) in enumerate(zip(items, cands), 1):
        tick("gemini", i - 1, len(items), f"Gemini: {it.product.supplier_pid}")
        pid = it.product.supplier_pid
        if pid in cached:
            ans = cached[pid]
            # Rang gegen die frische Retrieval-Liste, nicht gegen die gespeicherten Top-5.
            ranks = {c.class_id: i + 1 for i, c in enumerate(cand[: config.TOP_K_CLASSES])}
            ans.rank_of_choice = ranks.get(ans.class_id or "")
            it.answers["gemini"] = ans
        else:
            it.answers["gemini"] = ask_gemini(model, it.product, cand[: config.TOP_K_CLASSES])
    tick("gemini", len(items), len(items), "Gemini fertig")

    for i, (it, cand) in enumerate(zip(items, cands), 1):
        tick("jev", i - 1, len(items), f"Jev: {it.product.supplier_pid}")
        it.answers["jev"] = ask_jev(model, it.product, cand[: config.JEV_TOP_K])
    tick("jev", len(items), len(items), "Jev fertig")

    feats: list[FeatureComparison] = []
    if with_features:
        enriched_path = out_dir / "enriched.json"
        enriched = json.loads(enriched_path.read_text(encoding="utf-8")) if enriched_path.exists() else []
        g_feats = {e["product"]["supplier_pid"]: e.get("features", []) for e in enriched}
        todo = [it for it in items if (it.answers["gemini"].class_id or it.answers["jev"].class_id)]
        if not enriched:
            notes.append("Merkmalsvergleich ohne enriched.json — Gemini hat fuer diese Artikel "
                         "keine Merkmale geliefert, also auch keine Belege.")
        for i, it in enumerate(todo, 1):
            tick("features", i - 1, len(todo), f"Merkmale: {it.product.supplier_pid}")
            cid = it.answers["jev"].class_id or it.answers["gemini"].class_id
            feats.append(compare_features(model, it.product, cid,
                                          g_feats.get(it.product.supplier_pid, [])))
        tick("features", len(todo), len(todo), "Merkmale fertig")

    if not reference:
        notes.append("Keine reference.json — es wird keine Trefferquote ausgewiesen. "
                     "Stattdessen zaehlen Abdeckung, Variantenkonsistenz und unbelegte Codes.")
    if jev_status.get("simulated"):
        notes.append("ETIM_DRY_RUN=1: Jev-Antworten sind simuliert, nicht gemessen.")

    both = [it for it in items if it.answers.get("gemini") and it.answers.get("jev")]
    agree = sum(1 for it in both if it.answers["gemini"].class_id == it.answers["jev"].class_id)
    comp = Comparison(
        job=out_dir.name,
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        etim_version=versions.label(model.version),
        gemini_model=config.GEMINI_MODEL,
        jev_model=jev_status.get("model", ""),
        jev_status=jev_status,
        has_reference=bool(reference),
        n_reference=sum(1 for it in items if it.reference_class),
        reused_gemini=bool(cached),
        with_features=with_features,
        top_k_gemini=config.TOP_K_CLASSES,
        top_k_jev=config.JEV_TOP_K,
        items=items, features=feats,
        metrics={
            "gemini": metrics(items, feats, "gemini", "Gemini", config.GEMINI_MODEL),
            "jev": metrics(items, feats, "jev", "Jev", jev_status.get("model", "typesafe/jev")),
        },
        agreement={"n": len(both), "same_class": agree,
                   "rate": round(agree / len(both), 4) if both else None},
        notes=notes,
    )
    (out_dir / "compare.json").write_text(
        json.dumps(comp.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    g, j = comp.metrics["gemini"], comp.metrics["jev"]
    print(f"compare: {len(items)} Artikel — ohne Klasse: Gemini {g.no_class}, Jev {j.no_class}"
          + (f" | Treffer: Gemini {g.hits}/{comp.n_reference}, Jev {j.hits}/{comp.n_reference}"
             if comp.has_reference else "")
          + f" → {out_dir / 'compare.json'}")
    return comp
