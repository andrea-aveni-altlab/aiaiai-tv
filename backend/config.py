import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
DATA_DIR      = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DB_PATH       = Path(os.getenv("DB_PATH",  DATA_DIR / "tv.duckdb"))
AUDITEL_DIR   = DATA_DIR / "auditel"
PROGRAMMI_DIR = DATA_DIR / "programmi"

# ── DataSource ───────────────────────────────────────────────────────────────
DATA_SOURCE = os.getenv("DATA_SOURCE", "local")
S3_BUCKET   = os.getenv("S3_BUCKET", "")
S3_PREFIX   = os.getenv("S3_PREFIX", "auditel/")

# ── API ──────────────────────────────────────────────────────────────────────
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "changeme")
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL   = "claude-sonnet-4-6"

# ── Mapping TV label → codice Auditel ────────────────────────────────────────
TV_TO_CODE = {
    "RAI1": "0001", "RAI2": "0002", "RAI3": "0003",
    "CAN5": "0004", "IT1":  "0005", "R4":   "0006", "TMC": "0013",
}
CODE_TO_TV = {v: k for k, v in TV_TO_CODE.items()}

TV_LABELS = {
    "0001": "RAI 1", "0002": "RAI 2", "0003": "RAI 3",
    "0004": "Canale 5", "0005": "Italia 1", "0006": "Rete 4", "0013": "LA7",
}

# ── Classificazione ascolto ───────────────────────────────────────────────────
CLASSIFICAZIONI_AUDITEL = {1, 2, 3}
CODICE_NON_RICONOSCIUTO = "9994"

# ── Piattaforme ──────────────────────────────────────────────────────────────
PIATTAFORME = {2: "DTT", 4: "Satellite", 7: "IPTV", 8: "Non definita", 9: "Totale"}

# ── Fasce orarie ─────────────────────────────────────────────────────────────
FASCE = {
    "notte":          (0*3600,   6*3600),
    "mattina":        (6*3600,  12*3600),
    "pomeriggio":    (12*3600,  18*3600),
    "access":        (18*3600,  20*3600),
    "prime_time":    (20*3600,  23*3600),
    "seconda_serata":(23*3600,  24*3600),
}
