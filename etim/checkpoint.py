"""Zwischenstand eines Laufs festhalten, damit ein Abbruch nichts kostet.

Ein Lauf ueber 250 Artikel dauert ueber eine Stunde und kostet Geld. Bisher
schrieben `classify` und `compare` ihr Ergebnis erst ganz am Ende — wer abbrach
(oder abbrechen musste, weil Jev ausfiel), verlor alles, auch die bereits
bezahlten Gemini-Antworten.

Hier liegt eine Datei je Lauf: `out/<job>/.checkpoint.<art>.json`. Sie haelt die
fertigen Artikel und die Bedingungen, unter denen sie entstanden sind (Modell,
ETIM-Version). Aendert sich eine Bedingung, ist der Zwischenstand wertlos und
wird verworfen — sonst mischte ein Lauf Antworten aus zwei Einstellungen.

Nach einem erfolgreichen Lauf wird die Datei geloescht: sie ist Arbeitsmaterial,
kein Ergebnis.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# So oft wird auf die Platte geschrieben. Jeder Artikel waere unnoetig teuer,
# 25 sind bei ~25 s/Artikel gut zehn Minuten Arbeit — mehr will niemand verlieren.
EVERY = int(os.getenv("ETIM_CHECKPOINT_EVERY", "5"))


def path(out_dir: Path, kind: str) -> Path:
    return out_dir / f".checkpoint.{kind}.json"


class Checkpoint:
    """Fertige Artikel eines Laufs, nach Artikelnummer."""

    def __init__(self, out_dir: Path, kind: str, conditions: dict[str, Any]):
        self.file = path(out_dir, kind)
        self.conditions = conditions
        self.done: dict[str, dict] = {}
        self._unsaved = 0

    def load(self) -> int:
        """Vorhandenen Zwischenstand uebernehmen, wenn die Bedingungen passen."""
        if not self.file.exists():
            return 0
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return 0
        if data.get("conditions") != self.conditions:
            # Anderes Modell oder andere ETIM-Version: nicht mischen.
            self.file.unlink(missing_ok=True)
            return 0
        items = data.get("done")
        if not isinstance(items, dict):
            return 0
        self.done = items
        return len(self.done)

    def add(self, key: str, value: dict) -> None:
        self.done[key] = value
        self._unsaved += 1
        if self._unsaved >= EVERY:
            self.save()

    def save(self) -> None:
        """Atomar schreiben: ein Abbruch mitten im Schreiben darf nichts zerstoeren."""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.file.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"conditions": self.conditions, "done": self.done},
                          fh, ensure_ascii=False)
            os.replace(tmp, self.file)
            self._unsaved = 0
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self.file.unlink(missing_ok=True)
        self.done.clear()
        self._unsaved = 0
