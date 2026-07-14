"""
Parser delle griglie editoriali Cairo/LA7 ("Politica commerciale", cairo_la7_*.pdf).

Struttura (verificata sui 5 doc gen-giu 2026): copertina con "Validità: 29 MARZO -
2 MAGGIO 2026" e data di pubblicazione STAMPATA ("Aggiornamento 20 febbraio 2026");
griglia LA7 alla prima pagina "Palinsesto editoriale" (la seconda, giorni in
minuscolo, è LA7d: fuori perimetro). Settimana-tipo su lattice 10': 7 colonne
giorno DOMENICA..SABATO, etichette orario ai lati (06:00 -> ~01:20), celle
delimitate dai segmenti H/V del disegno; i gap nelle linee verticali indicano
celle fuse su più giorni (es. TAGADÁ su LUN-VEN, titolo centrato). Una cella
disegnata una sola volta su una banda a bordi sfalsati (TG LA7 13:30) lascia
celle vuote nei giorni laterali: ereditano il testo dal sibling a pari t_start.
Alternanze nella cella separate da '/': decorrenze "il 30.3", "IL 13.4 & 27.4"
(-> finestra + eccezione 'solo' se più date), "dal 22.4", "fino al 15.4";
'®' = replica. Titoli {FILM, SERIE TV, DOC, LA7 DOC, PRODUZIONI, ...} = generico.
kind='base' per tutti gli slot; orari sempre dichiarati dalla geometria.
"""
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pdfplumber

from ..db import fascia_di

GIORNI = ["DOMENICA", "LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO"]
MESI = {m: i + 1 for i, m in enumerate(
    ["GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
     "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"])}
GENERICI = {"FILM", "SERIE TV", "TELEFILM", "DOC", "LA7 DOC", "PRODUZIONI",
            "MINISERIE", "CARTOON"}
_MESE_RX = "|".join(m.lower() for m in MESI)
# forme viste nei doc 2026: "il 6.1", "IL 13.4 & 27.4", "il 5.1 e il 6.1",
# "il 27.05", "il 4 e 11 e 18 giugno", "1 e 8 giugno" (senza 'il'),
# "dal 22.4", "dall'8.6", "fino al 15.4"; il '+' separa alternative come '/'
RE_SEP = re.compile(r"\s*/\s*|\s\+\s")
RE_DAL = re.compile(rf"\bdall?['\s]\s*(?:(\d{{1,2}}\.\d{{1,2}})|(\d{{1,2}})\s+({_MESE_RX}))", re.I)
RE_FINO = re.compile(rf"\bfino\s+al(?:l')?\s*(?:(\d{{1,2}}\.\d{{1,2}})|(\d{{1,2}})\s+({_MESE_RX}))", re.I)
RE_IL_DOT = re.compile(r"\b(?:il|l')\s*(\d{1,2}\.\d{1,2}(?:\s*(?:&|e)\s*(?:il\s+)?\d{1,2}\.\d{1,2})*)", re.I)
RE_IL_MESE = re.compile(rf"(?:\b(?:il|l')\s+)?\b(\d{{1,2}}(?:\s+e\s+\d{{1,2}})*)\s+({_MESE_RX})\b", re.I)
RE_DOT = re.compile(r"\d{1,2}\.\d{1,2}")


def _parse_cover(text: str) -> tuple[date, date, date | None]:
    """-> (periodo_da, periodo_a, pubblicato_stampata). Formati visti:
    'Validità: 4 GENNAIO - 28 FEBBRAIO 2026' | 'Validità: 1 - 28 MARZO 2026'."""
    t = text.upper().replace("–", "-")
    m = re.search(r"VALIDIT[AÀ]:\s*(\d{1,2})\s*([A-ZÀ-Ù]+)?\s*-\s*(\d{1,2})\s+([A-ZÀ-Ù]+)\s+(\d{4})", t)
    if not m:
        raise ValueError(f"copertina senza Validità riconoscibile: {text[:120]!r}")
    d1, m1, d2, m2, anno = m.groups()
    fine = date(int(anno), MESI[m2], int(d2))
    inizio = date(int(anno), MESI[m1] if m1 else MESI[m2], int(d1))
    if inizio > fine:                       # validità a cavallo d'anno
        inizio = inizio.replace(year=fine.year - 1)
    pub = None
    mp = re.search(r"AGGIORNAMENTO\s+(\d{1,2})\s+([A-ZÀ-Ù]+)\s+(\d{4})", t)
    if mp:
        pub = date(int(mp.group(3)), MESI[mp.group(2)], int(mp.group(1)))
    return inizio, fine, pub


def _data_dm(dm: str, periodo_da: date, periodo_a: date) -> date:
    """'13.4' -> data, scegliendo l'anno che cade nel periodo di validità."""
    g, m = (int(x) for x in dm.split("."))
    for anno in {periodo_da.year, periodo_a.year}:
        d = date(anno, m, g)
        if periodo_da - timedelta(days=7) <= d <= periodo_a + timedelta(days=7):
            return d
    raise ValueError(f"data '{dm}' fuori dal periodo {periodo_da}..{periodo_a}")


def _data_bordo(m, periodo_da: date, periodo_a: date) -> date:
    """Match di RE_DAL/RE_FINO -> data ('22.4' oppure '19' + 'giugno')."""
    if m.group(1):
        return _data_dm(m.group(1), periodo_da, periodo_a)
    return _data_dm(f"{m.group(2)}.{MESI[m.group(3).upper()]}", periodo_da, periodo_a)


def _parse_alternativa(txt: str, periodo_da: date, periodo_a: date) -> dict:
    """Un'alternativa di cella -> titolo pulito + finestra/date + flag."""
    t = " ".join(txt.split())
    out = {"replica": False, "valido_da": None, "valido_a": None, "date_list": None}
    if "®" in t:
        out["replica"] = True
        t = t.replace("®", " ")
    m = RE_FINO.search(t)
    if m:
        out["valido_a"] = _data_bordo(m, periodo_da, periodo_a)
        t = t[:m.start()] + t[m.end():]
    m = RE_DAL.search(t)
    if m:
        out["valido_da"] = _data_bordo(m, periodo_da, periodo_a)
        t = t[:m.start()] + t[m.end():]
    dates = None
    m = RE_IL_DOT.search(t)
    if m:
        dates = sorted(_data_dm(dm, periodo_da, periodo_a)
                       for dm in RE_DOT.findall(m.group(1)))
    else:
        m = RE_IL_MESE.search(t)
        if m:
            mese = MESI[m.group(2).upper()]
            dates = sorted(_data_dm(f"{g.strip()}.{mese}", periodo_da, periodo_a)
                           for g in m.group(1).split("e"))
    if dates:
        out["valido_da"], out["valido_a"] = dates[0], dates[-1]
        if len(dates) > 1:
            out["date_list"] = [d.isoformat() for d in dates]
        t = t[:m.start()] + t[m.end():]
    assert not (out["valido_da"] and out["valido_a"]
                and out["valido_da"] > out["valido_a"]), f"finestra rovesciata: {txt!r}"
    out["titolo"] = " ".join(t.split()).strip(" -")
    out["generico"] = out["titolo"].upper() in GENERICI
    return out


def _lattice(pg, x_left):
    """Etichette orario a sinistra della griglia -> funzione y->minuti (snap 10').
    Ricostruite dai chars (alcune sono frammentate carattere per carattere) e
    interpolate a tratti: il passo verticale NON è costante su tutta la pagina
    (verificato su maggio 2026, banda 22:30-23:10 disegnata con passo diverso)."""
    rows = {}
    for c in pg.chars:
        if c["x1"] < x_left + 1:
            rows.setdefault(round(c["top"]), []).append(c)
    pts, prev = [], None
    for top in sorted(rows):
        txt = "".join(c["text"] for c in sorted(rows[top], key=lambda c: c["x0"]))
        m = re.fullmatch(r"(\d{2}):(\d{2})", txt.strip())
        if not m:
            continue
        mins = int(m.group(1)) * 60 + int(m.group(2))
        if prev is not None and mins < prev:
            mins += 24 * 60                 # oltre mezzanotte: giorno TV
        prev = mins
        pts.append((min(c["top"] for c in rows[top]), mins))
    if len(pts) < 20:
        raise ValueError(f"solo {len(pts)} etichette orario: non è la griglia attesa")
    for (t0, m0), (t1, m1) in zip(pts, pts[1:]):
        passo = (t1 - t0) / (m1 - m0) * 10
        assert m1 > m0 and 3 < passo < 12, \
            f"lattice anomalo a {m0 // 60:02d}:{m0 % 60:02d} ({passo:.1f}px/10')"

    def t_of(y):
        if y <= pts[0][0]:
            i = 0
        elif y >= pts[-1][0]:
            i = len(pts) - 2
        else:
            i = max(j for j in range(len(pts) - 1) if pts[j][0] <= y)
        (t0, m0), (t1, m1) = pts[i], pts[i + 1]
        return round((m0 + (y - t0) * (m1 - m0) / (t1 - t0)) / 10) * 10

    return t_of


def _celle_griglia(pg):
    """Ricostruisce le celle (rettangoli fusi) della griglia dalla geometria.
    -> (celle, grid_top, grid_bot, t_of) con celle = [{cols, top, bot, testo}]."""
    vs = [l for l in pg.lines
          if abs(l["x0"] - l["x1"]) < 0.5 and l["bottom"] - l["top"] > 5]
    hs = [l for l in pg.lines if abs(l["y0"] - l["y1"]) < 0.5]

    # bordi colonna = ascisse delle V raggruppate (la griglia LA7 ne ha 8)
    xs = sorted({round(l["x0"], 1) for l in vs if 55 < l["x0"] < 515})
    edges = []
    for x in xs:
        if edges and x - edges[-1][-1] < 3:
            edges[-1].append(x)
        else:
            edges.append([x])
    col_edges = [sum(g) / len(g) for g in edges]
    if len(col_edges) < 7:
        raise ValueError(f"trovati solo {len(col_edges)} bordi colonna")
    L = col_edges[0]
    R = max(max(l["x1"] for l in hs if l["x1"] - l["x0"] > 5), col_edges[-1])
    W = (R - L) / 7
    cols = [(L + i * W, L + (i + 1) * W) for i in range(7)]

    t_of = _lattice(pg, L)

    # segmenti H uniti per quota
    per_top = {}
    for l in hs:
        if l["x1"] - l["x0"] < 5 or not (50 < l["top"] < 745):
            continue
        per_top.setdefault(round(l["top"], 1), []).append([l["x0"], l["x1"]])
    hsegs = {}
    for t, ivs in per_top.items():
        ivs.sort()
        merged = [ivs[0][:]]
        for x0, x1 in ivs[1:]:
            if x0 <= merged[-1][1] + 2:
                merged[-1][1] = max(merged[-1][1], x1)
            else:
                merged.append([x0, x1])
        hsegs[t] = merged
    full = [t for t, segs in hsegs.items()
            if any(s[0] <= L + 3 and s[1] >= R - 3 for s in segs)]
    grid_top, grid_bot = min(full), max(hsegs)
    # la prima riga è l'intestazione giorni: la griglia parte dal 2° full-width
    header_bot = min(t for t in full if t > grid_top + 3)
    grid_top = header_bot

    def covers(seg, ca, cb):
        return seg[0] <= ca + 3 and seg[1] >= cb - 3

    bounds = []
    for ca, cb in cols:
        bb = {grid_top, grid_bot}
        for t, segs in hsegs.items():
            if grid_top < t < grid_bot and any(covers(s, ca, cb) for s in segs):
                bb.add(t)
        bounds.append(sorted(bb))

    # V per bordo interno: separatore presente a quota y?
    vsegs = {}
    for l in vs:
        for i, ce in enumerate(col_edges):
            if abs(l["x0"] - ce) < 2:
                vsegs.setdefault(i, []).append(
                    (min(l["top"], l["bottom"]), max(l["top"], l["bottom"])))

    def separated(x_edge, y):
        i = min(range(len(col_edges)), key=lambda i: abs(col_edges[i] - x_edge))
        if abs(col_edges[i] - x_edge) > 3:
            return False                    # bordo mai disegnato = fuso
        return any(t0 - 1 <= y <= t1 + 1 for t0, t1 in vsegs.get(i, []))

    # celle per colonna + union-find orizzontale sui pari-confini non separati
    cells = {}
    for ci, bb in enumerate(bounds):
        for t1, t2 in zip(bb, bb[1:]):
            if t2 - t1 >= 3:
                cells[(ci, t1, t2)] = None
    parent = {k: k for k in cells}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for (ci, t1, t2) in sorted(cells):
        k2 = (ci + 1, t1, t2)
        if k2 in cells and not separated(cols[ci][1], (t1 + t2) / 2):
            ra, rb = find((ci, t1, t2)), find(k2)
            if ra != rb:
                parent[rb] = ra
    groups = {}
    for k in cells:
        groups.setdefault(find(k), []).append(k)

    rects = []
    for ks in groups.values():
        cis = sorted(k[0] for k in ks)
        rects.append({"cols": cis, "top": ks[0][1], "bot": ks[0][2], "chars": []})
    for c in pg.chars:
        xc, yc = (c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2
        if not (grid_top < yc < grid_bot and cols[0][0] < xc < cols[-1][1]):
            continue
        for r in rects:
            ca, cb = cols[r["cols"][0]][0], cols[r["cols"][-1]][1]
            if ca <= xc < cb and r["top"] <= yc < r["bot"]:
                r["chars"].append(c)
                break
    # testo dai CHARS, non da extract_words: lo stream contiene gli spazi veri,
    # mentre extract_words frammenta i titoli a spaziatura espansa ('L A T O R R E')
    for r in rects:
        righe = {}
        for c in r["chars"]:
            righe.setdefault(round(c["top"]), []).append(c)
        linee = []
        for t in sorted(righe):
            cs = sorted(righe[t], key=lambda c: c["x0"])
            s = cs[0]["text"]
            for c0, c1 in zip(cs, cs[1:]):
                if c1["x0"] - c0["x1"] > 1.5:      # spazio mancante nello stream
                    s += " "
                s += c1["text"]
            linee.append(s)
        r["testo"] = " ".join(" ".join(linee).split())

    # cella vuota (banda a bordi sfalsati, titolo disegnato una volta):
    # eredita dal sibling a pari t_start con testo, t_end più vicino
    for r in rects:
        if r["testo"]:
            continue
        sib = [s for s in rects if s is not r and s["testo"]
               and abs(s["top"] - r["top"]) < 2]
        if sib:
            best = min(sib, key=lambda s: abs(s["bot"] - r["bot"]))
            r["testo"] = best["testo"]
            r["ereditata"] = True
    return rects, grid_top, grid_bot, t_of


def parse_griglia(path: Path, conn) -> dict:
    """Parsa una politica commerciale Cairo e inserisce doc + slot base LA7.
    Idempotente (DELETE+INSERT per doc_id)."""
    doc_id = path.stem
    with pdfplumber.open(str(path)) as pdf:
        periodo_da, periodo_a, pubblicato = _parse_cover(pdf.pages[0].extract_text() or "")
        fonte = "stampata"
        if pubblicato is None:
            from pypdf import PdfReader
            created = str((PdfReader(str(path)).metadata or {}).get("/CreationDate", ""))
            m = re.match(r"D:(\d{8})", created)
            if not m:
                raise ValueError(f"{doc_id}: né data stampata né CreationDate")
            from datetime import datetime
            pubblicato, fonte = datetime.strptime(m.group(1), "%Y%m%d").date(), "pdf_meta"

        ed = next(i for i, pg in enumerate(pdf.pages)
                  if "PALINSESTO EDITORIALE" in (pg.extract_text() or "").upper())
        pg = pdf.pages[ed]
        rects, grid_top, grid_bot, t_of = _celle_griglia(pg)

        # tiling: per ogni giorno le celle devono coprire l'intera colonna
        span = t_of(grid_bot) - t_of(grid_top)
        for ci in range(7):
            tot = sum(t_of(r["bot"]) - t_of(r["top"]) for r in rects if ci in r["cols"])
            assert tot == span, f"{doc_id} col {GIORNI[ci]}: copertura {tot}' != {span}'"

        conn.execute("DELETE FROM slot_eccezione WHERE doc_id = ?", [doc_id])
        conn.execute("DELETE FROM slot_programmato WHERE doc_id = ?", [doc_id])
        conn.execute("DELETE FROM doc_sorgente WHERE doc_id = ?", [doc_id])
        conn.execute("INSERT INTO doc_sorgente VALUES (?,?,?,?,?,?,?,?,?)", [
            doc_id, "cairo", "listino_griglia", str(path),
            periodo_da, periodo_a, pubblicato, fonte,
            f"griglia editoriale LA7 p.{ed + 1}; LA7d fuori perimetro"])

        n_slot, n_celle = 0, 0
        for r in sorted(rects, key=lambda r: (r["top"], r["cols"][0])):
            if not r["testo"]:
                continue
            n_celle += 1
            mask = "".join("1" if i in r["cols"] else "0" for i in range(7))
            t1, t2 = t_of(r["top"]) * 60, t_of(r["bot"]) * 60
            alts = [t for t in RE_SEP.split(r["testo"]) if t.strip()]
            gruppo = f"{mask}:{t1}" if len(alts) > 1 else None
            for seq, alt in enumerate(alts):
                p = _parse_alternativa(alt, periodo_da, periodo_a)
                if not p["titolo"]:
                    continue
                slot_id = f"{doc_id}:{mask}:{t1}:{seq}"
                n_slot += 1
                conn.execute("""INSERT INTO slot_programmato
                    (slot_id, doc_id, rete, kind, dow_mask, valido_da, valido_a,
                     t_start, t_end, fascia, titolo_grezzo, generico, gruppo_alt,
                     prima_tv, replica, tipo, note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                    slot_id, doc_id, "LA7", "base", mask,
                    p["valido_da"], p["valido_a"], t1, t2,
                    fascia_di(conn, "cairo", t1), p["titolo"], p["generico"],
                    gruppo, None, p["replica"], None,
                    json.dumps({"ereditata": True} if r.get("ereditata") else {})])
                if p["date_list"]:
                    conn.execute("""INSERT INTO slot_eccezione
                        (ecc_id, doc_id, slot_id, tipo, date_list)
                        VALUES (?,?,?,?,?)""", [
                        f"{slot_id}:solo", doc_id, slot_id, "solo",
                        json.dumps(p["date_list"])])
    return {"doc_id": doc_id, "periodo": (str(periodo_da), str(periodo_a)),
            "pubblicato": str(pubblicato), "celle": n_celle, "slot": n_slot}
