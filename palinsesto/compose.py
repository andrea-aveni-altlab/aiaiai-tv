"""
Composizione del palinsesto programmato di un giorno: base + eccezioni + overlay.
Contratto v2 (validato): risoluzione a livello SLOT, mai a livello documento.
"""
import json
from datetime import date, timedelta

DOW = "DLMMGVS"          # indice 0=domenica ... 6=sabato (come le griglie)
CONCESSIONARIA_DI = {"RAI1": "rai", "RAI2": "rai", "RAI3": "rai", "LA7": "cairo",
                     "CAN5": "publitalia", "ITA1": "publitalia", "RETE4": "publitalia"}


def _dow_idx(giorno: date) -> int:
    return (giorno.weekday() + 1) % 7        # lun=0 python → dom=0 nostra maschera


def _si_sovrappone(a, b) -> bool:
    """Conflitto tra due slot: stessa rete e overlap orario; se uno dei due non ha
    orario, confronto per fascia."""
    if a["rete"] != b["rete"]:
        return False
    if a["t_start"] is not None and b["t_start"] is not None:
        a_end = a["t_end"] if a["t_end"] is not None else 26 * 3600
        b_end = b["t_end"] if b["t_end"] is not None else 26 * 3600
        return a["t_start"] < b_end and b["t_start"] < a_end
    return a["fascia"] == b["fascia"]


def _eccezioni_ammesse(conn, orizzonte):
    q = """SELECT e.slot_id, e.target_rete, e.target_fascia, e.tipo,
                  e.data_da, e.data_a, e.dow_mask, e.date_list
           FROM slot_eccezione e JOIN doc_sorgente d USING (doc_id)"""
    if orizzonte:
        q += " WHERE d.pubblicato_il <= ?"
        return conn.execute(q, [orizzonte]).fetchall()
    return conn.execute(q).fetchall()


def _eccezione_copre(ecc, giorno: date) -> bool:
    _, _, _, _, da, a, dm, dl = ecc
    if dl:
        return giorno.isoformat() in json.loads(dl)
    if da and giorno < da:
        return False
    if a and giorno > a:
        return False
    if dm and dm[_dow_idx(giorno)] != "1":
        return False
    return bool(da or a or dm)


def palinsesto_del_giorno(conn, giorno: date, rete: str | None = None,
                          orizzonte: date | None = None) -> list[dict]:
    par_rete, par_oriz = [], []
    where_rete = ""
    if rete:
        where_rete = " AND s.rete = ?"
        par_rete = [rete]
    where_oriz = ""
    if orizzonte:
        where_oriz = " AND d.pubblicato_il <= ?"
        par_oriz = [orizzonte]

    # ── STRATO 1: base — per (concessionaria, rete) vince la griglia più recente ──
    base = conn.execute(f"""
        WITH ammessi AS (
            SELECT d.*, row_number() OVER (
                PARTITION BY d.concessionaria ORDER BY d.pubblicato_il DESC, d.doc_id DESC
            ) AS rn
            FROM doc_sorgente d
            WHERE d.tipo_doc IN ('griglia','listino_griglia')
              AND ? BETWEEN d.periodo_da AND d.periodo_a {where_oriz.replace('d.', 'd.')}
        )
        SELECT s.slot_id, s.rete, s.t_start, s.t_end, s.t_end_aperto, s.fascia,
               s.blocco_id, s.titolo_grezzo, s.generico, s.gruppo_alt,
               s.prima_tv, s.replica, s.tipo, s.note, s.valido_da, s.valido_a,
               a.doc_id, a.pubblicato_il
        FROM slot_programmato s JOIN ammessi a USING (doc_id)
        WHERE a.rn = 1 AND s.kind = 'base'
          AND substr(s.dow_mask, ? + 1, 1) = '1'
          AND ? BETWEEN coalesce(s.valido_da, a.periodo_da)
                    AND coalesce(s.valido_a, a.periodo_a)
          {where_rete}
    """, [giorno, *par_oriz, _dow_idx(giorno), giorno, *par_rete]).fetchall()

    cols = ["slot_id","rete","t_start","t_end","t_end_aperto","fascia","blocco_id",
            "titolo","generico","gruppo_alt","prima_tv","replica","tipo","note",
            "valido_da","valido_a","doc_id","pubblicato_il"]
    attivi = [dict(zip(cols, r)) for r in base]

    # eccezioni (filtrate per orizzonte via il LORO doc): escluso toglie, solo INTERSECA
    eccs = _eccezioni_ammesse(conn, orizzonte)
    per_slot = {}
    for e in eccs:
        per_slot.setdefault(e[0], []).append(e)
    filtrati = []
    for s in attivi:
        mie = per_slot.get(s["slot_id"], [])
        if any(e[3] == "escluso" and _eccezione_copre(e, giorno) for e in mie):
            continue
        soli = [e for e in mie if e[3] == "solo"]
        if soli and not any(_eccezione_copre(e, giorno) for e in soli):
            continue
        filtrati.append(s)
    attivi = filtrati

    # alternanze: nel gruppo la finestra più stretta vince; >1 superstite = irrisolta
    per_gruppo = {}
    for s in attivi:
        if s["gruppo_alt"]:
            per_gruppo.setdefault((s["doc_id"], s["gruppo_alt"]), []).append(s)
    scarta = set()
    for gruppo in per_gruppo.values():
        if len(gruppo) < 2:
            continue
        def ampiezza(s):
            da = s["valido_da"] or date(2000, 1, 1)
            a = s["valido_a"] or date(2099, 1, 1)
            return (a - da).days
        minima = min(ampiezza(s) for s in gruppo)
        stretti = [s for s in gruppo if ampiezza(s) == minima]
        if len(stretti) < len(gruppo) and len(stretti) >= 1 and minima < 3650:
            for s in gruppo:
                if s not in stretti:
                    scarta.add(s["slot_id"])
            gruppo = stretti
        if len(gruppo) > 1:
            for s in gruppo:
                s["alternanza_irrisolta"] = True
    attivi = [s for s in attivi if s["slot_id"] not in scarta]

    # specificità base-vs-base: finestra di validità più corta sopprime la più lunga
    soppressi = set()
    for a in attivi:
        for b in attivi:
            if a is b or a["slot_id"] in soppressi or b["slot_id"] in soppressi:
                continue
            if _si_sovrappone(a, b):
                da_a = (a["valido_a"] or date(2099,1,1)) - (a["valido_da"] or date(2000,1,1))
                da_b = (b["valido_a"] or date(2099,1,1)) - (b["valido_da"] or date(2000,1,1))
                if da_a.days < da_b.days - 2:
                    soppressi.add(b["slot_id"])
    attivi = [s for s in attivi if s["slot_id"] not in soppressi]
    for s in attivi:
        s["certezza_contenuto"] = "derivato"
        s["certezza_orario"] = "dichiarato" if s["t_start"] is not None else "solo_fascia"

    # ── STRATO 2: overlay puntuale, in ordine di pubblicazione (l'ultimo vince) ──
    punt = conn.execute(f"""
        SELECT s.slot_id, s.rete, s.t_start, s.t_end, s.t_end_aperto, s.fascia,
               s.blocco_id, s.titolo_grezzo, s.generico, s.gruppo_alt,
               s.prima_tv, s.replica, s.tipo, s.note, NULL, NULL,
               d.doc_id, d.pubblicato_il
        FROM slot_programmato s JOIN doc_sorgente d USING (doc_id)
        WHERE s.kind = 'puntuale' AND s.data = ? {where_oriz} {where_rete}
        ORDER BY d.pubblicato_il, d.doc_id
    """, [giorno, *par_oriz, *par_rete]).fetchall()

    for r in punt:
        o = dict(zip(cols, r))
        o["certezza_contenuto"] = "puntuale"
        o["certezza_orario"] = "dichiarato" if o["t_start"] is not None else "solo_fascia"
        vittime = [s for s in attivi if _si_sovrappone(o, s)
                   and not (s["certezza_contenuto"] == "puntuale"
                            and json.loads(s["note"] or "{}").get("sequenza") is not None
                            and json.loads(o["note"] or "{}").get("sequenza") is not None
                            and s["doc_id"] == o["doc_id"])]
        if o["t_start"] is None:
            con_orario = [v for v in vittime if v["t_start"] is not None]
            if con_orario:
                o["t_start"] = min(v["t_start"] for v in con_orario)
                o["t_end"] = max(v["t_end"] or v["t_start"] for v in con_orario)
                o["certezza_orario"] = "ereditato"
        attivi = [s for s in attivi if s not in vittime]
        attivi.append(o)

    out = []
    for s in sorted(attivi, key=lambda s: (s["rete"], s["t_start"] if s["t_start"] is not None else 10**9)):
        note = json.loads(s["note"] or "{}")
        out.append({
            "giorno": giorno.isoformat(), "rete": s["rete"],
            "t_start": s["t_start"], "t_end": s["t_end"], "fascia": s["fascia"],
            "blocco_id": s["blocco_id"], "titolo": s["titolo"],
            "certezza_contenuto": s["certezza_contenuto"],
            "certezza_orario": s["certezza_orario"],
            "generico": bool(s["generico"]),
            "alternanza_irrisolta": bool(s.get("alternanza_irrisolta", False)),
            "prima_tv": s["prima_tv"], "replica": s["replica"], "tipo": s["tipo"],
            "genere": note.get("genere_colore"),
            "doc_id": s["doc_id"], "pubblicato_il": str(s["pubblicato_il"]),
        })
    return out


def costruisci_cache(conn, da: date, a: date, orizzonte: date | None = None,
                     label: str | None = None) -> int:
    """Materializza palinsesto_composto per l'intervallo. Cache rigenerabile.
    alternanza_irrisolta viaggia fino alla cache (requisito esplicito)."""
    label = label or (orizzonte.isoformat() if orizzonte else "pieno")
    conn.execute("DELETE FROM palinsesto_composto WHERE orizzonte_label = ? AND giorno BETWEEN ? AND ?",
                 [label, da, a])
    n, g = 0, da
    while g <= a:
        for r in palinsesto_del_giorno(conn, g, orizzonte=orizzonte):
            conn.execute("INSERT OR REPLACE INTO palinsesto_composto VALUES "
                         "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                label, g, r["rete"], r["t_start"], r["t_end"], r["fascia"],
                r["blocco_id"], r["titolo"], r["certezza_contenuto"], r["certezza_orario"],
                r["generico"], r["alternanza_irrisolta"], r["prima_tv"], r["replica"],
                r["tipo"], r["genere"], r["doc_id"], r["pubblicato_il"]])
            n += 1
        g += timedelta(days=1)
    return n
