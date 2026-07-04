"""
Tutte le query UI leggono da audience_cache — mai dai dati grezzi.

Modello a celle atomiche: audience_cache ha una riga per (evento × cella). Ogni
query ricompone il target in due stadi:
  1) celle → evento  (cells.event_stage: SUM(num)/SUM(den), SUM(copertura));
  2) eventi → programma  (media pesata sulla durata, invariata dal modello vecchio).
Il target è un preset (cells.preset_where) o, in prospettiva, un custom builder.
"""

from datetime import date
from db import get_conn
from config import TV_LABELS
from cells import preset_where, event_stage, DEFAULT_TARGET


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
    cell_where = preset_where(target_id)
    row = "data = ?"
    params: list = [data]
    if cod_emit:
        row += " AND cod_emit = ?"; params.append(cod_emit)
    params.append(min_audience)   # soglia a query-time sul target ricomposto
    sql = event_stage(cell_where, row) + """
        SELECT cod_emit, tv, programma,
               MIN(t_start) AS t_start, MAX(t_end) AS t_end, SUM(durata_min) AS durata_min,
               ROUND(SUM(audience      * durata_min) / NULLIF(SUM(durata_min), 0))    AS audience,
               ROUND(SUM(share_auditel * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_auditel,
               ROUND(SUM(share_reale   * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_reale,
               ROUND(MAX(copertura))                                                  AS copertura
        FROM evento
        GROUP BY cod_emit, tv, programma
        HAVING ROUND(SUM(audience * durata_min) / NULLIF(SUM(durata_min), 0)) >= ?
        ORDER BY tv, MIN(t_start)
    """
    rows = conn.execute(sql, params).fetchall()
    cols = ["cod_emit","tv","programma","t_start","t_end",
            "durata_min","audience","share_auditel","share_reale","copertura"]
    result = []
    for row_ in rows:
        d = _row_to_dict(row_, cols)
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
    cell_where = preset_where(target_id)
    sql = event_stage(cell_where, "data = ? AND t_start >= ? AND t_end <= ?") + """
        SELECT cod_emit, tv, programma, t_start, t_end, durata_min,
               ROUND(audience)         AS audience,
               ROUND(share_auditel, 1) AS share_auditel,
               ROUND(share_reale, 1)   AS share_reale
        FROM evento
        ORDER BY tv, t_start
    """
    rows = conn.execute(sql, [data, ora_inizio, ora_fine]).fetchall()
    cols = ["cod_emit","tv","programma","t_start","t_end",
            "durata_min","audience","share_auditel","share_reale"]
    result = []
    for row_ in rows:
        d = _row_to_dict(row_, cols)
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
    cell_where = preset_where(target_id)
    sql = event_stage(cell_where, "data = ? AND t_start >= ? AND t_end <= ?") + """
        SELECT cod_emit, tv,
               ROUND(SUM(audience      * durata_min) / NULLIF(SUM(durata_min), 0))    AS audience_media,
               ROUND(SUM(share_auditel * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_auditel,
               ROUND(SUM(share_reale   * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_reale
        FROM evento
        GROUP BY cod_emit, tv
        ORDER BY share_auditel DESC
    """
    rows = conn.execute(sql, [data, ora_inizio, ora_fine]).fetchall()
    cols = ["cod_emit","tv","audience_media","share_auditel","share_reale"]
    result = []
    for row_ in rows:
        d = _row_to_dict(row_, cols)
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
    cell_where = preset_where(target_id)
    filters = ["LOWER(programma) LIKE LOWER(?)"]
    params: list = [f"%{programma}%"]
    if data_from:
        filters.append("data >= ?"); params.append(data_from)
    if data_to:
        filters.append("data <= ?"); params.append(data_to)
    if cod_emit:
        filters.append("cod_emit = ?"); params.append(cod_emit)
    sql = event_stage(cell_where, " AND ".join(filters)) + """
        SELECT data::VARCHAR AS data, cod_emit, tv, programma,
               MIN(t_start) AS t_start, SUM(durata_min) AS durata_min,
               ROUND(SUM(audience      * durata_min) / NULLIF(SUM(durata_min), 0))    AS audience,
               ROUND(SUM(share_auditel * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_auditel,
               ROUND(SUM(share_reale   * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_reale
        FROM evento
        GROUP BY data, cod_emit, tv, programma
        ORDER BY data, tv, MIN(t_start)
    """
    rows = conn.execute(sql, params).fetchall()
    cols = ["data","cod_emit","tv","programma","t_start",
            "durata_min","audience","share_auditel","share_reale"]
    result = []
    for row_ in rows:
        d = _row_to_dict(row_, cols)
        d["tv_label"]   = TV_LABELS.get(d["cod_emit"], d["tv"])
        d["ora_inizio"] = _sec_to_hhmm(d["t_start"])
        result.append(d)
    return result


def search_programmi(query: str, limit: int = 20) -> list[dict]:
    conn = get_conn()
    # I nomi programma esistono in tutte le celle: basta il distinct, nessun target.
    rows = conn.execute("""
        SELECT programma, tv, cod_emit, MAX(data)::VARCHAR AS ultima_data
        FROM audience_cache
        WHERE LOWER(programma) LIKE LOWER(?)
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
    cell_where = preset_where(target_id)
    row = "data = ?"
    params: list = [data]
    if fascia_start is not None:
        row += " AND t_start >= ?"; params.append(fascia_start)
    if fascia_end is not None:
        row += " AND t_end <= ?";   params.append(fascia_end)
    params.append(n)
    sql = event_stage(cell_where, row) + """
        SELECT cod_emit, tv, programma,
               MIN(t_start) AS t_start, SUM(durata_min) AS durata_min,
               ROUND(SUM(audience      * durata_min) / NULLIF(SUM(durata_min), 0))    AS audience,
               ROUND(SUM(share_auditel * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_auditel,
               ROUND(SUM(share_reale   * durata_min) / NULLIF(SUM(durata_min), 0), 1) AS share_reale
        FROM evento
        GROUP BY cod_emit, tv, programma
        ORDER BY audience DESC
        LIMIT ?
    """
    rows = conn.execute(sql, params).fetchall()
    cols = ["cod_emit","tv","programma","t_start","durata_min",
            "audience","share_auditel","share_reale"]
    result = []
    for row_ in rows:
        d = _row_to_dict(row_, cols)
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
    from db import available_dates
    ok_dates = available_dates()
    return {
        "available_dates": ok_dates,
        "total_days": len(ok_dates),
        "date_min": ok_dates[-1] if ok_dates else None,
        "date_max": ok_dates[0] if ok_dates else None,
        "last_ingest": rows[0] if rows else None,
        "log": [dict(zip(["data","ingested_at","stmt_count","ind_count","prog_count","status"], r))
                for r in rows],
    }
