import tarfile
import tempfile
import logging
from abc import ABC, abstractmethod
from datetime import date, datetime
from pathlib import Path

import duckdb

from config import (
    AUDITEL_DIR, PROGRAMMI_DIR, STATIC_PROGRAMMI_PATH, DATA_SOURCE,
    S3_BUCKET, S3_PREFIX,
    TV_TO_CODE, CODE_TO_TV, TV_LABELS,
    CLASSIFICAZIONI_AUDITEL, CODICE_NON_RICONOSCIUTO,
)
from db import get_conn
import cells

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
        if not result:
            for candidate in (PROGRAMMI_DIR / "programmi_master.xlsx", STATIC_PROGRAMMI_PATH):
                if candidate.exists():
                    result.append((date(1970, 1, 1), candidate))
                    break
        return result


class S3Source(DataSource):
    """
    Lista solo metadata (economico), scarica un singolo file solo quando
    esplicitamente richiesto da fetch(). Evita di scaricare l'intero bucket
    ad ogni list_auditel_files().
    """
    def __init__(self):
        try:
            import boto3
            self._s3 = boto3.client("s3")
        except ImportError:
            raise RuntimeError("boto3 non installato: pip install boto3")
        self._tmp = Path(tempfile.mkdtemp(prefix="aiaiai_s3_"))
        self._keys_cache: dict[date, str] = {}      # date -> S3 key (tar.gz)
        self._prog_keys_cache: dict[date, str] = {} # date -> S3 key (xlsx)

    def _list_keys(self, suffix: str) -> dict[date, str]:
        paginator = self._s3.get_paginator("list_objects_v2")
        result = {}
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(suffix): continue
                fname = Path(key).name
                d = _parse_date_from_filename(fname)
                if not d: continue
                result[d] = key
        return result

    def list_auditel_files(self):
        if not self._keys_cache:
            self._keys_cache = self._list_keys(".tar.gz")
        # Path fittizio (non ancora scaricato): il download reale avviene in fetch()
        return sorted((d, self._tmp / Path(k).name) for d, k in self._keys_cache.items())

    def list_programmi_files(self):
        if not self._prog_keys_cache:
            self._prog_keys_cache = self._list_keys(".xlsx")
        return sorted((d, self._tmp / Path(k).name) for d, k in self._prog_keys_cache.items())

    def fetch(self, target_date: date, is_programmi: bool = False) -> Path:
        """Scarica il singolo file per la data richiesta, se non già in cache locale."""
        cache = self._prog_keys_cache if is_programmi else self._keys_cache
        if target_date not in cache:
            raise FileNotFoundError(f"Nessuna chiave S3 per {target_date}")
        key = cache[target_date]
        local = self._tmp / Path(key).name
        if not local.exists():
            self._s3.download_file(S3_BUCKET, key, str(local))
        return local


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


def _hhmmss_to_sec(s: str) -> int:
    """Ora statement del feed AltlabFilteredMDA: HHMMSS (6 cifre, ore 02-25).
    NB: il tracciato ufficiale dice HHMM, ma il feed reale ha i secondi —
    interpretarlo come HHMM proietta gli orari fino a +97h (bug luglio 2026).
    Vedi tests/test_parse_statements.py."""
    s = str(s).strip().zfill(6)
    return int(s[:2]) * 3600 + int(s[2:4]) * 60 + int(s[4:6])


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
            durata_sec = fields[8]   # il feed la fornisce GIA' in secondi
            piattaf    = fields[10]
            classif    = fields[18]
            dig_vod    = fields[20]
            if data_live != target_str: continue
            if tipo_stmt not in ("L", "V"): continue
            if tipo_ppl != "I": continue
            if dig_vod == "1": continue
            try:
                t_start = _hhmmss_to_sec(ora_ini)
                dur_sec = int(durata_sec)
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


def _insert_programmi_dedup(conn: duckdb.DuckDBPyConnection, df_p) -> int:
    """
    Inserisce il palinsesto del giorno deduplicando gli eventi spuri:
    stesso (cod_emit, programma, t_start) con t_end divergenti sono errori
    di rilevazione (non frammentazione da break: i frammenti avrebbero
    t_start progressivi). Nel gruppo di duplicati si tiene il t_end massimo
    che non sfora nel primo evento successivo del canale (contiguita'
    t_end == next_start ammessa; ultimo evento del canale: nessun vincolo).
    Se tutti sforano, il gruppo viene scartato per intero: nessun candidato
    e' affidabile. Ritorna il numero di righe inserite.
    """
    return conn.execute("""
        INSERT INTO programmi
        WITH eventi AS (
            SELECT *,
                   COUNT(*) OVER (PARTITION BY data, cod_emit, programma, t_start) AS n_dup
            FROM df_p
        ),
        successivi AS (
            -- primo t_start strettamente successivo sul canale: LEAD sui
            -- t_start DISTINTI, cosi' i duplicati dello stesso istante
            -- non contano mai come "evento successivo"
            SELECT data, cod_emit, t_start,
                   LEAD(t_start) OVER (PARTITION BY data, cod_emit ORDER BY t_start) AS next_start
            FROM (SELECT DISTINCT data, cod_emit, t_start FROM df_p)
        ),
        validi AS (
            -- le righe non duplicate passano sempre; un duplicato e'
            -- candidato solo se non sfora nell'evento successivo
            SELECT e.*
            FROM eventi e
            JOIN successivi s USING (data, cod_emit, t_start)
            WHERE e.n_dup = 1
               OR s.next_start IS NULL
               OR e.t_end <= s.next_start
        )
        -- tra i candidati del gruppo tiene il t_end massimo; se nessun
        -- duplicato e' candidato, il gruppo sparisce del tutto
        SELECT data, cod_emit, tv, programma, t_start, t_end, durata_sec
        FROM validi
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY data, cod_emit, programma, t_start
            ORDER BY t_end DESC
        ) = 1
    """).fetchone()[0]


# ── Calcolo audience_cache ────────────────────────────────────────────────────

def _assert_age_tiling(conn: duckdb.DuckDBPyConnection, date_str: str) -> None:
    """Guardia di tiling della partizione età. Ogni individuo deve stare in
    kids∪demo: nel SuperPanel sesso ed età sono sempre valorizzati, quindi un
    individuo fuori dominio (sesso=0 o nuove_classi_eta fuori 1..11) è un errore
    di parsing, NON una categoria residua. Si logga con panel/prg e si fa fallire
    l'ingest del giorno (che finisce come error, come i giorni con PK duplicata),
    invece di produrre un 4+ silenziosamente sbagliato."""
    uncovered = (
        "NOT ("
        " (nuove_classi_eta BETWEEN 1 AND 4)"
        " OR (nuove_classi_eta BETWEEN 5 AND 11 AND sesso IN (1,2) AND resp_acquisto IN (0,1))"
        " )"
    )
    n = conn.execute(
        f"SELECT COUNT(*) FROM individui WHERE data = DATE '{date_str}' AND {uncovered}"
    ).fetchone()[0]
    if n:
        sample = conn.execute(
            f"SELECT panel, prg, nuove_classi_eta, sesso, resp_acquisto "
            f"FROM individui WHERE data = DATE '{date_str}' AND {uncovered} LIMIT 20"
        ).fetchall()
        log.error(f"{date_str}: {n} individui fuori da kids∪demo (errore parsing?): {sample}")
        raise ValueError(
            f"{date_str}: {n} individui non classificabili per età/sesso "
            f"(fuori da kids∪demo) — ingest fallito, dato da verificare"
        )


def _block_insert_sql(cfg: dict, date_str: str, classif_in: str, nr: str) -> str:
    g = cfg["gcols"]
    sel_p  = "".join(f", p.{c}"  for c in g)   # colonne cella in pop → sp
    sel_sp = "".join(f", sp.{c}" for c in g)   # colonne cella in num/den/reached
    sel_c  = "".join(f", {c}"    for c in g)   # nomi colonna nudi (carry-through)
    jn = "".join(f" AND num.{c} = b.{c}" for c in g)
    jc = "".join(f" AND cop.{c} = b.{c}" for c in g)

    if cfg["block"] == "demo":
        cell_id = "'D' || lpad(b.nuove_classi_eta::VARCHAR, 2, '0') || '_' || b.sesso || '_' || b.resp_acquisto"
        age_e, sesso_e, ra_e, cse_e = "b.nuove_classi_eta", "b.sesso", "b.resp_acquisto", "NULL"
    elif cfg["block"] == "kids":
        cell_id = "'K'"
        age_e = sesso_e = ra_e = cse_e = "NULL"
    else:  # cse
        cell_id = "'C' || b.cse::VARCHAR"
        age_e, sesso_e, ra_e, cse_e = "NULL", "NULL", "NULL", "b.cse"

    ov = "GREATEST(0, LEAST(sp.s_e, pr.t_end) - GREATEST(sp.s_s, pr.t_start))"
    return f"""
        INSERT INTO audience_cache
        WITH pop AS (      -- popolazione del blocco (individui), con la chiave-cella
            SELECT panel, prg, fat_exp{sel_c}
            FROM individui
            WHERE data = DATE '{date_str}' AND {cfg['pop']}
        ),
        sp AS (            -- statements degli individui della popolazione
            SELECT s.cod_emit AS s_emit, s.t_start AS s_s, s.t_end AS s_e,
                   s.classificazione, p.fat_exp, p.panel, p.prg{sel_p}
            FROM statements s
            JOIN pop p ON s.data = DATE '{date_str}' AND s.panel = p.panel AND s.prg = p.prg
        ),
        num AS (           -- numeratore audience: statement sullo STESSO canale del programma
            SELECT pr.cod_emit, pr.programma, pr.t_start, pr.t_end{sel_sp},
                   SUM({ov} * sp.fat_exp / 1000.0) / NULLIF(pr.durata_sec, 0) AS num_audience
            FROM programmi pr
            JOIN sp ON sp.s_emit = pr.cod_emit AND sp.s_s < pr.t_end AND sp.s_e > pr.t_start
            WHERE pr.data = DATE '{date_str}'
            GROUP BY pr.cod_emit, pr.programma, pr.t_start, pr.t_end, pr.durata_sec{sel_sp}
        ),
        reached AS (       -- (evento, cella, individuo) con overlap massimo, per la copertura
            SELECT pr.cod_emit, pr.programma, pr.t_start, pr.t_end{sel_sp}, sp.panel, sp.prg,
                   ANY_VALUE(sp.fat_exp) AS fat_exp, MAX({ov}) AS max_ov
            FROM programmi pr
            JOIN sp ON sp.s_emit = pr.cod_emit AND sp.s_s < pr.t_end AND sp.s_e > pr.t_start
            WHERE pr.data = DATE '{date_str}'
            GROUP BY pr.cod_emit, pr.programma, pr.t_start, pr.t_end{sel_sp}, sp.panel, sp.prg
        ),
        cop AS (           -- copertura additiva ESATTA: Σ fat_exp/1000 sui distinti raggiunti (overlap>=60s)
            SELECT cod_emit, programma, t_start, t_end{sel_c},
                   SUM(CASE WHEN max_ov >= 60 THEN fat_exp / 1000.0 END) AS copertura
            FROM reached
            GROUP BY cod_emit, programma, t_start, t_end{sel_c}
        ),
        den AS (           -- denominatori di fascia: QUALUNQUE canale nella finestra del programma
            SELECT pr.cod_emit, pr.tv, pr.programma, pr.t_start, pr.t_end, pr.durata_sec{sel_sp},
                   SUM({ov} * sp.fat_exp / 1000.0) / NULLIF(pr.t_end - pr.t_start, 0) AS den_reale,
                   SUM(CASE WHEN sp.classificazione IN ({classif_in}) AND sp.s_emit != '{nr}'
                            THEN {ov} * sp.fat_exp / 1000.0 END)
                     / NULLIF(pr.t_end - pr.t_start, 0) AS den_auditel
            FROM programmi pr
            JOIN sp ON sp.s_s < pr.t_end AND sp.s_e > pr.t_start
            WHERE pr.data = DATE '{date_str}'
            GROUP BY pr.cod_emit, pr.tv, pr.programma, pr.t_start, pr.t_end, pr.durata_sec{sel_sp}
        )
        -- base = den (tutte le celle con qualche ascolto nella finestra); num/cop
        -- in LEFT JOIN (0 se la cella non ha visto QUESTO programma). Nessuna
        -- soglia per-cella: le celle memorizzano tutto, la soglia audience>500
        -- si applica a query-time sul target ricomposto.
        SELECT
            DATE '{date_str}', b.cod_emit, b.tv, b.programma,
            b.t_start, b.t_end, b.durata_sec / 60,
            '{cfg['partizione']}', '{cfg['block']}', {cell_id},
            {age_e}, {sesso_e}, {ra_e}, {cse_e},
            COALESCE(num.num_audience, 0), b.den_auditel, b.den_reale, COALESCE(cop.copertura, 0)
        FROM den b
        LEFT JOIN num ON num.cod_emit = b.cod_emit AND num.programma = b.programma
             AND num.t_start = b.t_start AND num.t_end = b.t_end{jn}
        LEFT JOIN cop ON cop.cod_emit = b.cod_emit AND cop.programma = b.programma
             AND cop.t_start = b.t_start AND cop.t_end = b.t_end{jc}
        WHERE b.den_reale > 0
    """


def _build_audience_cache(conn: duckdb.DuckDBPyConnection, target_date: date) -> int:
    """Costruisce le 34 celle atomiche del giorno (3 query, una per blocco, con
    GROUP BY sulle dimensioni-cella). Per ogni (evento × cella) memorizza gli
    ingredienti grezzi additivi: num_audience, den_auditel/den_reale (audience di
    fascia sulla popolazione della cella), copertura (Σ fat_exp sui distinti
    raggiunti). Mai la share divisa: si ricompone a query-time."""
    date_str = target_date.strftime("%Y-%m-%d")
    classif_in = ",".join(str(c) for c in CLASSIFICAZIONI_AUDITEL)
    nr = CODICE_NON_RICONOSCIUTO

    _assert_age_tiling(conn, date_str)

    total = 0
    for cfg in cells.BLOCKS:
        conn.execute(_block_insert_sql(cfg, date_str, classif_in, nr))
        n = conn.execute(
            f"SELECT COUNT(*) FROM audience_cache "
            f"WHERE data = DATE '{date_str}' AND block = '{cfg['block']}'"
        ).fetchone()[0]
        total += n
        log.info(f"  cache blocco {cfg['block']}: {n} righe")

    return total


# ── Spia di plausibilita' ─────────────────────────────────────────────────────
# Programmi-ancora con ordine di grandezza noto dal mondo reale (audience 4+,
# picco-evento del giorno). Range volutamente larghi: e' una spia, non un test
# rigido — se scatta, qualcosa a monte (parsing, pesi, feed) si e' quasi
# certamente rotto. Ancoraggio a valori esterni, non solo a coerenza interna:
# il bug HHMM/x60 (luglio 2026) dava Affari Tuoi a 2,4M ed era internamente
# coerentissimo. Con questi range sarebbe scattata al primo ingest.
ANCHORS = [
    ("AFFARI TUOI%",            "0001", 3_000_000, 8_000_000),
    ("TG1 SERA%",               "0001", 2_500_000, 6_000_000),
    ("LA RUOTA DELLA FORTUNA%", "0004", 3_000_000, 8_000_000),
    ("TG5 (20%",                "0004", 2_000_000, 6_000_000),
]


def _check_anchors(conn: duckdb.DuckDBPyConnection, target_date: date) -> str:
    """Controllo di plausibilita' esterno post-ingest: per ogni programma-ancora
    in onda nel giorno, il picco-evento di audience 4+ deve cadere nel range
    noto. Logga WARNING se fuori; ritorna il riassunto per ingest_log."""
    esiti = []
    for pattern, emit, lo, hi in ANCHORS:
        aud = conn.execute("""
            SELECT MAX(aud) FROM (
                SELECT SUM(num_audience) AS aud
                FROM audience_cache
                WHERE data = ? AND cod_emit = ? AND programma LIKE ?
                  AND block IN ('kids','demo')
                GROUP BY programma, t_start
            )
        """, [target_date, emit, pattern]).fetchone()[0]
        if aud is None:
            continue    # ancora non in onda quel giorno
        nome = pattern.rstrip("%")
        if lo <= aud <= hi:
            esiti.append(f"{nome}={aud/1e6:.1f}M ok")
        else:
            log.warning(f"  SPIA PLAUSIBILITA' {target_date}: {nome} = {aud:,.0f} "
                        f"fuori range [{lo/1e6:.1f}M-{hi/1e6:.1f}M] — pipeline da verificare")
            esiti.append(f"{nome}={aud/1e6:.1f}M FUORI RANGE [{lo/1e6:.1f}-{hi/1e6:.1f}]")
    return "; ".join(esiti) if esiti else "nessuna ancora in onda"


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

    auditel_files = dict(source.list_auditel_files())

    if target_date not in auditel_files:
        raise FileNotFoundError(f"Nessun tar.gz trovato per {date_str}")

    tar_path  = auditel_files[target_date]
    # Se la source è S3, il tar_path sopra è un placeholder: materializza il download
    if isinstance(source, S3Source):
        tar_path = source.fetch(target_date, is_programmi=False)

    # I programmi sono un asset statico dell'app, indipendente dalla data source:
    # file datato in PROGRAMMI_DIR, poi master locale, poi bundle statico.
    prog_path = None
    for cand in (
        PROGRAMMI_DIR / f"programmi_{date_str}.xlsx",
        PROGRAMMI_DIR / "programmi_master.xlsx",
        STATIC_PROGRAMMI_PATH,
    ):
        if cand.exists():
            prog_path = cand
            break
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
    n_prog = 0
    if rows_prog:
        import pandas as pd
        cols_p = ['data','cod_emit','tv','programma','t_start','t_end','durata_sec']
        df_p = pd.DataFrame(rows_prog, columns=cols_p)
        n_prog = _insert_programmi_dedup(conn, df_p)

    log.info(f"  {n_prog} eventi programma inseriti ({len(rows_prog) - n_prog} duplicati scartati)")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_stmt_data_emit ON statements(data, cod_emit)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ind_data ON individui(data, panel, prg)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prog_data ON programmi(data, cod_emit)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_data ON audience_cache(data, block)")

    log.info("  Calcolo audience cache...")
    cache_rows = _build_audience_cache(conn, target_date)
    spia = _check_anchors(conn, target_date)
    log.info(f"  spia plausibilita': {spia}")

    conn.execute("INSERT OR REPLACE INTO ingest_log VALUES (?,?,?,?,?,?,?)", [
        target_date, datetime.now(), len(rows_stmt), len(rows_ind),
        n_prog, "ok", f"cache_rows={cache_rows}; spia: {spia}",
    ])

    result = {"date": date_str, "status": "ok", "statements": len(rows_stmt),
              "individui": len(rows_ind), "programmi": n_prog, "cache_rows": cache_rows}
    # calcola-e-scarta: le tabelle grezze non servono dopo la cache.
    # Il tar.gz resta su S3 come source of truth ricostruibile.
    conn.execute("DELETE FROM statements WHERE data = ?", [target_date])
    conn.execute("DELETE FROM individui  WHERE data = ?", [target_date])

    log.info(f"Ingestion completata: {result}")
    return result


def ingest_range(start: date, end: date, force: bool = False) -> dict:
    """
    Ingesta tutti i giorni nell'intervallo [start, end] inclusi.
    Salta i giorni gia' in cache (a meno di force). Checkpoint ogni 5 giorni
    per contenere la crescita del file DuckDB dovuta al MVCC.
    Pensata per girare in background: non solleva, accumula esiti per giorno.
    """
    from datetime import timedelta
    conn = get_conn()

    esiti = {"ok": [], "skip": [], "error": []}
    processed = 0

    d = start
    while d <= end:
        try:
            r = ingest_date(d, force=force)
            if r.get("status") == "ok":
                esiti["ok"].append(str(d))
                processed += 1
                if processed % 5 == 0:
                    conn.execute("CHECKPOINT")
                    log.info(f"  checkpoint dopo {processed} giorni")
            else:
                esiti["skip"].append(str(d))
        except FileNotFoundError as e:
            # tar.gz non ancora su S3, o programmi mancanti: ritentabile
            log.warning(f"  {d} saltato: {e}")
            esiti["skip"].append(str(d))
        except Exception as e:
            log.error(f"  {d} errore: {e}")
            esiti["error"].append(str(d))
        d += timedelta(days=1)

    conn.execute("CHECKPOINT")
    log.info(f"ingest_range completato: {len(esiti['ok'])} ok, "
             f"{len(esiti['skip'])} skip, {len(esiti['error'])} error")
    return esiti


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
