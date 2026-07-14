"""
Controlli di plausibilità sul programmato — stessa filosofia delle ancore
post-ingest dell'app ascolti: il dato deve reggere contro cosa ci si aspetta
dal mondo, non solo essere internamente coerente.

Il meccanismo d'artefatto che questi controlli intercettano è sempre lo
stesso: l'OCR incolla un'annotazione datata nel titolo, la data diventa
finestra di validità, e un programma quotidiano risulta in onda un giorno
solo (Storie Italiane [6/1→6/1], TV7 [3/4→3/4], Don Matteo 15 [8/1→8/1]).

Regola A  — slot con dow_mask larga (>= DOW_LARGA_MIN giorni) e finestra di
            validità di 1-2 giorni: quasi certamente artefatto.
Regola A' — slot con QUALUNQUE maschera e finestra di UN giorno (da == a):
            un settimanale che va in onda una volta sola è sospetto uguale.
Regola B  — eccezione 'solo' con meno di SOLO_MIN_DATE date su dow_mask
            larga: un "escl." letto come "solo" inverte la semantica
            (il caso CINQUE MINUTI).

Esenzioni: kind='puntuale' (datati per natura), note.sub_box (one-off
dichiarati), note.curato (già passati dalla curatela umana), gruppo_alt
(le alternative datate sono la norma: FILM il 6.4 / TORRE il 13.4).
NOTA il limite: quando l'artefatto vive DENTRO un gruppo di alternanza
(CINQUE MINUTI inverno), l'esenzione gruppo lo maschera — ma quei casi
arrivano in curatela comunque via confidenza OCR / titolo sospetto.

I flag NON correggono nulla (la semantica è della curatela): aggiungono
note.finestra_implausibile + lettura_incerta, e la riga entra nella lista
`curatela` da sola — senza aspettare che la scopra un occhio umano.
"""
import json

DOW_LARGA_MIN = 4                 # giorni attivi per dire "quotidiana/feriale"
FINESTRA_ARTEFATTO_MAX_GIORNI = 2
SOLO_MIN_DATE = 4


def segna_finestre_implausibili(conn, doc_id: str | None = None) -> int:
    where, par = "s.kind = 'base'", []
    if doc_id:
        where += " AND s.doc_id = ?"
        par.append(doc_id)
    rows = conn.execute(f"""
        SELECT s.slot_id, s.dow_mask, s.valido_da, s.valido_a, s.gruppo_alt,
               s.note,
               (SELECT e.date_list FROM slot_eccezione e
                WHERE e.slot_id = s.slot_id AND e.tipo = 'solo' LIMIT 1)
        FROM slot_programmato s WHERE {where}""", par).fetchall()
    n = 0
    for sid, mask, va, vb, gruppo, note_raw, solo_raw in rows:
        note = json.loads(note_raw or "{}")
        if gruppo or note.get("sub_box") or note.get("curato") \
                or note.get("finestra_implausibile"):
            continue
        larga = (mask or "").count("1") >= DOW_LARGA_MIN
        motivo = None
        if va and vb:
            giorni = (vb - va).days + 1
            if larga and giorni <= FINESTRA_ARTEFATTO_MAX_GIORNI:
                motivo = f"finestra {giorni}g su maschera {mask}"
            elif giorni == 1:
                motivo = f"finestra di 1 giorno ({va}) senza alternanza"
        if motivo is None and larga and solo_raw:
            n_date = len(json.loads(solo_raw))
            if n_date < SOLO_MIN_DATE:
                motivo = (f"'solo' con {n_date} date su maschera {mask}: "
                          "possibile 'escl.' letto come 'solo'")
        if motivo:
            note["finestra_implausibile"] = motivo
            note["lettura_incerta"] = True
            conn.execute("UPDATE slot_programmato SET note = ? WHERE slot_id = ?",
                         [json.dumps(note), sid])
            n += 1
    return n
