"""
API di scrittura per la tabella previsione — pensata perché il modello aiaiai
ci scriva DAL PRIMO RUN, non come ripensamento.

Uso dal modello:
    from palinsesto import db, previsioni
    conn = db.connect()
    previsioni.registra(conn,
        sorgente="aiaiai", versione="v0.1-2026w29",
        pubblicato_il=date(2026, 7, 20),
        righe=[{"grana": "giorno", "periodo_da": "2026-05-12", "periodo_a": "2026-05-12",
                "rete": "RAI1", "posizione": "AFFARI TUOI", "blocco_id": "affari_tuoi",
                "target": "individui", "metrica": "amr_migliaia", "valore": 5450.0}])
Le dimensioni target/metrica si auto-registrano se nuove (upsert), così una
sorgente con vocabolario proprio entra subito e si riconcilia dopo.
"""
from datetime import date


def registra(conn, sorgente: str, versione: str, pubblicato_il: date,
             righe: list[dict], doc_id: str | None = None) -> int:
    n = 0
    for r in righe:
        conn.execute("INSERT INTO target VALUES (?,?,?) ON CONFLICT DO NOTHING",
                     [r["target"], r.get("target_label", r["target"]), None])
        conn.execute("INSERT INTO metrica VALUES (?,?) ON CONFLICT DO NOTHING",
                     [r["metrica"], r.get("unita")])
        # tipo_giorno/rete/posizione sono nella PK (NOT NULL implicito):
        # sentinelle esplicite al posto di NULL, cosi' l'API non inciampa
        conn.execute("""INSERT OR REPLACE INTO previsione VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
            sorgente, versione, doc_id, pubblicato_il,
            r.get("grana", "giorno"), r.get("periodo_label"),
            r["periodo_da"], r["periodo_a"], r.get("tipo_giorno") or "tutti",
            r.get("rete") or "", r.get("posizione") or "", r.get("blocco_id"),
            r["target"], r["metrica"], float(r["valore"])])
        n += 1
    return n


def confronto(conn, periodo_da: date, periodo_a: date,
              target: str | None = None) -> list[dict]:
    """Tutte le sorgenti sullo stesso asse (blocco/rete/periodo/target)."""
    q = """SELECT sorgente, versione_sorgente, grana, periodo_da, periodo_a,
                  tipo_giorno, rete, posizione, blocco_id, target_id, metrica_id, valore
           FROM previsione
           WHERE periodo_da <= ? AND periodo_a >= ?"""
    par = [periodo_a, periodo_da]
    if target:
        q += " AND target_id = ?"
        par.append(target)
    cols = ["sorgente","versione","grana","periodo_da","periodo_a","tipo_giorno",
            "rete","posizione","blocco_id","target","metrica","valore"]
    return [dict(zip(cols, r)) for r in conn.execute(q + " ORDER BY sorgente, periodo_da", par).fetchall()]
