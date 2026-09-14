"""Einziger Ort, an dem Gemini aufgerufen wird.

- generate_json(prompt, schema, files=...) -> validierte Pydantic-Instanz
- embed(texts) -> np.ndarray (n, d), L2-normalisiert
- ETIM_DRY_RUN=1 liefert deterministische Fakes (Tests, Demo ohne Key)
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from typing import Any, Sequence, Type, TypeVar

import numpy as np
from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)

_client = None
_fake_handlers: dict[str, Any] = {}


def register_fake(name: str, fn) -> None:
    """Tests registrieren hier Fake-Antworten je Aufruf-Name."""
    _fake_handlers[name] = fn


def _get_client():
    global _client
    if _client is None:
        from google import genai

        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY fehlt (.env) — oder ETIM_DRY_RUN=1 setzen")
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


# Fehler, die von selbst weggehen (Kapazität, Netz) — nur die sind einen Retry wert.
# Alles andere (falsches Modell, ungültiges Schema, Key abgelaufen) wiederholt sich
# identisch und soll sofort sichtbar werden, statt hinter Backoff zu verschwinden.
_TRANSIENT_CODES = (429, 500, 502, 503, 504)
_TRANSIENT_WORDS = ("unavailable", "resource_exhausted", "overloaded", "deadline",
                    "timeout", "timed out", "connection", "internal error")


def _status_code(e: Exception) -> int | None:
    for attr in ("code", "status_code"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
    return None


def _is_transient(e: Exception) -> bool:
    code = _status_code(e)
    if code is not None:
        return code in _TRANSIENT_CODES
    msg = str(e).lower()
    if any(str(c) in msg for c in _TRANSIENT_CODES):
        return True
    return any(w in msg for w in _TRANSIENT_WORDS)


def _err_label(e: Exception) -> str:
    code = _status_code(e)
    return f"HTTP {code}" if code else type(e).__name__


def _quota_exhausted(e: Exception) -> str:
    """Erkennt ein aufgebrauchtes Tages-/Projektkontingent (nicht: zu schnell gefeuert).

    Ein 429 heißt zweierlei: "kurz zu viele Anfragen" (Backoff hilft) oder "Kontingent
    für heute weg" (Backoff hilft nie). Nur der erste Fall ist einen Retry wert; der
    zweite braucht eine Antwort, die sagt, was zu tun ist. Rückgabe: Hinweistext oder "".
    """
    if _status_code(e) != 429 and "429" not in str(e):
        return ""
    msg = str(e)
    if "PerDay" in msg or "free_tier" in msg or "FreeTier" in msg:
        m = re.search(r"limit: (\d+), model: ([\w.\-]+)", msg)
        if m:
            return (f"Free-Tier-Kontingent erschöpft: {m.group(1)} Anfragen/Tag für "
                    f"{m.group(2)}. Billing im Google-AI-Studio-Projekt aktivieren "
                    f"oder GEMINI_MODEL auf ein Modell mit freiem Kontingent setzen.")
        return ("Tageskontingent des Projekts erschöpft — Billing aktivieren "
                "oder bis morgen warten.")
    return ""


def _server_retry_delay(e: Exception) -> float | None:
    """Googles eigener Vorschlag, wann es wieder Sinn hat (retryDelay / 'retry in Xs')."""
    m = re.search(r"[Rr]etry(?:Delay|\sin)['\":\s]+(\d+(?:\.\d+)?)s", str(e))
    return float(m.group(1)) if m else None


def generate_json(
    name: str,
    prompt: str,
    schema: Type[T],
    files: Sequence[tuple[bytes, str]] = (),
    temperature: float = 0.1,
    retries: int = 6,
) -> T:
    """Strukturierte Antwort. `files` = [(bytes, mime_type)], z. B. PDF-Ausschnitte."""
    if config.DRY_RUN:
        if name not in _fake_handlers:
            raise RuntimeError(f"DRY_RUN: kein Fake für '{name}' registriert")
        return schema.model_validate(_fake_handlers[name](prompt))

    from google.genai import types

    client = _get_client()
    parts: list[Any] = [types.Part.from_bytes(data=b, mime_type=m) for b, m in files]
    parts.append(prompt)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=temperature,
                ),
            )
            if resp.parsed is not None:
                return resp.parsed  # type: ignore[return-value]
            return schema.model_validate(json.loads(resp.text))
        except Exception as e:  # noqa: BLE001
            last_err = e
            if not _is_transient(e):
                raise RuntimeError(f"LLM-Aufruf '{name}' fehlgeschlagen: {e}") from e
            if hint := _quota_exhausted(e):
                raise RuntimeError(f"LLM-Aufruf '{name}' abgebrochen. {hint}") from e
            if attempt == retries - 1:
                break
            # Kapazitätsfehler halten länger an als ein paar Sekunden; Jitter, damit
            # bei Batch-Läufen nicht alle Aufrufe gleichzeitig wieder anklopfen.
            # Nennt der Server selbst eine Wartezeit, gilt seine.
            delay = _server_retry_delay(e) or min(2 ** attempt, 30) * (1 + random.random())
            print(f"    {name}: {_err_label(e)}, neuer Versuch in {delay:.0f}s "
                  f"({attempt + 1}/{retries - 1})")
            time.sleep(delay)
    raise RuntimeError(f"LLM-Aufruf '{name}' fehlgeschlagen nach {retries} Versuchen: {last_err}")


# Das Embed-Kontingent zaehlt jeden Text als eigenen Request, nicht jeden
# HTTP-Aufruf: 5.640 Klassen laufen sonst nach wenigen Sekunden in 429
# (EmbedContentPerMinutePerProjectPerUserPerModel, Paid Tier: 3.000/min).
# Darum ein Token-Bucket ueber die Texte und Respektieren der Server-Wartezeit.
EMBED_TEXTS_PER_MIN = int(__import__("os").getenv("ETIM_EMBED_TEXTS_PER_MIN", "2500"))
_embed_window: list[float] = []


def _embed_throttle(n: int) -> None:
    now = time.monotonic()
    _embed_window[:] = [t for t in _embed_window if now - t < 60.0]
    if len(_embed_window) + n > EMBED_TEXTS_PER_MIN and _embed_window:
        wait = 60.0 - (now - _embed_window[0]) + 0.5
        if wait > 0:
            print(f"    embed: Rate-Limit-Puffer, warte {wait:.0f}s")
            time.sleep(wait)
            now = time.monotonic()
            _embed_window[:] = [t for t in _embed_window if now - t < 60.0]
    _embed_window.extend([now] * n)


def _embed_chunk(client, chunk: list[str], retries: int = 6):
    """Einen Batch einbetten. Jeder Text braucht ein eigenes Content-Objekt.

    Eine blosse Liste von Strings liest die API als EINEN Content mit mehreren
    Parts und liefert dafuer genau ein Embedding — der Fehler wirft nichts, er
    kommt als Matrix mit Batch-Anzahl statt Text-Anzahl zurueck und verschiebt
    spaeter jede Klassenzuordnung. Die Pruefung steht in embed().
    """
    contents = [{"parts": [{"text": t}]} for t in chunk]
    last = None
    for attempt in range(retries):
        _embed_throttle(len(chunk))
        try:
            return client.models.embed_content(model=config.GEMINI_EMBED_MODEL, contents=contents)
        except Exception as e:  # noqa: BLE001
            last = e
            if _status_code(e) != 429 and not _is_transient(e):
                raise
            if attempt == retries - 1:
                break
            delay = _server_retry_delay(e) or min(2 ** attempt, 30) * (1 + random.random())
            print(f"    embed: {_err_label(e)}, neuer Versuch in {delay:.0f}s ({attempt + 1}/{retries - 1})")
            time.sleep(delay)
            _embed_window.clear()
    raise RuntimeError(f"Embedding fehlgeschlagen nach {retries} Versuchen: {last}")


def embed(texts: Sequence[str], batch: int = 100) -> np.ndarray:
    if config.DRY_RUN:
        # deterministisches Hash-Embedding, reicht für Tests
        vecs = []
        for t in texts:
            rng = np.random.default_rng(int(hashlib.md5(t.lower().encode()).hexdigest()[:8], 16))
            v = rng.standard_normal(64)
            # Wort-Overlap grob abbilden, damit ähnliche Texte ähnliche Vektoren bekommen
            for w in set(t.lower().split()):
                r = np.random.default_rng(int(hashlib.md5(w.encode()).hexdigest()[:8], 16))
                v += r.standard_normal(64)
            vecs.append(v / np.linalg.norm(v))
        return np.vstack(vecs)

    client = _get_client()
    out: list[np.ndarray] = []
    for i in range(0, len(texts), batch):
        chunk = list(texts[i : i + batch])
        resp = _embed_chunk(client, chunk)
        if len(resp.embeddings) != len(chunk):
            raise RuntimeError(
                f"Embedding-API lieferte {len(resp.embeddings)} Vektoren für {len(chunk)} Texte "
                f"(Modell {config.GEMINI_EMBED_MODEL}). Abbruch — stillschweigend weiterzurechnen "
                f"würde jede Klassenzuordnung verschieben."
            )
        out.append(np.array([e.values for e in resp.embeddings], dtype=np.float32))
    m = np.vstack(out)
    if m.shape[0] != len(texts):
        raise RuntimeError(f"Embedding-Matrix hat {m.shape[0]} Zeilen für {len(texts)} Texte.")
    return m / np.linalg.norm(m, axis=1, keepdims=True)
