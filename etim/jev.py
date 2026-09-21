"""Einziger Ort, an dem Jev (TypeSafe System One) aufgerufen wird.

Jev beantwortet getypte Fragen gegen einen Zustand und gibt kalibrierte
Wahrscheinlichkeiten zurueck. Er erzeugt keinen Text: was nicht als Option
vorgegeben ist, kann nicht herauskommen — und auch nicht erfunden werden.

Transportwege (ETIM_JEV_TRANSPORT):
  worker      POST an den eigenen Cloudflare-Worker, Shared Secret im Header.
              Zugangsdaten liegen dann als Worker-Secret, nicht in dieser App.
  cloudflare  direkt an Workers AI (api.cloudflare.com), Account-ID + API-Token.

DRY_RUN liefert Fakes. Jede Antwort traegt dann simulated=True — das Feld wird
bis ins Dashboard durchgereicht, damit nie eine Simulation wie eine Messung aussieht.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config

# Jev-Grenzen (Cloudflare-Modellkarte typesafe/jev, Stand 21.9.2026)
MAX_CHOICE_OPTIONS = 255
MAX_SCORE_LEVELS = 10
CONTEXT_TOKENS = 32_000

# Ausweichoption des Selbsttests. compare.py bringt fuer die Klassenwahl eine eigene mit.
NONE_OPTION_SELFTEST = "neither"

_fake_handlers: dict[str, Callable[[dict, dict], dict]] = {}


def register_fake(name: str, fn) -> None:
    """Tests registrieren hier Fake-Antworten je Aufruf-Name."""
    _fake_handlers[name] = fn


# ----------------------------------------------------------------- Fragetypen

def choice(instructions: Any, criteria: dict[str, Any]) -> dict:
    """Eine Option aus einer festen Menge. criteria: Option -> Beschreibung."""
    if not criteria:
        raise ValueError("Choice ohne Optionen")
    if len(criteria) > MAX_CHOICE_OPTIONS:
        raise ValueError(
            f"Choice mit {len(criteria)} Optionen, Jev erlaubt {MAX_CHOICE_OPTIONS}. "
            "Kandidatenfeld vorher kuerzen."
        )
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def noul(instructions: Any, true_desc: Any = None, false_desc: Any = None) -> dict:
    """Ja/Nein. Die Antwort ist die Wahrscheinlichkeit fuer ja (kein confidence-Feld)."""
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true_desc is not None or false_desc is not None:
        q["criteria"] = {"true": true_desc, "false": false_desc}
    return q


def score(instructions: Any, levels: list[Any]) -> dict:
    """Position auf einer geordneten Skala (2..10 Stufen)."""
    if not 2 <= len(levels) <= MAX_SCORE_LEVELS:
        raise ValueError(f"Score braucht 2..{MAX_SCORE_LEVELS} Stufen, bekam {len(levels)}")
    return {"type": "score", "instructions": instructions, "criteria": levels}


# ------------------------------------------------------------------- Ergebnis

@dataclass
class JevResult:
    answers: dict[str, dict]
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    latency_ms: int = 0
    simulated: bool = False

    @property
    def cost_usd(self) -> float:
        """Output ist bei Jev kostenlos; nur Input-Tokens zaehlen."""
        return self.usage.get("input_tokens", 0) / 1_000_000 * config.JEV_INPUT_USD_PER_M

    def choice_of(self, key: str) -> tuple[str | None, float, dict[str, float]]:
        a = self.answers.get(key) or {}
        return a.get("choice"), float(a.get("confidence") or 0.0), a.get("probabilities") or {}

    def noul_of(self, key: str) -> float | None:
        a = self.answers.get(key) or {}
        v = a.get("noul")
        return None if v is None else float(v)


class JevError(RuntimeError):
    """Fehler eines Jev-Aufrufs, mit einem Satz, der im Dashboard lesbar ist."""


# ------------------------------------------------------------- Konfigurierung

def configured() -> tuple[bool, str]:
    """(bereit?, Begruendung). Die Oberflaeche zeigt die Begruendung im Klartext."""
    if config.DRY_RUN:
        return True, "Trockenlauf — Jev wird simuliert, die Zahlen sind keine Messung."
    t = config.JEV_TRANSPORT
    if t == "worker":
        if not config.JEV_WORKER_URL:
            return False, "ETIM_JEV_WORKER_URL fehlt (.env) — Worker-Adresse eintragen."
        if not config.JEV_WORKER_SECRET:
            return False, "ETIM_JEV_WORKER_SECRET fehlt (.env) — dasselbe Secret wie im Worker."
        return True, f"Worker {config.JEV_WORKER_URL}"
    if t == "cloudflare":
        if not config.CF_ACCOUNT_ID or not config.CF_API_TOKEN:
            return False, "CLOUDFLARE_ACCOUNT_ID oder CLOUDFLARE_API_TOKEN fehlt (.env)."
        return True, "Cloudflare Workers AI (direkt)"
    return False, f"Unbekannter Transport '{t}' — 'worker' oder 'cloudflare' setzen."


def status() -> dict:
    ok, reason = configured()
    return {
        "ready": ok,
        "reason": reason,
        "transport": "dry-run" if config.DRY_RUN else config.JEV_TRANSPORT,
        "model": config.JEV_MODEL,
        "simulated": config.DRY_RUN,
        "max_options": MAX_CHOICE_OPTIONS,
    }


# -------------------------------------------------------------------- Aufruf

_TRANSIENT_STATUS = (429, 500, 502, 503, 504, 529)


def _post(url: str, body: dict, headers: dict[str, str], timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _redact(text: str) -> str:
    """Ein fremder Fehlertext koennte das mitgeschickte Secret enthalten."""
    for secret in (config.JEV_WORKER_SECRET, config.CF_API_TOKEN):
        if secret and len(secret) >= 8:
            text = text.replace(secret, "<secret>")
    return text


def _explain(status_code: int, detail: str) -> str:
    """Fehlertext ohne Zugangsdaten — er landet im Dashboard und im Log."""
    known = {
        401: "Jev lehnt die Zugangsdaten ab (401). Worker-Secret bzw. API-Token pruefen.",
        403: "Jev-Zugriff verweigert (403). Token-Berechtigung 'Workers AI' pruefen.",
        404: "Jev-Endpunkt nicht gefunden (404). Worker-Adresse bzw. Account-ID pruefen.",
        422: "Jev hat die Anfrage abgelehnt (422) — eine Frage ist fehlerhaft aufgebaut.",
        429: "Jev-Ratenlimit erreicht (429).",
        529: "Jev ist ueberlastet (529).",
    }
    base = known.get(status_code, f"Jev-Aufruf fehlgeschlagen (HTTP {status_code}).")
    return f"{base} {_redact(detail)}".strip() if detail else base


def ask(name: str, state: Any, questions: dict[str, dict], retries: int = 4) -> JevResult:
    """Einen Zustand gegen mehrere Fragen auswerten. Fragen laufen parallel."""
    if not questions:
        raise ValueError("Jev-Aufruf ohne Fragen")

    if config.DRY_RUN:
        if name not in _fake_handlers:
            raise JevError(f"Trockenlauf: kein Jev-Fake fuer '{name}' registriert")
        t0 = time.monotonic()
        answers = _fake_handlers[name](state, questions)
        # Token grob schaetzen, damit Kostenspalten im Trockenlauf plausibel bleiben
        approx = len(json.dumps({"state": state, "questions": questions})) // 4
        return JevResult(
            answers=answers, usage={"input_tokens": approx, "output_tokens": 0},
            model=f"{config.JEV_MODEL} (simuliert)",
            latency_ms=int((time.monotonic() - t0) * 1000), simulated=True,
        )

    ok, reason = configured()
    if not ok:
        raise JevError(reason)

    payload = {"state": state, "questions": questions}
    if config.JEV_TRANSPORT == "worker":
        url = config.JEV_WORKER_URL
        body: dict[str, Any] = payload
        headers = {"Authorization": f"Bearer {config.JEV_WORKER_SECRET}"}
    else:
        url = f"https://api.cloudflare.com/client/v4/accounts/{config.CF_ACCOUNT_ID}/ai/run"
        body = {"model": config.JEV_MODEL, "input": payload}
        headers = {"Authorization": f"Bearer {config.CF_API_TOKEN}"}

    last = ""
    for attempt in range(retries):
        t0 = time.monotonic()
        try:
            raw = _post(url, body, headers, config.JEV_TIMEOUT)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = (e.read().decode("utf-8", "replace") or "")[:300]
            except Exception:  # noqa: BLE001
                pass
            if e.code not in _TRANSIENT_STATUS or attempt == retries - 1:
                raise JevError(_explain(e.code, detail)) from None
            last = _explain(e.code, "")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == retries - 1:
                raise JevError(f"Jev nicht erreichbar: {type(e).__name__}") from None
            last = f"Jev nicht erreichbar ({type(e).__name__})"
        else:
            # Workers AI verpackt die Antwort in "result"; der eigene Worker reicht sie direkt durch.
            data = raw.get("result") if isinstance(raw.get("result"), dict) else raw
            if not isinstance(data, dict) or "answers" not in data:
                raise JevError(f"Jev-Antwort ohne 'answers': {str(raw)[:200]}")
            return JevResult(
                answers=data.get("answers") or {},
                usage=data.get("usage") or {},
                model=data.get("model") or config.JEV_MODEL,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        delay = min(2 ** attempt, 20) * (1 + random.random())
        print(f"    jev/{name}: {last}, neuer Versuch in {delay:.0f}s ({attempt + 1}/{retries - 1})")
        time.sleep(delay)
    raise JevError(last or "Jev-Aufruf fehlgeschlagen")


def selftest() -> dict:
    """Einen echten Mini-Aufruf machen und sagen, was dabei herauskam.

    Zwei Optionen, eine triviale Frage — kostet einen Bruchteil eines Cents und
    beantwortet die einzige Frage, die vor einem Lauf zaehlt: kommt eine Antwort
    von Jev zurueck, ja oder nein.
    """
    st = status()
    ok, reason = configured()
    if not ok:
        return {**st, "ok": False, "error": reason}
    if config.DRY_RUN and "selftest" not in _fake_handlers:
        # Im Trockenlauf soll der Befehl zeigen, dass die Kette steht — nicht an
        # einem fehlenden Fake scheitern. Die Antwort ist als simuliert markiert.
        register_fake("selftest", lambda state, questions: {
            "kind": {"type": "choice", "choice": "valve", "confidence": 1.0,
                     "probabilities": {"valve": 1.0, "cable": 0.0, NONE_OPTION_SELFTEST: 0.0}},
        })
    try:
        res = ask("selftest", {"item": "A ball valve DN 20 for drinking water"}, {
            "kind": choice(
                "Which of these describes the item?",
                {"valve": "A valve of any kind", "cable": "An electrical cable",
                 NONE_OPTION_SELFTEST: "Neither of the above"},
            ),
        })
    except JevError as e:
        return {**st, "ok": False, "error": str(e)}
    pick, conf, probs = res.choice_of("kind")
    return {
        **st, "ok": True, "model": res.model, "choice": pick,
        "confidence": conf, "probabilities": probs,
        "latency_ms": res.latency_ms, "usage": res.usage,
        "cost_usd": res.cost_usd, "simulated": res.simulated,
    }
