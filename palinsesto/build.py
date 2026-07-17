"""CLI del DB palinsesto.
  python -m palinsesto.build init
  python -m palinsesto.build parse-pt <dir_settimanali>
  python -m palinsesto.build parse-cairo <pdf_o_dir> [...]
  python -m palinsesto.build parse-rai <pdf> [...]
  python -m palinsesto.build giorno YYYY-MM-DD [--rete X] [--orizzonte YYYY-MM-DD]
  python -m palinsesto.build cache YYYY-MM-DD YYYY-MM-DD [--orizzonte ...]
  python -m palinsesto.build rubriche          # registro rubrica_listino da CSV+previsione
  python -m palinsesto.build match-rubriche    # matcher programma→rubrica → match_rubrica
  python -m palinsesto.build curatela          # lista slot da correggere
  python -m palinsesto.build plausibilita [doc] # flagga finestre implausibili
  python -m palinsesto.build applica-curatela  # applica curatela_slot.csv
  python -m palinsesto.build report
"""
import sys
from datetime import date
from pathlib import Path

from . import db
from .compose import palinsesto_del_giorno, costruisci_cache


def main(argv=None):
    args = list(argv or sys.argv[1:])
    cmd = args.pop(0) if args else "report"
    conn = db.connect()

    if cmd == "init":
        print(f"schema inizializzato in {db.DB_PATH}")

    elif cmd == "parse-pt":
        from .parsers.settimanale_pt import parse_settimanale
        src = Path(args[0])
        tot = 0
        for f in sorted(src.glob("pt_*.pdf")):
            r = parse_settimanale(f, conn)
            tot += r["slot"]
            print(f"  {f.name}: {r['periodo'][0]}..{r['periodo'][1]} "
                  f"pubblicato={r['pubblicato']} slot={r['slot']}")
        print(f"totale slot: {tot}")

    elif cmd == "parse-cairo":
        from .parsers.griglia_cairo import parse_griglia
        from .qa import segna_finestre_implausibili
        files = []
        for a in args:
            p = Path(a)
            files += sorted(p.glob("cairo_la7_*.pdf")) if p.is_dir() else [p]
        for f in files:
            r = parse_griglia(f, conn)
            nq = segna_finestre_implausibili(conn, r["doc_id"])
            print(f"  {f.name}: {r['periodo'][0]}..{r['periodo'][1]} "
                  f"pubblicato={r['pubblicato']} celle={r['celle']} slot={r['slot']}"
                  f" finestre_implausibili={nq}")

    elif cmd == "parse-rai":
        from .parsers.griglia_rai import parse_griglia_rai
        from .qa import segna_finestre_implausibili
        for a in args:
            r = parse_griglia_rai(Path(a), conn)
            nq = segna_finestre_implausibili(conn, r["doc_id"])
            print(f"  {Path(a).name}: {r['periodo'][0]}..{r['periodo'][1]} "
                  f"pubblicato={r['pubblicato']} slot={r['slot']} {r['per_rete']}"
                  f" finestre_implausibili={nq}")

    elif cmd == "parse-listino":
        from .parsers.listino_publitalia import parse_listino
        for a in args:
            r = parse_listino(Path(a), conn)
            print(f"  {Path(a).name}: {r['periodo'][0]}..{r['periodo'][1]} "
                  f"pubblicato={r['pubblicato']} v={r['versione']} "
                  f"slot={r['slot']} {r['per_rete']} previsioni={r['previsioni']}")

    elif cmd == "rubriche":
        from .matcher_rubrica import ricostruisci_rubriche
        r = ricostruisci_rubriche(conn)
        print(f"rubrica_listino: {r['rai']} rai + {r['publitalia']} publitalia "
              f"+ {r['cairo']} cairo")

    elif cmd == "match-rubriche":
        from .matcher_rubrica import esegui_match
        r = esegui_match(conn)
        print(f"match_rubrica: {r['righe_match']} righe "
              f"({r['rubriche_collegate']} rubriche collegate, "
              f"{r['kpi_ok']} righe usabili per KPI)")
        print(f"  per livello: {dict(r['per_livello'])}")
        print(f"  per metodo:  {dict(r['per_metodo'])}")
        print(f"  fuori fase 1: {r['fuori_fase1']}")
        print(f"  senza match (curatela): {r['senza_match']} -> {r['curatela']}")

    elif cmd == "giorno":
        g = date.fromisoformat(args.pop(0))
        rete = oriz = None
        while args:
            a = args.pop(0)
            if a == "--rete": rete = args.pop(0)
            elif a == "--orizzonte": oriz = date.fromisoformat(args.pop(0))
        for r in palinsesto_del_giorno(conn, g, rete=rete, orizzonte=oriz):
            hh = (f"{r['t_start']//3600:02d}:{r['t_start']%3600//60:02d}"
                  if r["t_start"] is not None else "  -  ")
            flags = "".join([
                "P" if r["prima_tv"] else "", "R" if r["replica"] else "",
                "g" if r["generico"] else "", "?" if r["alternanza_irrisolta"] else "",
                "L" if r.get("lettura_incerta") else ""])
            print(f"{r['rete']:6} {hh} {r['fascia']:14} {r['titolo'][:44]:44} "
                  f"[{r['certezza_contenuto'][:4]}/{r['certezza_orario'][:6]}] "
                  f"{r['tipo'] or ''}{' ' + r['genere'] if r['genere'] else ''} {flags}")

    elif cmd == "cache":
        da, a = date.fromisoformat(args[0]), date.fromisoformat(args[1])
        oriz = date.fromisoformat(args[3]) if len(args) > 3 else None
        print(f"righe cache: {costruisci_cache(conn, da, a, orizzonte=oriz)}")

    elif cmd == "plausibilita":
        from .qa import segna_finestre_implausibili
        n = segna_finestre_implausibili(conn, args[0] if args else None)
        print(f"finestre implausibili flaggate: {n}")

    elif cmd == "applica-curatela":
        # applica palinsesto/curatela_slot.csv (SOLO umano, non rigenerato):
        # correzioni idempotenti, da rilanciare dopo ogni re-parse.
        # Campi vuoti = non toccare; '-' = azzera. azione: ''=correggi |
        # elimina | nuovo (usa doc_id/rete/dow_mask/t_min per l'INSERT).
        import csv as _csv
        import json as _json
        from .db import fascia_di as _fascia
        f = Path(__file__).parent / "curatela_slot.csv"
        n_upd = n_del = n_new = n_miss = 0
        for r in _csv.DictReader(f.open(), delimiter=";"):
            sid = r["slot_id"].strip()
            az = (r.get("azione") or "").strip().lower()
            if az == "nuovo":
                t1 = int(r["t_start_min"]) * 60
                conn.execute("""INSERT OR REPLACE INTO slot_programmato
                    (slot_id, doc_id, rete, kind, dow_mask, valido_da, valido_a,
                     t_start, t_end, fascia, titolo_grezzo, generico, gruppo_alt,
                     prima_tv, replica, tipo, note)
                    VALUES (?,?,?,'base',?,?,?,?,?,?,?,FALSE,NULL,NULL,?,NULL,?)""", [
                    sid, r["doc_id"], r["rete"], r["dow_mask"],
                    r["valido_da"] or None, r["valido_a"] or None,
                    t1, int(r["t_end_min"]) * 60,
                    _fascia(conn, "rai", t1), r["titolo"],
                    r.get("replica", "").strip().lower() == "true",
                    _json.dumps({"curato": True})])
                n_new += 1
                continue
            row = conn.execute(
                "SELECT note FROM slot_programmato WHERE slot_id = ?", [sid]).fetchone()
            if row is None:
                print(f"  MANCANTE (re-parse ha cambiato gli id?): {sid}")
                n_miss += 1
                continue
            if az == "elimina":
                conn.execute("DELETE FROM slot_eccezione WHERE slot_id = ?", [sid])
                conn.execute("DELETE FROM slot_programmato WHERE slot_id = ?", [sid])
                n_del += 1
                continue
            # riga senza correzioni = non ancora curata: NON toccare il flag
            # (resta in lista finche' qualcuno non la corregge davvero)
            if not any((r.get(c) or "").strip() for c in
                       ("titolo", "valido_da", "valido_a", "solo", "escluso",
                        "replica")):
                continue
            note = _json.loads(row[0] or "{}")
            note.pop("lettura_incerta", None)
            note.pop("finestra_illeggibile", None)
            note.pop("finestra_implausibile", None)
            note["curato"] = True
            if r.get("nota"):
                note["curatela_nota"] = r["nota"]
            set_sql, par = ["note = ?"], [_json.dumps(note)]
            if r["titolo"].strip():
                set_sql.append("titolo_grezzo = ?")
                par.append(r["titolo"].strip())
            for campo in ("valido_da", "valido_a"):
                v = (r.get(campo) or "").strip()
                if v == "-":
                    set_sql.append(f"{campo} = NULL")
                elif v:
                    set_sql.append(f"{campo} = ?")
                    par.append(v)
            if (r.get("replica") or "").strip():
                set_sql.append("replica = ?")
                par.append(r["replica"].strip().lower() == "true")
            conn.execute(f"UPDATE slot_programmato SET {', '.join(set_sql)} "
                         "WHERE slot_id = ?", par + [sid])
            for tipo in ("solo", "escluso"):
                v = (r.get(tipo) or "").strip()
                if not v:
                    continue
                conn.execute("DELETE FROM slot_eccezione WHERE slot_id = ? AND tipo = ?",
                             [sid, tipo])
                if v != "-":
                    doc = conn.execute("SELECT doc_id FROM slot_programmato "
                                       "WHERE slot_id = ?", [sid]).fetchone()[0]
                    conn.execute("""INSERT OR REPLACE INTO slot_eccezione
                        (ecc_id, doc_id, slot_id, tipo, date_list)
                        VALUES (?,?,?,?,?)""", [
                        f"{sid}:cur:{tipo}", doc, sid, tipo,
                        _json.dumps([d.strip() for d in v.split(",")])])
            n_upd += 1
        print(f"curatela: corretti={n_upd} eliminati={n_del} nuovi={n_new} mancanti={n_miss}")

    elif cmd == "curatela":
        # slot con lettura incerta (OCR/parsing): da correggere a mano, non
        # sono incertezze di palinsesto
        righe = conn.execute("""
            SELECT s.doc_id, s.rete, s.dow_mask, s.t_start, s.titolo_grezzo,
                   json_extract_string(s.note, '$.ocr_conf')            AS conf,
                   json_extract_string(s.note, '$.finestra_illeggibile') AS fin
            FROM slot_programmato s
            WHERE json_extract_string(s.note, '$.lettura_incerta') = 'true'
            ORDER BY s.doc_id, s.rete, s.t_start""").fetchall()
        for d, rete, dm, ts, tit, conf, fin in righe:
            hh = f"{ts // 3600:02d}:{ts % 3600 // 60:02d}"
            motivo = " ".join(filter(None, [
                f"conf={conf}" if conf else None,
                "FINESTRA ILLEGGIBILE" if fin == "true" else None]))
            print(f"  {d.replace('rai_tvprogram_', '')} {rete:5} {dm} {hh} "
                  f"{tit[:52]:52} {motivo}")
        print(f"totale da curare: {len(righe)}")

    elif cmd == "report":
        for r in conn.execute("""
            SELECT concessionaria, tipo_doc, COUNT(*) docs,
                   MIN(periodo_da), MAX(periodo_a),
                   (SELECT COUNT(*) FROM slot_programmato s
                    JOIN doc_sorgente d2 USING (doc_id)
                    WHERE d2.concessionaria = d.concessionaria
                      AND d2.tipo_doc = d.tipo_doc) slot
            FROM doc_sorgente d GROUP BY 1, 2 ORDER BY 1, 2""").fetchall():
            print(f"  {r[0]:11} {r[1]:16} doc={r[2]:3}  {r[3]} -> {r[4]}  slot={r[5]}")
    else:
        print(__doc__)
    conn.close()


if __name__ == "__main__":
    main()
