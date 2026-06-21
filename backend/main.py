import logging
import os
from datetime import date, datetime
from typing import Optional

import anthropic
from fastapi import FastAPI, HTTPException, Query, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import INGEST_API_KEY, CLAUDE_API_KEY, CLAUDE_MODEL, TV_LABELS, FASCE
from db import get_conn, available_dates, last_ingested_date
from ingest import ingest_date, ingest_all
from queries import (
    get_programmi_giorno, get_prime_time, get_prime_time_summary,
    get_storico_programma, search_programmi, get_top_programmi, get_status,
)
from targets import TARGETS, DEFAULT_TARGET

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="AIAIAI TV", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _parse_date(date_str: str) -> date:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"Formato data non valido: {date_str}")


def _check_api_key(x_api_key: str | None):
    if x_api_key != INGEST_API_KEY:
        raise HTTPException(401, "API key non valida")


@app.get("/api/status")
def status():
    return get_status()

@app.get("/api/targets")
def list_targets():
    return [{"id": t.id, "label": t.label, "short": t.short} for t in TARGETS.values()]

@app.get("/api/emittenti")
def list_emittenti():
    return [{"cod_emit": k, "nome": v} for k, v in TV_LABELS.items()]

@app.get("/api/fasce")
def list_fasce():
    return [{"id": k, "label": k.replace("_"," ").title(), "start": v[0], "end": v[1]}
            for k, v in FASCE.items()]

@app.get("/api/programmi/{data_str}")
def programmi_giorno(
    data_str: str,
    target: str = Query(DEFAULT_TARGET),
    emit: Optional[str] = Query(None),
    min_audience: float = Query(0),
):
    d = _parse_date(data_str)
    return get_programmi_giorno(d, target_id=target, cod_emit=emit, min_audience=min_audience)

@app.get("/api/programmi/{data_str}/{cod_emit}")
def programmi_emittente(data_str: str, cod_emit: str, target: str = Query(DEFAULT_TARGET)):
    d = _parse_date(data_str)
    result = get_programmi_giorno(d, target_id=target, cod_emit=cod_emit)
    if not result:
        raise HTTPException(404, f"Nessun dato per {cod_emit} il {data_str}")
    return result

@app.get("/api/primetime/{data_str}")
def prime_time(
    data_str: str,
    target: str = Query(DEFAULT_TARGET),
    start: int  = Query(20 * 3600),
    end: int    = Query(23 * 3600),
):
    d = _parse_date(data_str)
    return {
        "programmi": get_prime_time(d, target_id=target, ora_inizio=start, ora_fine=end),
        "summary":   get_prime_time_summary(d, target_id=target, ora_inizio=start, ora_fine=end),
    }

@app.get("/api/top/{data_str}")
def top_programmi(
    data_str: str,
    target: str = Query(DEFAULT_TARGET),
    n: int = Query(20),
    fascia: Optional[str] = Query(None),
):
    d = _parse_date(data_str)
    fascia_start = fascia_end = None
    if fascia and fascia in FASCE:
        fascia_start, fascia_end = FASCE[fascia]
    return get_top_programmi(d, target_id=target, n=n,
                             fascia_start=fascia_start, fascia_end=fascia_end)

@app.get("/api/programma/{titolo}")
def storico_programma(
    titolo: str,
    target: str              = Query(DEFAULT_TARGET),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str]   = Query(None, alias="to"),
    emit: Optional[str]      = Query(None),
):
    d_from = _parse_date(from_date) if from_date else None
    d_to   = _parse_date(to_date)   if to_date   else None
    result = get_storico_programma(titolo, target_id=target,
                                   data_from=d_from, data_to=d_to, cod_emit=emit)
    if not result:
        raise HTTPException(404, f"Nessun dato per '{titolo}'")
    return result

@app.get("/api/search")
def search(q: str = Query(..., min_length=2), limit: int = Query(20)):
    return search_programmi(q, limit=limit)


class NLRequest(BaseModel):
    q: str
    data: Optional[str] = None
    target: str = DEFAULT_TARGET

SYSTEM_NL = """Sei un assistente specializzato in ascolti televisivi italiani.
Hai accesso a dati di audience e share TV per le 7 emittenti generaliste (RAI 1-3, Canale 5, Italia 1, Rete 4, LA7).
Metriche: audience (spettatori medi), share_auditel (% su TV accese escl. non riconosciuto), share_reale (% incluso Netflix/Prime ecc.), copertura.
Rispondi in italiano, in modo conciso e giornalistico. Segnala sempre quale share stai citando."""

@app.post("/api/nl")
def nl_query(req: NLRequest):
    if not CLAUDE_API_KEY:
        raise HTTPException(503, "Claude API non configurata")
    context = _build_nl_context(req)
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    msg = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=1000,
        system=SYSTEM_NL,
        messages=[{"role":"user","content":f"Dati:\n{context}\n\nDomanda: {req.q}"}],
    )
    return {"response": msg.content[0].text, "context": context}

def _build_nl_context(req: NLRequest) -> str:
    lines = [f"Date disponibili: {', '.join(available_dates()[:5])}"]
    if req.data:
        try:
            d = _parse_date(req.data)
            top = get_top_programmi(d, target_id=req.target, n=10)
            lines.append(f"\nTop 10 programmi {req.data}:")
            for p in top:
                lines.append(f"  {p['tv_label']:10} {p['ora_inizio']} {p['programma'][:35]:<35} aud={p['audience']:>9,.0f} sh={p['share_auditel']}%")
        except Exception:
            pass
    return "\n".join(lines)


@app.post("/api/ingest/{data_str}")
def trigger_ingest(
    data_str: str,
    background_tasks: BackgroundTasks,
    force: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    _check_api_key(x_api_key)
    d = _parse_date(data_str)
    background_tasks.add_task(_run_ingest, d, force)
    return {"status": "started", "date": data_str}

@app.post("/api/ingest/all")
def trigger_ingest_all(
    background_tasks: BackgroundTasks,
    force: bool = Query(False),
    x_api_key: Optional[str] = Header(None),
):
    _check_api_key(x_api_key)
    background_tasks.add_task(ingest_all, force)
    return {"status": "started"}

def _run_ingest(d: date, force: bool):
    try:
        result = ingest_date(d, force=force)
        log.info(f"Ingest completato: {result}")
    except Exception as e:
        log.error(f"Ingest fallito per {d}: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
