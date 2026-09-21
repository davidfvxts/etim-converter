import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
OUT = ROOT / "out"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
ETIM_VERSION = os.getenv("ETIM_VERSION", "ETIM-10.0")
REVIEW_THRESHOLD = float(os.getenv("ETIM_REVIEW_THRESHOLD", "0.75"))
# Mindestanteil befüllter Merkmale, damit ein Artikel ohne Review exportiert wird.
# 0 schaltet die Prüfung ab.
MIN_COVERAGE = float(os.getenv("ETIM_MIN_COVERAGE", "0.30"))
DRY_RUN = os.getenv("ETIM_DRY_RUN", "0") == "1"

PAGES_PER_CHUNK = int(os.getenv("ETIM_PAGES_PER_CHUNK", "6"))
TOP_K_CLASSES = int(os.getenv("ETIM_TOP_K", "20"))

# --- Jev (TypeSafe System One) -------------------------------------------------
# Transport: "worker" laeuft ueber den eigenen Cloudflare-Worker (Zugangsdaten
# liegen dort als Secret), "cloudflare" spricht Workers AI direkt an.
JEV_TRANSPORT = os.getenv("ETIM_JEV_TRANSPORT", "worker")
JEV_WORKER_URL = os.getenv("ETIM_JEV_WORKER_URL", "")
JEV_WORKER_SECRET = os.getenv("ETIM_JEV_WORKER_SECRET", "")
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CF_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
JEV_MODEL = os.getenv("ETIM_JEV_MODEL", "typesafe/jev")
JEV_TIMEOUT = int(os.getenv("ETIM_JEV_TIMEOUT", "90"))
# Kandidatenfeld fuer Jev. Jev erlaubt 255 Optionen je Choice; eine Option bleibt
# fuer "keine passt" reserviert.
JEV_TOP_K = int(os.getenv("ETIM_JEV_TOP_K", "254"))

# --- Preise (Stand 21.9.2026, fuer die Kostenspalten im Vergleich) -------------
# Jev: Output kostenlos, nur Input zaehlt.
JEV_INPUT_USD_PER_M = float(os.getenv("ETIM_JEV_INPUT_USD_PER_M", "0.042"))
# Gemini 3.8-flash Einfuehrungspreis; ab 1.1.2027 laut Preisliste 1.50/7.50.
GEMINI_INPUT_USD_PER_M = float(os.getenv("ETIM_GEMINI_INPUT_USD_PER_M", "0.75"))
GEMINI_OUTPUT_USD_PER_M = float(os.getenv("ETIM_GEMINI_OUTPUT_USD_PER_M", "3.75"))

# --- Upload / Studio -----------------------------------------------------------
MAX_UPLOAD_MB = int(os.getenv("ETIM_MAX_UPLOAD_MB", "40"))
