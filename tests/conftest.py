import json
import os
import re
import shutil
from pathlib import Path

import pytest

os.environ["ETIM_DRY_RUN"] = "1"

from etim import config, jev, llm  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def fake_decide(prompt: str):
    """Wählt die Kandidatenklasse, deren Text am meisten Wörter mit dem Artikel teilt."""
    name = re.search(r"Bezeichnung: (.*)", prompt).group(1).lower()
    cands = re.findall(r"^(EC\d{6}): (.*)$", prompt, flags=re.M)
    best, best_score = None, -1
    for cid, text in cands:
        words = set(re.findall(r"\w+", text.lower()))
        score = len(words & set(re.findall(r"\w+", name)))
        if "gummieinlage" in name and "accessories" in text.lower():
            score += 5
        if "rohrschelle" in name and text.lower().startswith("pipe clamp"):
            score += 3
        if "kugelhahn" in name and "ball valve" in text.lower():
            score += 3
        if score > best_score:
            best, best_score = cid, score
    return {"class_id": best, "confidence": 0.9 if best_score >= 3 else 0.5, "reasoning": "fake", "runner_up": None}


def fake_fill(prompt: str):
    attrs = re.search(r"Katalogattribute \(wörtlich\): (.*)", prompt).group(1)
    feats = re.findall(r"^(EF\d{6}) \| (.*?) \| ([ANLR]) \| (.*?) \| (.*)$", prompt, flags=re.M)
    out = []
    for fid, desc, typ, unit, vals in feats:
        v, src, conf, reason = None, None, 0.0, "nicht im Katalog"
        if desc.startswith("Material") and "Stahl" in attrs and "EV000001=Steel" in vals:
            v, src, conf, reason = "EV000001", "Material=Stahl verzinkt", 0.95, None
        elif desc.startswith("Material") and "EPDM" in attrs and "EV000003=Plastic" in vals:
            v, src, conf, reason = "EV000003", "Material=EPDM", 0.6, None
        elif desc.startswith("Clamping") and (m := re.search(r"Spannbereich=(\d+)-(\d+) mm", attrs)):
            out.append({"feature_id": fid, "value": m.group(1), "value_max": m.group(2), "source": m.group(0), "confidence": 0.95, "reason": None})
            continue
        elif desc.startswith("Thread") and "M8/M10" in attrs:
            v, src, conf, reason = "EV000011", "Gewinde=M8/M10", 0.95, None
        elif desc.startswith("With rubber") and "Gummieinlage=ja" in attrs:
            v, src, conf, reason = "true", "Gummieinlage=ja", 0.9, None
        elif desc.startswith("Nominal") and "DN 20" in prompt:
            v, src, conf, reason = "20", "Kugelhahn DN 20", 0.9, None
        out.append({"feature_id": fid, "value": v, "value_max": None, "source": src, "confidence": conf, "reason": reason})
    return {"features": out}


def _words(x) -> set[str]:
    return set(re.findall(r"\w+", json.dumps(x, ensure_ascii=False).lower()))


def fake_jev(state, questions):
    """Deterministischer Jev-Ersatz: Wortueberlappung statt Modell.

    Bildet die Antwortform exakt nach (choice/probabilities/confidence bzw. noul),
    damit der Auswertungspfad getestet wird — nicht die Modellqualitaet.
    """
    # Der Sprachhinweis steht in jedem Zustand und wuerde jede Wortzaehlung verwaessern.
    if isinstance(state, dict):
        state = {k: v for k, v in state.items() if k != "language_note"}
    sw = _words(state)
    flat = " ".join(sw)
    # Fixture-Brücken Deutsch -> Englisch, wie sie fake_fill fuer Gemini benutzt.
    BRIDGE = [("stahl", "steel"), ("epdm", "plastic"), ("gummieinlage", "rubber"),
              ("kugelhahn", "ball valve"), ("rohrschelle", "pipe clamp")]
    answers = {}
    for key, q in questions.items():
        if q["type"] == "noul":
            crit = (q.get("criteria") or {}).get("true") or q["instructions"]
            hits = len(sw & _words(crit))
            for de, en in BRIDGE:
                if de in flat and en in json.dumps(crit, ensure_ascii=False).lower():
                    hits += 3
            answers[key] = {"type": "noul", "noul": round(min(0.95, 0.05 + 0.12 * hits), 2)}
            continue
        scores = {}
        for opt, desc in q["criteria"].items():
            score = len(sw & _words(desc)) if desc is not None else 0
            text = json.dumps(desc, ensure_ascii=False).lower()
            for de, en in BRIDGE:
                if de in flat and en in text:
                    score += 3
            # Dieselben Fixture-Heuristiken wie fake_decide, damit beide Modelle
            # im Test auf derselben Grundlage entscheiden.
            if "gummieinlage" in flat and "accessories" in text:
                score += 5
            scores[opt] = score
        # "keine passt" gewinnt nur, wenn wirklich nichts anderes punktet.
        if scores.get("none", 0) and max((v for k, v in scores.items() if k != "none"), default=0):
            scores["none"] = 0
        total = sum(scores.values()) or 1
        probs = {k: round(v / total, 4) for k, v in scores.items()}
        best = max(scores, key=lambda k: scores[k])
        answers[key] = {"type": "choice", "choice": best,
                        "confidence": round(probs[best], 4), "probabilities": probs}
    return answers


@pytest.fixture(scope="session", autouse=True)
def fakes(tmp_path_factory):
    llm.register_fake("decide", fake_decide)
    llm.register_fake("fill", fake_fill)
    jev.register_fake("classify", fake_jev)
    jev.register_fake("features", fake_jev)
    cache = tmp_path_factory.mktemp("cache")
    config.CACHE = cache
    config.OUT = tmp_path_factory.mktemp("out")
    yield
    shutil.rmtree(cache, ignore_errors=True)


@pytest.fixture(scope="session")
def model():
    from etim.model import EtimModel, build_sqlite

    build_sqlite(FIX / "etim_mini", config.CACHE / "etim.sqlite")
    return EtimModel(config.CACHE / "etim.sqlite")
