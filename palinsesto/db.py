"""Connessione e init del DB palinsesto (data/palinsesto.duckdb)."""
import os
from pathlib import Path
import duckdb

BASE = Path(__file__).parent
DB_PATH = Path(os.getenv("PALINSESTO_DB", BASE.parent / "data" / "palinsesto.duckdb"))

# Confini di fascia iniziali (giorno TV, secondi). Calibrabili: quando le griglie
# orarie saranno caricate, i confini si tarano sui dati; questi sono i default.
FASCE_DEFAULT = [
    ("notte",          2 * 3600,  6 * 3600),
    ("mattina",        6 * 3600, 12 * 3600),
    ("day",           12 * 3600, 18 * 3600),
    ("access",        18 * 3600, 20 * 3600 + 1800),
    ("prime",         20 * 3600 + 1800, 23 * 3600 + 900),
    ("seconda_serata", 23 * 3600 + 900, 26 * 3600),
]
# Publitalia: l'access commerciale arriva fino alle 21:25 (la Ruota 20:35 e'
# "Ruota della fortuna ACCESS" nelle sue stesse Stime); i settimanali PT
# (overlay per fascia, senza orario) devono sostituire SOLO il 21:30.
FASCE_PER_CONCESSIONARIA = {
    "publitalia": [
        ("notte",          2 * 3600,  6 * 3600),
        ("mattina",        6 * 3600, 12 * 3600),
        ("day",           12 * 3600, 18 * 3600),
        ("access",        18 * 3600, 21 * 3600 + 1500),
        ("prime",         21 * 3600 + 1500, 23 * 3600 + 900),
        ("seconda_serata", 23 * 3600 + 900, 26 * 3600),
    ],
}
TARGET_SEED = [
    ("individui", "Individui 4+", "popolazione 4+"),
    ("15_64", "15-64", "adulti 15-64"),
    ("25_54", "25-54", "adulti 25-54"),
]
METRICA_SEED = [
    ("amr_migliaia", "migliaia di individui (AMR)"),
    ("amr_individui", "individui (AMR)"),
    ("share_pct", "percentuale share"),
]


def connect(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(p))
    init_schema(conn)
    return conn


def init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute((BASE / "schema.sql").read_text())
    # migrazione additiva per DB creati prima della colonna (cache rigenerabile).
    # ATTENZIONE: niente "ADD COLUMN IF NOT EXISTS" — in DuckDB, se la colonna
    # esiste gia', RIAZZERA i valori al DEFAULT a ogni connect (verificato).
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info('palinsesto_composto')").fetchall()}
    if "lettura_incerta" not in cols:
        conn.execute("ALTER TABLE palinsesto_composto "
                     "ADD COLUMN lettura_incerta BOOLEAN DEFAULT FALSE")
    # fascia_def e' configurazione-nel-codice: refresh completo a ogni init
    conn.execute("DELETE FROM fascia_def")
    for cz in ("rai", "cairo", "publitalia"):
        for f, a, b in FASCE_PER_CONCESSIONARIA.get(cz, FASCE_DEFAULT):
            conn.execute("INSERT INTO fascia_def VALUES (?,?,?,?)", [cz, f, a, b])
    for t in TARGET_SEED:
        conn.execute("INSERT INTO target VALUES (?,?,?) ON CONFLICT DO NOTHING", list(t))
    for m in METRICA_SEED:
        conn.execute("INSERT INTO metrica VALUES (?,?) ON CONFLICT DO NOTHING", list(m))


def fascia_di(conn, concessionaria: str, t_start: int) -> str:
    row = conn.execute(
        "SELECT fascia FROM fascia_def WHERE concessionaria=? AND ? >= t_da AND ? < t_a",
        [concessionaria, t_start, t_start]).fetchone()
    return row[0] if row else "day"
