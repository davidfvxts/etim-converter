import os
import re
import shutil
from pathlib import Path

import pytest

os.environ["ETIM_DRY_RUN"] = "1"

from etim import config, llm  # noqa: E402

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


@pytest.fixture(scope="session", autouse=True)
def fakes(tmp_path_factory):
    llm.register_fake("decide", fake_decide)
    llm.register_fake("fill", fake_fill)
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
