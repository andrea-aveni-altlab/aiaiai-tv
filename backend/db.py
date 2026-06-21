import duckdb
import threading
from pathlib import Path
from config import DB_PATH

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None


def get_conn() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                _conn = duckdb.connect(str(DB_PATH))
                _init_schema(_conn)
    return _conn


def _init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS individui (
            data DATE, panel VARCHAR, prg INTEGER, fat_exp DOUBLE,
            city_size TINYINT, cse TINYINT, bambini_03 TINYINT,
            ragazzi_414 TINYINT, studi TINYINT, sesso TINYINT,
            eta TINYINT, resp_acquisto TINYINT, anno_nascita SMALLINT,
            ra_bambini_814 TINYINT, nuove_classi_eta TINYINT,
            regione TINYINT, sesso4 TINYINT, attivita TINYINT,
            broadband TINYINT, tv_connessa TINYINT, tipo_meter TINYINT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS statements (
            data DATE, panel VARCHAR, prg INTEGER, tipo_stmt VARCHAR,
            cod_emit VARCHAR, t_start INTEGER, t_end INTEGER,
            piattaforma TINYINT, classificazione TINYINT, dig_vod TINYINT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS programmi (
            data DATE, cod_emit VARCHAR, tv VARCHAR, programma VARCHAR,
            t_start INTEGER, t_end INTEGER, durata_sec INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS emittenti (
            cod_emit VARCHAR PRIMARY KEY, nome VARCHAR, tipo CHAR(1), network VARCHAR
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audience_cache (
            data DATE, cod_emit VARCHAR, tv VARCHAR, programma VARCHAR,
            t_start INTEGER, t_end INTEGER, durata_min INTEGER,
            target_id VARCHAR, audience DOUBLE, share_auditel DOUBLE,
            share_reale DOUBLE, copertura DOUBLE,
            PRIMARY KEY (data, cod_emit, programma, t_start, target_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingest_log (
            data DATE PRIMARY KEY, ingested_at TIMESTAMP,
            stmt_count INTEGER, ind_count INTEGER, prog_count INTEGER,
            status VARCHAR, note VARCHAR
        )
    """)
    _maybe_create_index(conn, "idx_stmt_data_emit", "statements(data, cod_emit)")
    _maybe_create_index(conn, "idx_ind_data",       "individui(data, panel, prg)")
    _maybe_create_index(conn, "idx_prog_data",      "programmi(data, cod_emit)")
    _maybe_create_index(conn, "idx_cache_data",     "audience_cache(data, target_id)")


def _maybe_create_index(conn, name: str, definition: str) -> None:
    exists = conn.execute(
        "SELECT COUNT(*) FROM duckdb_indexes() WHERE index_name = ?", [name]
    ).fetchone()[0]
    if not exists:
        conn.execute(f"CREATE INDEX {name} ON {definition}")


def available_dates() -> list[str]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT data::VARCHAR FROM ingest_log
        WHERE status = 'ok' ORDER BY data DESC
    """).fetchall()
    return [r[0] for r in rows]


def last_ingested_date() -> str | None:
    dates = available_dates()
    return dates[0] if dates else None
