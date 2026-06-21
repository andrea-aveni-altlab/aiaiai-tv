"""
Tutte le query UI leggono da audience_cache — mai dai dati grezzi.
"""

from datetime import date
from db import get_conn
from config import TV_LABELS
from targets import get_target, DEFAULT_TARGET


def _sec_to_hhmm(s: int) -> str:
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}"


def _row_to_dict(row, cols: list[str]) -> dict:
    return dict(zip(cols, row))


def get_programmi_giorno(
    data: date,
    target_id: str = DEFAULT_TARGET,
    cod_emit: str | None = None,
    min_audience: float = 0,
) -> list[dict]:
    conn = get_conn()
    get_target(target_id)
    emit_filter = f"AND cod_emit = '{cod_emit}'" if cod_emit else ""
    rows = conn.execute(f"""
        SELECT
            cod_emit, tv, programma,
            MIN(t_start) AS t_start,
            MAX(t_end)   AS t_end,
            SUM(durata_min) AS durata_min,
            ROUND(SUM(audience      * durata_min) / NULLIF(SUM(durata_min), 0))        AS audience,
            ROUND(SUM(share_auditel * durata_min) / NULLIF(SUM(durata_min), 0), 1)     AS share_auditel,
            ROUND(SUM(share_reale   * durata_min) / NULLIF(SUM(durata_min), 0), 1)     AS share_reale,
            ROUND(MAX(copertura))                                                       AS copertura
        FROM audience_cache
        WHERE data = ? AND target_id = ?
          {emit_filter}
        GROUP BY cod_emit, tv, programma
        HAVING ROUND(SUM(audience * durata_min) / NULLIF(SUM(durata_min), 0)) >= ?
        ORDER BY tv, MIN(t_start)
    """, [data, target_id, min_audience]).fetchall()
    cols = ["cod_emit","tv","programma","t_start","t_end",
            "durata_min","audience","share_auditel","share_reale","copertura"]
    result = []
    for row in rows:
        d = _row_to_dict(row, cols)
        d["tv_label"]   = TV_LABELS.get(d["cod_emit"], d["tv"])
        d["ora_inizio"] = _sec_to_hhmm(d["t_start"])
        d["ora_fine"]   = _sec_to_hhmm(d["t_end"])
        result.append(d)
    return result


def get_prime_time(
    data: date,
    target_id: str = DEFAULT_TARGET,
    ora_inizio: int = 20 * 3600,
    ora_fine: int   = 23 * 3600,
) -> list[dict]:
    conn = get_conn()
    get_target(target_id)
    rows = conn.execute("""
        SELECT cod_emit, tv, programma, t_start, t_end, durata_min,
               ROUND(audience) AS audience,
               ROUND(share_auditel, 1) AS share_auditel,
               ROUND(share_reale, 1)   AS share_reale
        FROM audience_cache
        WHERE data = ? AND target_id = ?
          AND t_start >= ? AND t_end <= ?
        ORDER BY tv, t_start
    """, [data, target_id, ora_inizio, ora_fine]).fetchall()
    cols = ["cod_emit","tv","programma","t_start","t_end",
            "durata_min","audience","share_auditel","share_reale"]
    result = []
    for row in rows:
        d = _row_to_dict(row, cols)
        d["tv_label"]   = TV_LABELS.get(d["cod_emit"], d["tv"])
        d["ora_inizio"] = _sec_to_hhmm(d["t_start"])
        d["ora_fine"]   = _sec_to_hhmm(d["t_end"])
        result.append(d)
    return result


def get_prime_time_summary(
    data: date,
    target_id: str = DEFAULT_TARGET,
    ora_inizio: int = 20 * 3600,
    ora_fine: int   = 23 * 3600,
) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT cod_emit, tv,
               ROUND(SUM(audience      * durata_min) / NULLIF(SUM(durata_min), 0)) AS audience_media,
               ROUND(SUM(share_auditel * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_auditel,
               ROUND(SUM(share_reale   * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_reale
        FROM audience_cache
        WHERE data = ? AND target_id = ?
          AND t_start >= ? AND t_end <= ?
        GROUP BY cod_emit, tv
        ORDER BY share_auditel DESC
    """, [data, target_id, ora_inizio, ora_fine]).fetchall()
    cols = ["cod_emit","tv","audience_media","share_auditel","share_reale"]
    result = []
    for row in rows:
        d = _row_to_dict(row, cols)
        d["tv_label"] = TV_LABELS.get(d["cod_emit"], d["tv"])
        result.append(d)
    return result


def get_storico_programma(
    programma: str,
    target_id: str = DEFAULT_TARGET,
    data_from: date | None = None,
    data_to: date | None   = None,
    cod_emit: str | None   = None,
) -> list[dict]:
    conn = get_conn()
    get_target(target_id)
    filters = ["target_id = ?", "LOWER(programma) LIKE LOWER(?)"]
    params: list = [target_id, f"%{programma}%"]
    if data_from:
        filters.append("data >= ?"); params.append(data_from)
    if data_to:
        filters.append("data <= ?"); params.append(data_to)
    if cod_emit:
        filters.append("cod_emit = ?"); params.append(cod_emit)
    where = " AND ".join(filters)
    rows = conn.execute(f"""
        SELECT data::VARCHAR AS data, cod_emit, tv, programma,
               MIN(t_start) AS t_start,
               SUM(durata_min) AS durata_min,
               ROUND(SUM(audience      * durata_min) / NULLIF(SUM(durata_min), 0))        AS audience,
               ROUND(SUM(share_auditel * durata_min) / NULLIF(SUM(durata_min), 0), 1)     AS share_auditel,
               ROUND(SUM(share_reale   * durata_min) / NULLIF(SUM(durata_min), 0), 1)     AS share_reale
        FROM audience_cache
        WHERE {where}
        GROUP BY data, cod_emit, tv, programma
        ORDER BY data, tv, MIN(t_start)
    """, params).fetchall()
    cols = ["data","cod_emit","tv","programma","t_start",
            "durata_min","audience","share_auditel","share_reale"]
    result = []
    for row in rows:
        d = _row_to_dict(row, cols)
        d["tv_label"]   = TV_LABELS.get(d["cod_emit"], d["tv"])
        d["ora_inizio"] = _sec_to_hhmm(d["t_start"])
        result.append(d)
    return result


def search_programmi(query: str, limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT programma, tv, cod_emit,
               MAX(data)::VARCHAR AS ultima_data
        FROM audience_cache
        WHERE LOWER(programma) LIKE LOWER(?)
          AND target_id = '4plus'
        GROUP BY programma, tv, cod_emit
        ORDER BY ultima_data DESC
        LIMIT ?
    """, [f"%{query}%", limit]).fetchall()
    return [{"programma": r[0], "tv": r[1],
             "tv_label": TV_LABELS.get(r[2], r[1]),
             "cod_emit": r[2], "ultima_data": r[3]} for r in rows]


def get_top_programmi(
    data: date,
    target_id: str = DEFAULT_TARGET,
    n: int = 20,
    fascia_start: int | None = None,
    fascia_end: int | None   = None,
) -> list[dict]:
    conn = get_conn()
    get_target(target_id)
    fascia_filter = ""
    params: list = [data, target_id]
    if fascia_start is not None:
        fascia_filter += " AND t_start >= ?"; params.append(fascia_start)
    if fascia_end is not None:
        fascia_filter += " AND t_end <= ?";   params.append(fascia_end)
    params.append(n)
    rows = conn.execute(f"""
        SELECT cod_emit, tv, programma,
               MIN(t_start) AS t_start,
               SUM(durata_min) AS durata_min,
               ROUND(SUM(audience      * durata_min) / NULLIF(SUM(durata_min), 0))        AS audience,
               ROUND(SUM(share_auditel * durata_min) / NULLIF(SUM(durata_min), 0), 1)     AS share_auditel,
               ROUND(SUM(share_reale   * durata_min) / NULLIF(SUM(durata_min), 0), 1)     AS share_reale
        FROM audience_cache
        WHERE data = ? AND target_id = ?
          {fascia_filter}
        GROUP BY cod_emit, tv, programma
        ORDER BY audience DESC
        LIMIT ?
    """, params).fetchall()
    cols = ["cod_emit","tv","programma","t_start","durata_min",
            "audience","share_auditel","share_reale"]
    result = []
    for row in rows:
        d = _row_to_dict(row, cols)
        d["tv_label"]   = TV_LABELS.get(d["cod_emit"], d["tv"])
        d["ora_inizio"] = _sec_to_hhmm(d["t_start"])
        result.append(d)
    return result


def get_status() -> dict:
    conn = get_conn()
    rows = conn.execute("""
        SELECT data::VARCHAR, ingested_at::VARCHAR,
               stmt_count, ind_count, prog_count, status
        FROM ingest_log ORDER BY data DESC LIMIT 10
    """).fetchall()
    return {
        "available_dates": [r[0] for r in rows if r[5] == "ok"],
        "last_ingest": rows[0] if rows else None,
        "log": [dict(zip(["data","ingested_at","stmt_count","ind_count","prog_count","status"], r))
                for r in rows],
    }
