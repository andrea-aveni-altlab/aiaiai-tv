"""
Parser dei listini Publitalia (publitalia_listino_*.pdf, ~260 pp).

Due estrattori:
1. GRIGLIE "PALINSESTO PROGRAMMI" (pp. 2-20): settimana-tipo per rete
   (CAN5/ITA1/RETE4; le tematiche sono fuori perimetro). Testo vettoriale con
   GLIFI RADDOPPIATI a coppie ('0066..0000' = 06.00): si dimezzano quando la
   slice pari == slice dispari. Righe ancorate alle etichette orario a
   sinistra (non un lattice: orari irregolari di inizio programma); celle
   delimitate da linee H/V e rect sottili, colonne DOM..SAB uniformi dalle
   intestazioni; fusione orizzontale via union-find come Cairo. Alternative
   separate SOLO da '/'; le alternanze non datate (FILM/FICTION) restano
   irrisolte NEL LISTINO e vengono risolte dall'overlay dei settimanali PT.
   La rete si riconosce dalle firme nel testo (TG5 -> CAN5, STUDIO APERTO ->
   ITA1, TG4 -> RETE4).
2. STIME (pp. ~114-126): tabelle 'X STIME <target>' con colonne
   sottoperiodo x (base | PRIMISSIMA), AMR in migliaia -> previsione con
   sorgente='publitalia_listino', versione = codice listino stampato su ogni
   pagina (es. 25601), grana='periodo'. Suffisso 'weekend' nella posizione ->
   tipo_giorno='weekend'.

pubblicato_il: data STAMPATA in fondo all'indice (p.1, "26 gennaio 2026").
periodo del doc: unione dei sottoperiodi delle Stime (piu' preciso della
copertina "MARZO - APRILE").
"""
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

import pdfplumber

from ..db import fascia_di
from .. import previsioni

MESI = {m: i + 1 for i, m in enumerate(
    ["GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
     "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"])}
GENERICI = {"FILM", "TELEFILM", "SOAP", "SITCOM", "DOC", "DOCUMENTARI",
            "CARTONI", "PRODUZIONE", "SERIE", "MINISERIE", "FICTION", "SPORT"}
FIRME_RETE = [("CAN5", ("TG5", "MATTINO 5", "VERISSIMO")),
              ("ITA1", ("STUDIO APERTO", "LE IENE", "SPORT MEDIASET")),
              ("RETE4", ("TG4", "TEMPESTA D'AMORE", "QUARTO GRADO", "ZONA BIANCA"))]
GIORNI = ["DOM", "LUN", "MAR", "MER", "GIO", "VEN", "SAB"]


def _dimezza(txt: str) -> str:
    """'0066..0000' -> '06.00'; i glifi bold sono raddoppiati a coppie
    (e su alcune pagine QUADRUPLICATI: ricorsivo)."""
    while len(txt) >= 4 and len(txt) % 2 == 0 and txt[0::2] == txt[1::2]:
        txt = txt[0::2]
    return txt


def _tempo(txt: str) -> int | None:
    """'06.00' -> minuti (senza wrap: lo gestisce il chiamante)."""
    m = re.fullmatch(r"(\d{1,2})[.:](\d{2})", txt)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


# ── griglia settimana-tipo ────────────────────────────────────────────────────
def _segmenti_pagina(pg):
    """Linee + rect sottili -> (hlist [(y, x0, x1)], vlist [(x, y0, y1)])."""
    hs, vs = [], []
    for l in pg.lines:
        if abs(l["y0"] - l["y1"]) < 0.5 and l["x1"] - l["x0"] > 4:
            hs.append((l["top"], l["x0"], l["x1"]))
        elif abs(l["x0"] - l["x1"]) < 0.5 and l["bottom"] - l["top"] > 4:
            vs.append((l["x0"], l["top"], l["bottom"]))
    for r in pg.rects:
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if h < 1.5 and w > 4:
            hs.append((r["top"], r["x0"], r["x1"]))
        elif w < 1.5 and h > 4:
            vs.append((r["x0"], r["top"], r["bottom"]))
        elif w > 20 and h > 8:
            # rect pieni (cornice griglia, celle ombreggiate): i 4 bordi sono
            # confini veri — su RETE4 nessun programma copre 7 giorni e la
            # cornice e' l'unico segmento a tutta larghezza
            hs.append((r["top"], r["x0"], r["x1"]))
            hs.append((r["bottom"], r["x0"], r["x1"]))
            vs.append((r["x0"], r["top"], r["bottom"]))
            vs.append((r["x1"], r["top"], r["bottom"]))
    return hs, vs


def _griglia_pagina(pg):
    """-> (rects celle, cols, y2t) oppure None se la pagina non e' una griglia."""
    words = [dict(w, text=_dimezza(w["text"])) for w in pg.extract_words()]
    heads = {w["text"]: (w["x0"] + w["x1"]) / 2 for w in words
             if w["text"] in GIORNI and w["top"] < 200}
    if len(heads) < 7:
        return None
    centri = [heads[g] for g in GIORNI]
    step = (centri[6] - centri[0]) / 6
    cols = [(c - step / 2, c + step / 2) for c in centri]
    L, R = cols[0][0], cols[-1][1]

    # etichette orario a sinistra della griglia: ancore riga (+wrap dopo 24)
    labels = []
    for w in sorted((w for w in words if w["x1"] < L - 2), key=lambda w: w["top"]):
        t = _tempo(w["text"])
        if t is not None:
            labels.append((w["top"], t))
    if len(labels) < 8:
        return None
    anchored, prev = [], None
    for top, t in labels:
        if prev is not None and t < prev:
            t += 24 * 60
        if prev is not None and t <= prev:
            continue
        anchored.append((top, t))
        prev = t

    def y2t(y):
        best = min(anchored, key=lambda lt: abs(lt[0] + 3 - y))
        if abs(best[0] + 3 - y) <= 7:
            return best[1]
        prima = [lt for lt in anchored if lt[0] + 3 <= y]
        dopo = [lt for lt in anchored if lt[0] + 3 > y]
        if prima and dopo:                       # interpolazione (raro)
            (y0, t0), (y1, t1) = prima[-1], dopo[0]
            return round(t0 + (y - y0 - 3) * (t1 - t0) / (y1 - y0))
        return best[1]

    hs, vs = _segmenti_pagina(pg)
    grid_top = min(y for y, x0, x1 in hs if x0 <= L + 4 and x1 >= R - 4)
    grid_bot = max(y for y, x0, x1 in hs if x0 <= L + 4 and x1 >= R - 4)
    if grid_bot - grid_top < 300:
        return None

    bounds = []
    for ca, cb in cols:
        bb = {grid_top, grid_bot}
        for y, x0, x1 in hs:
            if grid_top < y < grid_bot and x0 <= ca + 4 and x1 >= cb - 4:
                bb.add(y)
        puliti = []
        for t in sorted(bb):
            if puliti and t - puliti[-1] < 3:
                continue
            puliti.append(t)
        bounds.append(puliti)

    def separated(x_edge, y):
        return any(abs(x - x_edge) <= 3 and y0 - 1 <= y <= y1 + 1
                   for x, y0, y1 in vs)

    cells = {}
    for ci, bb in enumerate(bounds):
        for t1, t2 in zip(bb, bb[1:]):
            cells[(ci, t1, t2)] = None
    parent = {k: k for k in cells}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    chiavi = sorted(cells)
    for (ci, t1, t2) in chiavi:
        for (cj, u1, u2) in chiavi:
            if cj == ci + 1 and abs(u1 - t1) < 3 and abs(u2 - t2) < 3 \
                    and not separated(cols[ci][1], (t1 + t2) / 2):
                ra, rb = find((ci, t1, t2)), find((cj, u1, u2))
                if ra != rb:
                    parent[rb] = ra
    groups = {}
    for k in cells:
        groups.setdefault(find(k), []).append(k)
    rects = []
    for ks in groups.values():
        rects.append({"cols": sorted(k[0] for k in ks),
                      "top": min(k[1] for k in ks), "bot": max(k[2] for k in ks),
                      "parole": []})
    # testo dai CHARS (come Cairo): l'interlinea stretta del prime frammenta
    # extract_words in singole lettere ('F I L M'); lo stream di caratteri
    # con gli spazi veri ricompone pulito. _dimezza per token (glifi doppi).
    for c in pg.chars:
        xc, yc = (c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2
        if not (grid_top < yc < grid_bot and L < xc < R):
            continue
        for r in rects:
            ca, cb = cols[r["cols"][0]][0], cols[r["cols"][-1]][1]
            if ca <= xc < cb and r["top"] <= yc < r["bot"]:
                r["parole"].append(c)
                break
    for r in rects:
        righe_c = {}
        for c in r["parole"]:
            righe_c.setdefault(round(c["top"] / 3), []).append(c)
        linee = []
        for _, cs in sorted(righe_c.items()):
            cs.sort(key=lambda c: c["x0"])
            s = cs[0]["text"]
            for c0, c1 in zip(cs, cs[1:]):
                if c1["x0"] - c0["x1"] > 1.2:
                    s += " "
                s += c1["text"]
            linee.append(s)
        testo = " ".join(" ".join(linee).split())
        r["testo"] = " ".join(_dimezza(tok) for tok in testo.split())
        # eredita' banda a bordi sfalsati (come Cairo/Rai)
    for r in rects:
        if r["testo"]:
            continue
        sib = [s for s in rects if s is not r and s["testo"]
               and abs(s["top"] - r["top"]) < 2]
        if sib:
            best = min(sib, key=lambda s: abs(s["bot"] - r["bot"]))
            r["testo"] = best["testo"]
            r["ereditata"] = True
    return rects, cols, y2t


def _rete_griglia(rects) -> str | None:
    testo = " ".join(r["testo"] for r in rects).upper()
    for rete, firme in FIRME_RETE:
        if any(f in testo for f in firme):
            return rete
    return None


# ── stime ─────────────────────────────────────────────────────────────────────
def _target_da_titolo(titolo: str) -> tuple[str, str]:
    m = re.search(r"(\d{1,2})\s*-\s*(\d{1,2})\s*ANNI", titolo)
    if m:
        return f"{m.group(1)}_{m.group(2)}", f"{m.group(1)}-{m.group(2)} anni"
    if "4+" in titolo or "INDIVIDUI" in titolo:
        return "individui", "Individui 4+"
    return "nd", titolo.strip()


def _sottoperiodo(txt: str, anno: int) -> tuple[date, date] | None:
    """'1-28/3' | '29/3-2/5' -> (da, a)."""
    m = re.fullmatch(r"(\d{1,2})(?:/(\d{1,2}))?-(\d{1,2})/(\d{1,2})", txt)
    if not m:
        return None
    g1, m1, g2, m2 = m.groups()
    a = date(anno, int(m2), int(g2))
    d = date(anno, int(m1) if m1 else int(m2), int(g1))
    if d > a:
        d = d.replace(year=anno - 1)
    return d, a


def _stime_pagina(pg, anno: int):
    """-> (titolo, righe_previsione_grezze) o None. Colonne dai token
    sottoperiodo dell'intestazione; 'PRIMISSIMA' marca le colonne premium."""
    words = [dict(w, text=_dimezza(w["text"])) for w in pg.extract_words()]
    titolo = " ".join(w["text"] for w in sorted(
        (w for w in words if w["top"] < 145), key=lambda w: (w["top"], w["x0"])))
    if "STIME" not in titolo.upper():
        return None
    colonne = []                              # (x_centro, periodo, primissima?)
    for w in words:
        if w["top"] > 215:
            continue
        per = _sottoperiodo(w["text"], anno)
        if per:
            colonne.append({"x": (w["x0"] + w["x1"]) / 2, "per": per,
                            "label": w["text"], "prim": False})
    colonne.sort(key=lambda c: c["x"])
    if not colonne:
        return None
    for w in words:
        if w["top"] < 215 and _dimezza(w["text"]).upper() == "PRIMISSIMA":
            xc = (w["x0"] + w["x1"]) / 2
            best = min(colonne, key=lambda c: abs(c["x"] - xc))
            best["prim"] = True

    # righe dati: sotto l'intestazione, etichetta a sinistra + numeri a destra;
    # clustering per gap verticale (niente bucket fissi: spezzano le righe)
    x_num_min = min(c["x"] for c in colonne) - 30
    dati_w = sorted((w for w in words if 215 <= w["top"] <= 812),
                    key=lambda w: w["top"])
    gruppi = []
    for w in dati_w:
        if gruppi and w["top"] - gruppi[-1][-1]["top"] < 4:
            gruppi[-1].append(w)
        else:
            gruppi.append([w])
    out = []
    for ws in gruppi:
        ws.sort(key=lambda w: w["x0"])
        label, valori = [], []
        for w in ws:
            t = w["text"]
            if re.fullmatch(r"\d{1,3}(?:\.\d{3})*", t) and w["x0"] > x_num_min:
                xc = (w["x0"] + w["x1"]) / 2
                col = min(colonne, key=lambda c: abs(c["x"] - xc))
                valori.append((col, int(t.replace(".", ""))))
            else:
                label.append(t)
        etichetta = " ".join(label).strip()
        if not valori or not etichetta or etichetta.lower().startswith(("le stime", "l’universo", "l'universo")):
            continue
        out.append((etichetta, valori))
    return titolo, out


# ── parser documento ─────────────────────────────────────────────────────────
def parse_listino(path: Path, conn) -> dict:
    doc_id = path.stem
    with pdfplumber.open(str(path)) as pdf:
        indice = pdf.pages[1].extract_text() or ""
        mp = re.search(rf"(\d{{1,2}})\s+({'|'.join(m.lower() for m in MESI)})\s+(\d{{4}})",
                       indice.lower())
        if not mp:
            raise ValueError(f"{doc_id}: data stampata non trovata nell'indice")
        pubblicato = date(int(mp.group(3)), MESI[mp.group(2).upper()], int(mp.group(1)))
        anno = pubblicato.year if pubblicato.month < 11 else pubblicato.year + 1
        versione = None
        mv = re.search(r"^(\d{5})$", (pdf.pages[1].extract_text() or ""), re.M)
        if mv:
            versione = mv.group(1)

        # ── stime -> previsione (prima: definiscono anche il periodo del doc) ──
        # perimetro: una pagina Stime entra se ha una FIRMA generalista o se
        # e' la PRIMA del suo prodotto (le pagine successive senza firma sono
        # le tematiche: 13 righe 'Prime Time', una per canale, indistinguibili)
        righe_prev, periodi, prodotti_visti = [], set(), set()
        for pg in pdf.pages:
            testa = (pg.extract_text() or "")[:200].upper()
            if "STIME" not in _dimezza(testa) and "STIME" not in testa:
                continue
            st = _stime_pagina(pg, anno)
            if not st:
                continue
            titolo, dati = st
            tid, tlabel = _target_da_titolo(titolo.upper())
            prodotto = re.sub(r"STIME|\d{1,2}\s*-\s*\d{1,2}\s*ANNI\*?|ANNI\*?", "",
                              titolo.upper()).strip(" *")
            rete_pagina = None
            testo_pag = " ".join(e for e, _ in dati).upper()
            for rete, firme in FIRME_RETE:
                if any(f in testo_pag for f in firme):
                    rete_pagina = rete
                    break
            prima_del_prodotto = prodotto not in prodotti_visti
            prodotti_visti.add(prodotto)
            if rete_pagina is None and not prima_del_prodotto:
                continue                    # pagina tematiche: fuori perimetro
            if not prima_del_prodotto:
                # tematiche con firma-esca (replica di Verissimo su La5):
                # le tradisce la ripetizione interna delle etichette
                visti_pag = Counter(e for e, _ in dati)
                if visti_pag and visti_pag.most_common(1)[0][1] > 1:
                    continue
            for etichetta, valori in dati:
                tipo_giorno = None
                pos = etichetta
                if re.search(r"\bweekend\b", pos, re.I):
                    tipo_giorno = "weekend"
                    pos = re.sub(r"\s*\bweekend\b", "", pos, flags=re.I).strip()
                if prodotto and prodotto != "PRIME":
                    pos = f"{prodotto}: {pos}"   # disambigua tra prodotti
                for col, v in valori:
                    periodi.add(col["per"])
                    righe_prev.append({
                        "grana": "periodo", "periodo_label": col["label"],
                        "periodo_da": col["per"][0], "periodo_a": col["per"][1],
                        "tipo_giorno": tipo_giorno, "rete": rete_pagina,
                        "posizione": pos + (" [primissima]" if col["prim"] else ""),
                        "target": tid, "target_label": tlabel,
                        "metrica": "amr_migliaia", "valore": v})
        if not periodi:
            raise ValueError(f"{doc_id}: nessuna pagina Stime riconosciuta")
        # dedup first-wins sulla PK di previsione (collisioni residue rare)
        viste, uniche = set(), []
        for r in righe_prev:
            k = (r["periodo_da"], r["rete"], r["posizione"],
                 r["tipo_giorno"], r["target"], r["metrica"])
            if k in viste:
                continue
            viste.add(k)
            uniche.append(r)
        righe_prev = uniche
        periodo_da = min(p[0] for p in periodi)
        periodo_a = max(p[1] for p in periodi)

        conn.execute("DELETE FROM previsione WHERE doc_id = ?", [doc_id])
        conn.execute("DELETE FROM slot_programmato WHERE doc_id = ?", [doc_id])
        conn.execute("DELETE FROM doc_sorgente WHERE doc_id = ?", [doc_id])
        conn.execute("INSERT INTO doc_sorgente VALUES (?,?,?,?,?,?,?,?,?)", [
            doc_id, "publitalia", "listino_griglia", str(path),
            periodo_da, periodo_a, pubblicato, "stampata",
            f"listino {versione or '?'}; griglie CAN5/ITA1/RETE4 + stime"])

        n_prev = previsioni.registra(conn, "publitalia_listino",
                                     versione or doc_id, pubblicato,
                                     righe_prev, doc_id=doc_id)

        # ── griglie settimana-tipo (pp. 2-20) ──
        n_slot, per_rete = 0, {}
        for i in range(2, min(21, len(pdf.pages))):
            pg = pdf.pages[i]
            testa = _dimezza(((pg.extract_text() or "").split("\n") + ["", "", ""])[2].replace(" ", ""))
            if "PROGRAMMI" not in testa.upper():
                continue
            g = _griglia_pagina(pg)
            if not g:
                continue
            rects, cols, y2t = g
            rete = _rete_griglia(rects)
            if rete is None or rete in per_rete:
                # tematiche fuori perimetro; first-wins per rete (le repliche
                # sulle tematiche contengono le stesse firme, es. VERISSIMO
                # su La5) — le generaliste vengono per prime (pp. 2/4/6)
                continue

            # tiling per colonna
            span = y2t(max(r["bot"] for r in rects)) - y2t(min(r["top"] for r in rects))
            for ci in range(7):
                tot = sum(y2t(r["bot"]) - y2t(r["top"]) for r in rects if ci in r["cols"])
                assert abs(tot - span) <= 10, \
                    f"{doc_id} {rete} col {ci}: copertura {tot}' != {span}'"

            for i_cella, r in enumerate(
                    sorted(rects, key=lambda r: (r["top"], r["cols"][0]))):
                if not r["testo"]:
                    continue
                mask = "".join("1" if c in r["cols"] else "0" for c in range(7))
                t1, t2 = y2t(r["top"]), y2t(r["bot"])
                t2 = min(t2, 26 * 60)
                if t2 <= t1:
                    continue
                alts = [a.strip() for a in re.split(r"\s*/\s*", r["testo"]) if a.strip()]
                gruppo = f"{rete}:{mask}:{t1}" if len(alts) > 1 else None
                for seq, alt in enumerate(alts):
                    titolo = " ".join(alt.split()).strip(" -")
                    if not titolo:
                        continue
                    note = {}
                    if r.get("ereditata"):
                        note["ereditata"] = True
                    n_slot += 1
                    per_rete[rete] = per_rete.get(rete, 0) + 1
                    conn.execute("""INSERT INTO slot_programmato
                        (slot_id, doc_id, rete, kind, dow_mask, t_start, t_end,
                         fascia, titolo_grezzo, generico, gruppo_alt,
                         prima_tv, replica, tipo, note)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                        f"{doc_id}:{rete}:{i_cella}:{t1}:{seq}", doc_id, rete,
                        "base", mask, t1 * 60, t2 * 60,
                        fascia_di(conn, "publitalia", t1 * 60), titolo,
                        titolo.upper() in GENERICI, gruppo,
                        None, None, None, json.dumps(note)])
    return {"doc_id": doc_id, "periodo": (str(periodo_da), str(periodo_a)),
            "pubblicato": str(pubblicato), "versione": versione,
            "slot": n_slot, "per_rete": per_rete, "previsioni": n_prev}
