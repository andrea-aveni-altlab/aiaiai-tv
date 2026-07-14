"""
Parser dei settimanali "Palinsesto Prime Time" Publitalia (pt_YYYY_wNN.pdf).

Struttura (verificata su pt_2026_w15): 4 pagine; la pagina 0 copre le tre
generaliste in colonne fisse — Canale 5 (x 140-355), Italia 1 (x 360-570),
Rete 4 (x 575-785); le pagine 1-3 sono reti tematiche (fuori perimetro, per ora).
Righe: per ogni giorno (etichetta DOMENICA..SABATO a sinistra + numero) due
fasce ancorate dalle etichette 'prime time' / 'seconda serata'.
Ogni titolo del prime porta il GENERE nel colore del riquadro (legenda in
testata: FILM blu scuro, FICTION/SERIE azzurro, PRODUZIONI arancio, SPORT verde).
Metadati testuali: '1a TV' → prima_tv; 'R' finale → replica; '(Film)/(Tf)/(Doc)'
→ tipo. kind='puntuale': ogni riga è dichiarata per la singola data.
Il '/' dentro una cella = sequenza nella fascia (PRESSING/TG5), non alternanza.
pubblicato_il: PDF CreationDate (fonte 'pdf_meta').
"""
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

RETI_P0 = [("CAN5", 140, 357), ("ITA1", 360, 572), ("RETE4", 575, 790)]
GIORNI = ["DOMENICA", "LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO"]
MESI = {m: i + 1 for i, m in enumerate(
    ["GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
     "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"])}
GENERE_LEGENDA = {  # colori legenda (pagina 0, testata)
    (0.0, 0.31, 0.545): "film",
    (0.118, 0.561, 0.859): "fiction_serie",
    (0.91, 0.388, 0.0): "produzioni",
    (0.0, 0.502, 0.0): "sport",
}


def _norm_color(c):
    if c is None:
        return None
    if isinstance(c, (int, float)):
        return (float(c),)
    return tuple(round(float(x), 3) for x in c)


def _genere_da_colore(fill):
    fill = _norm_color(fill)
    if not fill or len(fill) != 3:
        return None
    best, bd = None, 0.15
    for ref, g in GENERE_LEGENDA.items():
        d = sum((a - b) ** 2 for a, b in zip(fill, ref)) ** 0.5
        if d < bd:
            best, bd = g, d
    return best


def _parse_header_dates(text: str) -> tuple[date, date]:
    """'Settimana 12 - 18 APRILE 2026' | '28 Dicembre 2025 - 3 Gennaio 2026'."""
    t = text.upper().replace("–", "-")
    m = re.search(
        r"SETTIMANA\s+(\d{1,2})\s*(?:([A-ZÀ-Ù]+))?\s*(?:(\d{4}))?\s*-\s*(\d{1,2})\s+([A-ZÀ-Ù]+)\s+(\d{4})", t)
    if not m:
        raise ValueError(f"header settimana non riconosciuto: {text[:80]!r}")
    d1, m1, y1, d2, m2, y2 = m.groups()
    mese_b, anno_b = MESI[m2], int(y2)
    mese_a = MESI[m1] if m1 and m1 in MESI else None
    anno_a = int(y1) if y1 else anno_b
    fine = date(anno_b, mese_b, int(d2))
    if mese_a is None:            # stesso mese di fine
        inizio = date(anno_b, mese_b, int(d1))
    else:
        inizio = date(anno_a if anno_a else anno_b, mese_a, int(d1))
        if inizio > fine:         # anno a cavallo senza anno esplicito
            inizio = inizio.replace(year=fine.year - 1)
    assert (fine - inizio).days == 6, f"settimana non di 7 giorni: {inizio}..{fine}"
    return inizio, fine


def _pulisci_titolo(txt: str) -> tuple[str, dict]:
    meta = {"prima_tv": False, "replica": False, "tipo": None}
    t = " ".join(txt.split())
    if re.search(r"\b1[aª]\s*TV\b", t, re.I):
        meta["prima_tv"] = True
        t = re.sub(r"\s*\b1[aª]\s*TV\b", "", t, flags=re.I)
    m = re.search(r"\((FILM|TF|DOC|MINIS|SERIE)\)", t, re.I)
    if m:
        meta["tipo"] = m.group(1).lower()
        t = t[: m.start()].strip() + t[m.end():]
    # 'R' (replica) come token isolato in coda o — per artefatti di rendering —
    # in testa alla cella ('REALPOLITIK R' estratto come 'R REALPOLITIK')
    if re.search(r"\sR$", t):
        meta["replica"] = True
        t = t[:-2].strip()
    elif re.match(r"^R\s", t):
        meta["replica"] = True
        t = t[2:].strip()
    return t.strip(" -"), meta


def parse_settimanale(path: Path, conn) -> dict:
    """Parsa un settimanale PT e inserisce doc + slot puntuali. Idempotente."""
    doc_id = "pub_pt_" + path.stem.replace("pt_", "")
    meta_pdf = PdfReader(str(path)).metadata or {}
    created = str(meta_pdf.get("/CreationDate", ""))
    m = re.match(r"D:(\d{8})", created)
    pubblicato = (datetime.strptime(m.group(1), "%Y%m%d").date()
                  if m else None)

    with pdfplumber.open(str(path)) as pdf:
        pg = pdf.pages[0]
        header = (pg.extract_text() or "").split("\n")[1]
        inizio, fine = _parse_header_dates(header)
        if pubblicato is None:
            pubblicato = inizio - timedelta(days=5)   # fallback prudente

        words = pg.extract_words()
        # ancore giorno: etichetta a sinistra (x<120)
        day_rows = []
        for w in words:
            nome = w["text"].rstrip("'’").upper()
            if w["x0"] < 120 and nome in GIORNI:
                day_rows.append((w["top"], nome))
        day_rows.sort()
        # ancore fascia
        fascia_rows = sorted(
            (w["top"], "prime" if w["text"].lower() == "prime" else "seconda_serata")
            for w in words if w["x0"] < 120 and w["text"].lower() in ("prime", "seconda"))
        # riquadri colorati (genere) per il prime
        rects = [r for r in pg.rects
                 if r.get("fill") and _genere_da_colore(r.get("non_stroking_color"))
                 and r["height"] > 20]

        def day_of(top):
            prev = [d for d in day_rows if d[0] <= top + 6]
            return prev[-1][1] if prev else None

        def fascia_of(top):
            best = min(fascia_rows, key=lambda f: abs(f[0] - top), default=None)
            return best[1] if best and abs(best[0] - top) < 16 else None

        # raggruppa parole in RIGHE fisiche per (giorno, colonna)
        righe_fis = {}
        for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
            if w["x0"] < 130 or w["top"] < 55:
                continue
            col = next((r for r, a, b in RETI_P0 if a <= w["x0"] < b), None)
            g = day_of(w["top"])
            if not col or not g:
                continue
            key = (g, col, round(w["top"] / 6))
            righe_fis.setdefault(key, {"top": w["top"], "parole": []})["parole"].append(w["text"])

        # assegna ogni riga fisica alla fascia (l'ancora piu' vicina; le righe di
        # continuazione senza ancora ereditano la fascia della riga precedente)
        per_cella = {}          # (giorno, col, fascia) -> [ (top, testo) ]
        ultima_fascia = {}
        for (g, col, _), rf in sorted(righe_fis.items(), key=lambda kv: (kv[0][1], kv[1]["top"])):
            fascia = fascia_of(rf["top"]) or ultima_fascia.get((g, col))
            if fascia is None:
                continue
            ultima_fascia[(g, col)] = fascia
            per_cella.setdefault((g, col, fascia), []).append((rf["top"], " ".join(rf["parole"])))

        # una cella = tutte le sue righe unite; l'UNICO separatore di titoli
        # multipli nella fonte e' '/' (verificato: i ritorni a capo dei titoli
        # lunghi non portano mai '/', i titoli distinti si')
        rows = []
        for (g, col, fascia), linee in per_cella.items():
            linee.sort()
            rows.append({"giorno": g, "rete": col, "fascia": fascia,
                         "top": linee[0][0],
                         "testo": " ".join(txt for _, txt in linee)})

        # genere dal colore del riquadro prime nella stessa colonna/riga
        def genere_at(top, col):
            a, b = next((a, b) for r, a, b in RETI_P0 if r == col)
            for r in rects:
                if abs(r["top"] - top) < 14 and r["x0"] < b and r["x1"] > a:
                    return _genere_da_colore(r.get("non_stroking_color"))
            return None

        conn.execute("DELETE FROM slot_programmato WHERE doc_id = ?", [doc_id])
        conn.execute("DELETE FROM doc_sorgente WHERE doc_id = ?", [doc_id])
        conn.execute("INSERT INTO doc_sorgente VALUES (?,?,?,?,?,?,?,?,?)", [
            doc_id, "publitalia", "settimanale_pt", str(path),
            inizio, fine, pubblicato, "pdf_meta" if m else "stimata", None])

        n = 0
        giorno_idx = {g: i for i, g in enumerate(GIORNI)}
        seq_cella = {}
        for r in rows:
            data_slot = inizio + timedelta(days=giorno_idx[r["giorno"]])
            for tit in (t for t in re.split(r"\s*/\s*", r["testo"]) if t.strip()):
                chiave = (data_slot, r["rete"], r["fascia"])
                seq = seq_cella[chiave] = seq_cella.get(chiave, -1) + 1
                titolo, meta = _pulisci_titolo(tit)
                if not titolo:
                    continue
                gen = genere_at(r["top"], r["rete"]) if r["fascia"] == "prime" else None
                n += 1
                conn.execute("""INSERT INTO slot_programmato
                    (slot_id, doc_id, rete, kind, data, fascia, titolo_grezzo,
                     generico, prima_tv, replica, tipo, note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", [
                    f"{doc_id}:{data_slot}:{r['rete']}:{r['fascia']}:{seq}",
                    doc_id, r["rete"], "puntuale", data_slot, r["fascia"], titolo,
                    False, meta["prima_tv"], meta["replica"], meta["tipo"],
                    json.dumps({"genere_colore": gen, "sequenza": seq}) ])
    return {"doc_id": doc_id, "periodo": (str(inizio), str(fine)),
            "pubblicato": str(pubblicato), "slot": n}
