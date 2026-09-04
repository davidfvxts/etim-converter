import sys

from .cli import main

try:
    sys.exit(main())
except RuntimeError as e:
    # llm.py meldet fehlenden Key / gescheiterte LLM-Aufrufe als RuntimeError —
    # das ist ein Bedienfehler, kein Absturz, also ohne Traceback ausgeben.
    print(f"Abbruch: {e}", file=sys.stderr)
    sys.exit(2)
