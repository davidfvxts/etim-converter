#!/usr/bin/env python3
"""Misst, wie zuverlaessig das Embedding-Retrieval die richtige ETIM-Klasse findet.

Warum das die wichtigste Messung der Pipeline ist: `classify` legt dem LLM nur die
Top-K Klassen vor (config.TOP_K_CLASSES). Ist die richtige Klasse nicht dabei, kann
kein nachgelagertes Modell das mehr reparieren — Recall@K ist die Obergrenze fuer
die Trefferquote der gesamten Pipeline.

Gemessen wird der echte Pfad: dieselbe `classify.query_text`, dieselbe Klassenmatrix
aus `classify._class_matrix`. Das Testset liegt in tests/fixtures/retrieval_eval.json
und enthaelt jeden Artikel deutsch und englisch — Kundenkataloge sind deutsch, die
ETIM-Klassentexte englisch, und dieser Sprachsprung ist der eigentliche Verdaechtige.

    python scripts/eval_retrieval.py            # beide Sprachen
    python scripts/eval_retrieval.py --lang de  # nur deutsch
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from etim import config, llm  # noqa: E402
from etim.classify import _class_matrix, query_text  # noqa: E402
from etim.model import EtimModel  # noqa: E402
from etim.schemas import Product, RawAttribute  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "retrieval_eval.json"
KS = (1, 3, 5, 10, 20, 50)


def to_product(block: dict) -> Product:
    return Product(
        supplier_pid="EVAL",
        name=block["name"],
        description=block["description"],
        attributes=[RawAttribute(name=n, value=v) for n, v in block["attributes"]],
    )


def rank_of(row: np.ndarray, ids: list[str], wanted: set[str]) -> int | None:
    """1-basierter Rang der besten akzeptierten Klasse, None wenn nicht gefunden."""
    order = np.argsort(-row)
    for pos, i in enumerate(order, start=1):
        if ids[i] in wanted:
            return pos
    return None


def evaluate(lang: str, items: list[dict], ids: list[str], emb: np.ndarray, model: EtimModel) -> dict:
    products = [to_product(it[lang]) for it in items]
    q = llm.embed([query_text(p) for p in products])
    sims = q @ emb.T
    rows = []
    for it, p, row in zip(items, products, sims):
        strict = rank_of(row, ids, {it["expected"]})
        lenient = rank_of(row, ids, {it["expected"], *it.get("also_ok", [])})
        top = np.argsort(-row)[:3]
        rows.append(
            {
                "name": p.name,
                "sector": it["sector"],
                "expected": it["expected"],
                "expected_desc": model.class_desc(it["expected"]),
                "rank": strict,
                "rank_lenient": lenient,
                "top3": [(ids[i], model.class_desc(ids[i]), round(float(row[i]), 3)) for i in top],
            }
        )
    return {"lang": lang, "rows": rows}


def recall(rows: list[dict], k: int, key: str = "rank") -> float:
    hit = sum(1 for r in rows if r[key] is not None and r[key] <= k)
    return hit / len(rows)


def mrr(rows: list[dict], key: str = "rank") -> float:
    return sum(1.0 / r[key] for r in rows if r[key]) / len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["de", "en", "both"], default="both")
    ap.add_argument("--verbose", action="store_true", help="jede Zeile einzeln ausgeben")
    a = ap.parse_args()

    data = json.loads(FIXTURE.read_text())
    items = data["items"]
    model = EtimModel()
    ids, emb = _class_matrix(model)
    print(f"Klassen: {len(ids)}  Matrix: {emb.shape}  Embed-Modell: {config.GEMINI_EMBED_MODEL}")
    print(f"Testset: {len(items)} Artikel  TOP_K der Pipeline: {config.TOP_K_CLASSES}\n")

    langs = ["de", "en"] if a.lang == "both" else [a.lang]
    results = [evaluate(lang, items, ids, emb, model) for lang in langs]

    head = "Sprache | " + " | ".join(f"R@{k}".rjust(6) for k in KS) + " |    MRR"
    print(head)
    print("-" * len(head))
    for res in results:
        r = res["rows"]
        print(
            f"{res['lang']:>7} | "
            + " | ".join(f"{recall(r, k):6.0%}" for k in KS)
            + f" | {mrr(r):6.2f}"
        )
    print("\n(weich: Klassen aus 'also_ok' zaehlen als Treffer)")
    for res in results:
        r = res["rows"]
        print(
            f"{res['lang']:>7} | "
            + " | ".join(f"{recall(r, k, 'rank_lenient'):6.0%}" for k in KS)
            + f" | {mrr(r, 'rank_lenient'):6.2f}"
        )

    for res in results:
        misses = [r for r in res["rows"] if r["rank"] is None or r["rank"] > config.TOP_K_CLASSES]
        print(f"\n— {res['lang'].upper()}: ausserhalb der Top-{config.TOP_K_CLASSES} "
              f"({len(misses)} von {len(res['rows'])}) —")
        for r in misses:
            print(f"  {r['name']}  → erwartet {r['expected']} ({r['expected_desc']}), Rang {r['rank']}")
            for cid, desc, s in r["top3"]:
                print(f"      statt: {cid} {desc} ({s})")
        if a.verbose:
            print(f"\n— {res['lang'].upper()}: alle Raenge —")
            for r in sorted(res["rows"], key=lambda x: (x["rank"] is None, x["rank"] or 0)):
                print(f"  Rang {str(r['rank']):>5}  {r['name']}  → {r['expected']} {r['expected_desc']}")

    worst = max((r["rank"] or 10**6) for res in results for r in res["rows"])
    print(f"\nSchlechtester Rang ueber alle Laeufe: {worst if worst < 10**6 else 'nicht gefunden'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
