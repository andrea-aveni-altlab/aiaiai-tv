"""CLI del DB palinsesto.
  python -m palinsesto.build init
  python -m palinsesto.build parse-pt <dir_settimanali>
  python -m palinsesto.build parse-cairo <pdf_o_dir> [...]
  python -m palinsesto.build parse-rai <pdf> [...]
  python -m palinsesto.build giorno YYYY-MM-DD [--rete X] [--orizzonte YYYY-MM-DD]
  python -m palinsesto.build cache YYYY-MM-DD YYYY-MM-DD [--orizzonte ...]
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
        files = []
        for a in args:
            p = Path(a)
            files += sorted(p.glob("cairo_la7_*.pdf")) if p.is_dir() else [p]
        for f in files:
            r = parse_griglia(f, conn)
            print(f"  {f.name}: {r['periodo'][0]}..{r['periodo'][1]} "
                  f"pubblicato={r['pubblicato']} celle={r['celle']} slot={r['slot']}")

    elif cmd == "parse-rai":
        from .parsers.griglia_rai import parse_griglia_rai
        for a in args:
            r = parse_griglia_rai(Path(a), conn)
            print(f"  {Path(a).name}: {r['periodo'][0]}..{r['periodo'][1]} "
                  f"pubblicato={r['pubblicato']} slot={r['slot']} {r['per_rete']}")

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
                "g" if r["generico"] else "", "?" if r["alternanza_irrisolta"] else ""])
            print(f"{r['rete']:6} {hh} {r['fascia']:14} {r['titolo'][:44]:44} "
                  f"[{r['certezza_contenuto'][:4]}/{r['certezza_orario'][:6]}] "
                  f"{r['tipo'] or ''}{' ' + r['genere'] if r['genere'] else ''} {flags}")

    elif cmd == "cache":
        da, a = date.fromisoformat(args[0]), date.fromisoformat(args[1])
        oriz = date.fromisoformat(args[3]) if len(args) > 3 else None
        print(f"righe cache: {costruisci_cache(conn, da, a, orizzonte=oriz)}")

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
