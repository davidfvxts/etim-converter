import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "cache"
OUT = ROOT / "out"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
ETIM_VERSION = os.getenv("ETIM_VERSION", "ETIM-10.0")
REVIEW_THRESHOLD = float(os.getenv("ETIM_REVIEW_THRESHOLD", "0.75"))
DRY_RUN = os.getenv("ETIM_DRY_RUN", "0") == "1"

PAGES_PER_CHUNK = int(os.getenv("ETIM_PAGES_PER_CHUNK", "6"))
TOP_K_CLASSES = int(os.getenv("ETIM_TOP_K", "20"))
