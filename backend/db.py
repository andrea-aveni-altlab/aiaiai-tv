import duckdb
import logging
import threading
from pathlib import Path
from config import DB_PATH

log = logging.getLogger(__name__)

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None
_thread_local = threading.local()   # connessione dedicata per-thread (writer ingest)


def get_conn() -> duckdb.DuckDBPyConnection:
    # Se il thread corrente ha una connessione dedicata (writer ingest) usa
    # quella; altrimenti il singleton condiviso per le letture HTTP.
    local = getattr(_thread_local, "conn", None)
    if local is not None:
        return local
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                _conn = duckdb.connect(str(DB_PATH))
                _init_schema(_conn)
    return _conn


def register_write_conn() -> duckdb.DuckDBPyConnection:
    """
    Apre una connessione dedicata e la registra come thread-local del thread
    corrente. Da qui get_conn() in questo thread restituisce lei, non il
    singleton condiviso: writer di ingest e letture HTTP non condividono lo
    stesso oggetto connessione (non thread-safe). Chiamare unregister a fine
    lavoro. Assume schema gia' creato dal singleton di lettura.
    """
    conn = duckdb.connect(str(DB_PATH))
    _thread_local.conn = conn
    return conn


def unregister_write_conn() -> None:
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        conn.close()
        _thread_local.conn = None


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
        CREATE TABLE IF NOT EXISTS ingest_log (
            data DATE PRIMARY KEY, ingested_at TIMESTAMP,
            stmt_count INTEGER, ind_count INTEGER, prog_count INTEGER,
            status VARCHAR, note VARCHAR
        )
    """)
    _create_audience_cache(conn)
    _maybe_create_index(conn, "idx_stmt_data_emit", "statements(data, cod_emit)")
    _maybe_create_index(conn, "idx_ind_data",       "individui(data, panel, prg)")
    _maybe_create_index(conn, "idx_prog_data",      "programmi(data, cod_emit)")


def _create_audience_cache(conn: duckdb.DuckDBPyConnection) -> None:
    """Schema a celle atomiche: grana = evento × cella. Memorizza gli ingredienti
    grezzi (num_audience, den_auditel, den_reale, copertura), mai la share divisa;
    identificatori partizione/blocco/cella per la ricomposizione a query-time."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audience_cache (
            data DATE, cod_emit VARCHAR, tv VARCHAR, programma VARCHAR,
            t_start INTEGER, t_end INTEGER, durata_min INTEGER,
            partizione VARCHAR, block VARCHAR, cell_id VARCHAR,
            age_class TINYINT, sesso TINYINT, ra TINYINT, cse_level TINYINT,
            num_audience DOUBLE, den_auditel DOUBLE, den_reale DOUBLE, copertura DOUBLE,
            PRIMARY KEY (data, cod_emit, programma, t_start, cell_id)
        )
    """)
    # L'indice cella si crea solo se la tabella ha il nuovo schema (colonna
    # 'block'): su un DB con la tabella vecchia ancora presente lo si salta,
    # cosi' _init_schema non fallisce prima che migrate_schema faccia il DROP.
    has_block = conn.execute(
        "SELECT COUNT(*) FROM duckdb_columns() "
        "WHERE table_name = 'audience_cache' AND column_name = 'block'"
    ).fetchone()[0]
    if has_block:
        _maybe_create_index(conn, "idx_cache_data", "audience_cache(data, block)")
        _maybe_create_index(conn, "idx_cache_emit", "audience_cache(data, cod_emit)")


def migrate_schema() -> None:
    """Migrazione una-tantum al modello a celle. Idempotente: se rileva lo schema
    vecchio (colonna target_id) fa DROP audience_cache + CREATE nuovo + svuota
    ingest_log (cosi' /api/dates riflette la cache vuota fino al reingest full);
    sui restart successivi non fa nulla. Da chiamare all'avvio dell'app, PRIMA di
    accettare ingest — non lazy dentro get_conn."""
    conn = get_conn()   # crea il singleton + _init_schema (tabelle nuove se assenti)
    is_old = conn.execute(
        "SELECT COUNT(*) FROM duckdb_columns() "
        "WHERE table_name = 'audience_cache' AND column_name = 'target_id'"
    ).fetchone()[0]
    if is_old:
        log.warning("Migrazione audience_cache: schema vecchio (target_id) rilevato "
                    "→ DROP + CREATE celle + svuoto ingest_log")
        conn.execute("DROP TABLE audience_cache")
        conn.execute("DELETE FROM ingest_log")
        _create_audience_cache(conn)
        conn.execute("CHECKPOINT")


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
