import tarfile
import tempfile
import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path

import duckdb

from config import (
    AUDITEL_DIR, PROGRAMMI_DIR, DATA_SOURCE,
    S3_BUCKET, S3_PREFIX,
    TV_TO_CODE, CODE_TO_TV, TV_LABELS,
    CLASSIFICAZIONI_AUDITEL, CODICE_NON_RICONOSCIUTO,
)
from db import get_conn
from targets import TARGETS

log = logging.getLogger(__name__)


# ── DataSource ────────────────────────────────────────────────────────────────

class DataSource(ABC):
    @abstractmethod
    def list_auditel_files(self) -> list[tuple[date, Path]]: pass
    @abstractmethod
    def list_programmi_files(self) -> list[tuple[date, Path]]: pass


class LocalSource(DataSource):
    def list_auditel_files(self) -> list[tuple[date, Path]]:
        result = []
        seen_dates = set()
        for pattern in ["*.tar.gz", "*_tar.gz"]:
            for p in sorted(AUDITEL_DIR.glob(pattern)):
                d = _parse_date_from_filename(p.name)
                if d and d not in seen_dates:
                    result.append((d, p))
                    seen_dates.add(d)
        return sorted(result)

    def list_programmi_files(self) -> list[tuple[date, Path]]:
        result = []
        for p in sorted(PROGRAMMI_DIR.glob("*.xlsx")):
            d = _parse_date_from_filename(p.name)
            if d:
                result.append((d, p))
        master = PROGRAMMI_DIR / "programmi_master.xlsx"
        if not result and master.exists():
            result.append((date(1970, 1, 1), master))
        return result


class S3Source(DataSource):
    def __init__(self):
        try:
            import boto3
            self._s3 = boto3.client("s3")
        except ImportError:
            raise RuntimeError("boto3 non installato: pip install boto3")
        self._tmp = Path(tempfile.mkdtemp(prefix="aiaiai_s3_"))

    def list_auditel_files(self):
        return self._list_and_download(S3_PREFIX + "auditel/", ".tar.gz")

    def list_programmi_files(self):
        return self._list_and_download(S3_PREFIX + "programmi/", ".xlsx")

    def _list_and_download(self, prefix, suffix):
        paginator = self._s3.get_paginator("list_objects_v2")
        result = []
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(suffix): continue
                fname = Path(key).name
                d = _parse_date_from_filename(fname)
                if not d: continue
                local = self._tmp / fname
                if not local.exists():
                    self._s3.download_file(S3_BUCKET, key, str(local))
                result.append((d, local))
        return sorted(result)


def get_source() -> DataSource:
    if DATA_SOURCE == "s3":
        return S3Source()
    return LocalSource()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date_from_filename(name: str) -> date | None:
    import re
    m = re.search(r'(\d{4})(\d{2})(\d{2})', name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _hhmm_to_sec(s: str) -> int:
    s = str(s).zfill(4)
    return int(s[:2]) * 3600 + int(s[2:]) * 60


def _timestr_to_sec(s: str) -> int:
    parts = str(s).split(".")
    if len(parts) >= 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 3600 + int(parts[1]) * 60
    return 0


# ── Parsing stmtastd ──────────────────────────────────────────────────────────

def _parse_statements(path: Path, target_date: date) -> list[tuple]:
    rows = []
    target_str = target_date.strftime("%Y-%m-%d")
    with open(path, encoding="latin-1", errors="replace") as f:
        for line in f:
            fields = line.rstrip("\r\n").split("|")
            if len(fields) < 21: continue
            tipo_stmt  = fields[0]
            data_live  = fields[1]
            panel      = fields[2]
            tipo_ppl   = fields[3]
            prg        = fields[4]
            cod_emit   = fields[6]
            ora_ini    = fields[7]
            durata_min = fields[8]
            piattaf    = fields[10]
            classif    = fields[18]
            dig_vod    = fields[20]
            if data_live != target_str: continue
            if tipo_stmt not in ("L", "V"): continue
            if tipo_ppl != "I": continue
            if dig_vod == "1": continue
            try:
                t_start = _hhmm_to_sec(ora_ini)
                dur_sec = int(durata_min) * 60
                if dur_sec <= 0: continue
                rows.append((
                    target_date, panel, int(prg), tipo_stmt, cod_emit,
                    t_start, t_start + dur_sec,
                    int(piattaf) if piattaf.isdigit() else 9,
                    int(classif) if classif.isdigit() else 0,
                    int(dig_vod),
                ))
            except (ValueError, IndexError):
                continue
    return rows


# ── Parsing fianag ────────────────────────────────────────────────────────────

def _parse_individui(path: Path, target_date: date) -> list[tuple]:
    rows = []
    target_str = target_date.strftime("%Y-%m-%d")
    with open(path, encoding="latin-1", errors="replace") as f:
        for line in f:
            fields = line.rstrip("\r\n").split("|")
            if len(fields) < 50: continue
            if fields[0] != target_str: continue
            if fields[2] != "I": continue
            try:
                def fi(i): return int(fields[i]) if i < len(fields) and fields[i].strip() else 0
                rows.append((
                    target_date, fields[1], fi(3), float(fields[4]),
                    fi(5), fi(45), fi(7), fi(8), fi(9), fi(10), fi(11),
                    fi(12), fi(16), fi(15), fi(36), fi(37), fi(39),
                    fi(29), fi(46), fi(49), fi(42),
                ))
            except (ValueError, IndexError):
                continue
    return rows


# ── Parsing programmi Excel ───────────────────────────────────────────────────

def _parse_programmi(path: Path, target_date: date) -> list[tuple]:
    import openpyxl
    from datetime import datetime as dt
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None: continue
        cell_date = row[0]
        if isinstance(cell_date, dt):
            row_date = cell_date.date()
        elif isinstance(cell_date, date):
            row_date = cell_date
        else:
            continue
        if row_date != target_date: continue
        tv   = str(row[3]).strip() if row[3] else ""
        prog = str(row[4]).strip() if row[4] else ""
        if tv not in TV_TO_CODE or not prog: continue
        t_s = _timestr_to_sec(row[1]) if row[1] else 0
        t_e = _timestr_to_sec(row[2]) if row[2] else 0
        if t_e <= t_s: continue
        rows.append((target_date, TV_TO_CODE[tv], tv, prog, t_s, t_e, t_e - t_s))
    wb.close()
    return rows


# ── Calcolo audience_cache ────────────────────────────────────────────────────

def _build_audience_cache(conn: duckdb.DuckDBPyConnection, target_date: date) -> int:
    date_str = target_date.strftime("%Y-%m-%d")
    classif_in = ",".join(str(c) for c in CLASSIFICAZIONI_AUDITEL)
    total_rows = 0

    for target_id, target in TARGETS.items():
        conn.execute(f"""
            INSERT OR REPLACE INTO audience_cache
            WITH stmt_exp AS (
                SELECT s.cod_emit, s.t_start AS s_s, s.t_end AS s_e,
                       s.classificazione, s.cod_emit AS s_emit,
                       i.fat_exp, i.panel, i.prg
                FROM statements s
                JOIN individui i
                  ON s.data = i.data AND s.panel = i.panel AND s.prg = i.prg
                WHERE s.data = '{date_str}' AND {target.sql_where}
            ),
            tv_fascia_reale AS (
                SELECT p.cod_emit AS p_emit, p.t_start AS p_s, p.t_end AS p_e, p.programma,
                       SUM(GREATEST(0, LEAST(s.s_e, p.t_end) - GREATEST(s.s_s, p.t_start))
                           * s.fat_exp / 1000.0
                       ) / NULLIF(p.t_end - p.t_start, 0) AS tv_reale
                FROM programmi p
                JOIN stmt_exp s ON s.s_s < p.t_end AND s.s_e > p.t_start
                WHERE p.data = '{date_str}'
                GROUP BY p.cod_emit, p.t_start, p.t_end, p.programma
            ),
            tv_fascia_auditel AS (
                SELECT p.cod_emit AS p_emit, p.t_start AS p_s, p.t_end AS p_e, p.programma,
                       SUM(GREATEST(0, LEAST(s.s_e, p.t_end) - GREATEST(s.s_s, p.t_start))
                           * s.fat_exp / 1000.0
                       ) / NULLIF(p.t_end - p.t_start, 0) AS tv_auditel
                FROM programmi p
                JOIN stmt_exp s ON s.s_s < p.t_end AND s.s_e > p.t_start
                  AND s.classificazione IN ({classif_in})
                  AND s.s_emit != '{CODICE_NON_RICONOSCIUTO}'
                WHERE p.data = '{date_str}'
                GROUP BY p.cod_emit, p.t_start, p.t_end, p.programma
            ),
            prog_audience AS (
                SELECT p.cod_emit, p.tv, p.programma, p.t_start, p.t_end, p.durata_sec,
                       SUM(GREATEST(0, LEAST(s.s_e, p.t_end) - GREATEST(s.s_s, p.t_start))
                           * s.fat_exp / 1000.0
                       ) / NULLIF(p.durata_sec, 0) AS audience,
                       COUNT(DISTINCT CASE
                           WHEN GREATEST(0, LEAST(s.s_e, p.t_end) - GREATEST(s.s_s, p.t_start)) >= 60
                           THEN s.panel || '|' || s.prg::VARCHAR
                       END) * AVG(s.fat_exp) / 1000000.0 AS copertura
                FROM programmi p
                JOIN stmt_exp s ON s.cod_emit = p.cod_emit
                  AND s.s_s < p.t_end AND s.s_e > p.t_start
                WHERE p.data = '{date_str}'
                GROUP BY p.cod_emit, p.tv, p.programma, p.t_start, p.t_end, p.durata_sec
                HAVING audience > 500
            )
            SELECT
                DATE '{date_str}', pa.cod_emit, pa.tv, pa.programma,
                pa.t_start, pa.t_end, pa.durata_sec / 60,
                '{target_id}',
                pa.audience,
                pa.audience / NULLIF(fa.tv_auditel, 0) * 100,
                pa.audience / NULLIF(fr.tv_reale, 0)   * 100,
                pa.copertura
            FROM prog_audience pa
            JOIN tv_fascia_reale   fr ON fr.p_emit = pa.cod_emit
                AND fr.p_s = pa.t_start AND fr.p_e = pa.t_end AND fr.programma = pa.programma
            JOIN tv_fascia_auditel fa ON fa.p_emit = pa.cod_emit
                AND fa.p_s = pa.t_start AND fa.p_e = pa.t_end AND fa.programma = pa.programma
        """)
        n = conn.execute(f"""
            SELECT COUNT(*) FROM audience_cache
            WHERE data = '{date_str}' AND target_id = '{target_id}'
        """).fetchone()[0]
        total_rows += n
        log.info(f"  cache {target_id}: {n} righe")

    return total_rows


# ── Entry point ───────────────────────────────────────────────────────────────

def ingest_date(target_date: date, force: bool = False) -> dict:
    conn = get_conn()
    date_str = target_date.strftime("%Y-%m-%d")

    if not force:
        existing = conn.execute(
            "SELECT status FROM ingest_log WHERE data = ?", [target_date]
        ).fetchone()
        if existing and existing[0] == "ok":
            log.info(f"{date_str} già ingerito (usa force=True per reingestire)")
            return {"date": date_str, "status": "already_ingested"}

    source = get_source()
    log.info(f"Inizio ingestion {date_str}")

    auditel_files   = dict(source.list_auditel_files())
    programmi_files = dict(source.list_programmi_files())

    if target_date not in auditel_files:
        raise FileNotFoundError(f"Nessun tar.gz trovato per {date_str}")

    tar_path  = auditel_files[target_date]
    prog_path = programmi_files.get(target_date) or programmi_files.get(date(1970, 1, 1))
    if prog_path is None:
        raise FileNotFoundError(f"Nessun file programmi trovato per {date_str}")

    with tempfile.TemporaryDirectory(prefix="aiaiai_ingest_") as tmp:
        tmp_path = Path(tmp)
        log.info(f"  Estrazione {tar_path.name}...")
        with tarfile.open(tar_path, "r:gz") as tf:
            tf.extractall(tmp_path)

        stmtastd_path = next((p for p in tmp_path.iterdir() if p.name.lower().startswith("stmtastd")), None)
        fianag_path   = next((p for p in tmp_path.iterdir() if p.name.lower().startswith("fianag")),   None)

        if not stmtastd_path: raise FileNotFoundError("stmtastd non trovato")
        if not fianag_path:   raise FileNotFoundError("fianag non trovato")

        for idx in ("idx_stmt_data_emit","idx_ind_data","idx_prog_data","idx_cache_data"):
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        for table in ("statements","individui","programmi","audience_cache"):
            conn.execute(f"DELETE FROM {table} WHERE data = ?", [target_date])

        import pandas as pd

        log.info("  Parsing individui...")
        rows_ind = _parse_individui(fianag_path, target_date)
        if rows_ind:
            cols_i = ['data','panel','prg','fat_exp','city_size','cse','bambini_03',
                      'ragazzi_414','studi','sesso','eta','resp_acquisto','anno_nascita',
                      'ra_bambini_814','nuove_classi_eta','regione','sesso4','attivita',
                      'broadband','tv_connessa','tipo_meter']
            df_i = pd.DataFrame(rows_ind, columns=cols_i)
            conn.register("df_i", df_i)
            conn.execute("INSERT INTO individui SELECT * FROM df_i")
            conn.unregister("df_i")
        log.info(f"  {len(rows_ind)} individui inseriti")

        log.info("  Parsing statements...")
        rows_stmt = _parse_statements(stmtastd_path, target_date)
        if rows_stmt:
            cols_s = ['data','panel','prg','tipo_stmt','cod_emit',
                      't_start','t_end','piattaforma','classificazione','dig_vod']
            df_s = pd.DataFrame(rows_stmt, columns=cols_s)
            conn.register("df_s", df_s)
            conn.execute("INSERT INTO statements SELECT * FROM df_s")
            conn.unregister("df_s")
        log.info(f"  {len(rows_stmt)} statements inseriti")

    log.info("  Parsing programmi...")
    rows_prog = _parse_programmi(prog_path, target_date)
    if rows_prog:
        import pandas as pd
        cols_p = ['data','cod_emit','tv','programma','t_start','t_end','durata_sec']
        df_p = pd.DataFrame(rows_prog, columns=cols_p)
        conn.execute("INSERT INTO programmi SELECT * FROM df_p")

    log.info(f"  {len(rows_prog)} eventi programma inseriti")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_stmt_data_emit ON statements(data, cod_emit)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ind_data ON individui(data, panel, prg)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prog_data ON programmi(data, cod_emit)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_data ON audience_cache(data, target_id)")

    log.info("  Calcolo audience cache...")
    cache_rows = _build_audience_cache(conn, target_date)

    conn.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?,?)", [
        target_date, datetime.now(), len(rows_stmt), len(rows_ind),
        len(rows_prog), "ok", f"cache_rows={cache_rows}",
    ])

    result = {"date": date_str, "status": "ok", "statements": len(rows_stmt),
              "individui": len(rows_ind), "programmi": len(rows_prog), "cache_rows": cache_rows}
    log.info(f"Ingestion completata: {result}")
    return result


def ingest_all(force: bool = False) -> list[dict]:
    source = get_source()
    results = []
    for d, _ in source.list_auditel_files():
        try:
            results.append(ingest_date(d, force=force))
        except Exception as e:
            log.error(f"Errore ingestion {d}: {e}")
            results.append({"date": str(d), "status": "error", "error": str(e)})
    return results
