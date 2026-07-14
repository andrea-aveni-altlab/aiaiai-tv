"""
Parser delle griglie Rai Pubblicità (rai_tvprogram_*.pdf).

Le griglie sono IMMAGINI RASTER dentro il PDF (~165 dpi): il testo vettoriale è
solo header ("29 MARZO -30 MAGGIO 2026") e piè di pagina ("Aggiornato al 12
febbraio 2026" = pubblicato_il, stampata). Pipeline per pagina:
  1. rendering a scala 3 (pypdfium2) -> immagine in scala di grigi;
  2. geometria celle dai PIXEL: run scuri lunghi = segmenti H/V, colonne giorno
     D..S per divisione uniforme tra i bordi, celle per colonna dai segmenti H
     che la coprono, fusione orizzontale dove manca il separatore V (union-find,
     come il parser Cairo); i bordi TRATTEGGIATI (annotazioni "nel corso") hanno
     run corti e non generano separatori;
  3. lattice 15' dalle etichette OCR delle due gutter laterali, interpolazione
     piecewise (la banda 02:00-06:00 è disegnata COMPRESSA: mai fit globale);
  4. OCR per cella (ROI) con Vision via ocr_helper.swift (compilato al volo);
  5. grammatica decorrenze: "f. al 24/5"/"fino al" -> valido_a; "d. 25/5"/"dal"
     -> valido_da; "escl. 31/3, 3/4" -> eccezione escluso; date/range residui
     ("29/3", "13/4-4/5", "7-21/5", "d. 7/4-12/5; 26/5") -> finestra + eccezione
     'solo' (i range si espandono sulle date del dow della colonna); '®' =
     replica. I RANGE si estraggono PRIMA di dal/fino (altrimenti "d. 7/4-12/5"
     si spezza male). Alternative separate per RIGHE: una riga che TERMINA con
     una data chiude l'alternativa corrente (le griglie Rai non usano '/').
  6. celle oltre le 26:00 (banda 02-06 del giorno successivo): dow_mask ruotata
     di +1 giorno e orario riportato a 02:00-06:00.
Reti: solo RAI1/RAI2/RAI3, riconosciute dall'OCR del logo in testata.
"""
import json
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

from ..db import fascia_di

SCALE = 3.0
SOGLIA = 170                 # grigio: sotto = "scuro"
MIN_H = 45                   # px: run minimo per un separatore orizzontale vero
MIN_V = 12                   # px: i run V servono solo a separated(); un falso
                             # positivo li' e' benigno (l'eredita' pareggia)
MESI = {m: i + 1 for i, m in enumerate(
    ["GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
     "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"])}
GENERICI = {"FILM", "FICTION", "SERIALE", "TF", "DOC", "MINISERIE", "TVMP",
            "INTRATT", "DEF"}

RE_DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")
RE_RANGE = re.compile(r"\b(\d{1,2})(?:/(\d{1,2}))?\s*-\s*(\d{1,2})/(\d{1,2})\b")
RE_ESCL = re.compile(r"\besc[l1iI]\.?\s*((?:\d{1,2}/\d{1,2}[.,;]?\s*)+)", re.I)
# 'al 4/9' senza la 'f.' (persa dall'OCR) e' comunque un fino-al: il \b non
# scatta dentro 'dal', e 'al' senza data dopo non matcha
RE_FINO = re.compile(r"\b(?:fino\s+al|f\.\s*al|f\.|al)\s*(\d{1,2})/(\d{1,2})", re.I)
RE_DAL = re.compile(r"\b(?:dal|d\.)\s*(\d{1,2})/(\d{1,2})", re.I)
RE_ORA = re.compile(r"^(\d{1,2})[.:](\d{2})\b")
RE_DUR = re.compile(r"\(\s*\d{1,3}['’]?\s*\)")
RE_RESIDUI = re.compile(r"\b(?:d|f|dal|fino\s+al|escl)\.?\s*(?=[;,)\s]|$)", re.I)

# ── incertezza di LETTURA (≠ incertezza di palinsesto) ───────────────────────
# Vision su queste griglie e' bimodale: conf=1.0 per il testo letto bene,
# 0.3-0.5 esattamente sulle righe storpiate (misurato: 367/369 a 1.0, le 2
# sotto erano 'HCTON 1S' e '19115'). Sotto questa soglia lo slot va in
# curatela, non nel DB come dato buono.
CONF_AFFIDABILE = 0.99
# data spezzata: cifre + '/' senza cifre dopo ("f. al 24/" con il 5 illeggibile)
RE_DATA_ROTTA = re.compile(r"\b\d{1,2}\s*/\s*(?![\d])")
# titolo sospetto: residui numerici di 3-4 cifre (date non riparate) o
# caratteri che l'OCR produce solo sbagliando
RE_TITOLO_SOSPETTO = re.compile(r"\b\d{3,4}\b|[\[\]{}|\\ÃÀ̧ĄÅ§]")


# ── OCR (Vision via helper Swift, compilato una volta per sessione) ──────────
_HELPER_SRC = Path(__file__).parent / "ocr_helper.swift"
_helper_bin = None


def _helper() -> Path:
    global _helper_bin
    if _helper_bin is None:
        import tempfile
        out = Path(tempfile.gettempdir()) / "palinsesto_ocr_helper"
        if not out.exists() or out.stat().st_mtime < _HELPER_SRC.stat().st_mtime:
            subprocess.run(["swiftc", "-O", str(_HELPER_SRC), "-o", str(out)],
                           check=True, capture_output=True)
        _helper_bin = out
    return _helper_bin


def _ocr(png: Path, rects=None) -> list | dict:
    args = [str(_helper()), str(png)]
    tmp = None
    if rects is not None:
        tmp = png.with_suffix(".rects.json")
        tmp.write_text(json.dumps(rects))
        args.append(str(tmp))
    try:
        r = subprocess.run(args, check=True, capture_output=True, text=True)
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    return json.loads(r.stdout)


# ── geometria dai pixel ───────────────────────────────────────────────────────
def _cluster(raw, gap=3):
    """raw = [(pos, a, b)] ordinato per pos -> segmenti [pos0, pos1, a, b]
    fondendo run adiacenti (spessore linea) che si sovrappongono in [a,b]."""
    segs = []
    for pos, a, b in raw:
        merged = False
        for s in reversed(segs):
            if pos - s[1] > gap:
                break
            if a < s[3] and s[2] < b:
                s[1] = pos
                s[2], s[3] = min(s[2], a), max(s[3], b)
                merged = True
                break
        if not merged:
            segs.append([pos, pos, a, b])
    return segs


def _min_direzionale(img, dx, dy):
    """Min puntuale dell'immagine con le sue traslate di +-1 lungo (dx,dy):
    copre l'ondeggiamento +-1px delle linee raster SENZA dilatare nell'altra
    direzione (un MinFilter quadrato fonde le lettere del testo bold e
    solidifica i bordi tratteggiati: entrambi diventerebbero falsi separatori)."""
    from PIL import ImageChops
    out = img
    for s in (-1, 1):
        shifted = img.transform(img.size, 0,  # AFFINE
                                (1, 0, s * dx, 0, 1, s * dy), fillcolor=255)
        out = ImageChops.darker(out, shifted)
    return out


def _segmenti(img):
    """-> (hlist [(y0, y1, x0, x1)], vlist [(x, y0, y1)]) dai run scuri.
    Le bande spesse (barra giorni, celle campite) portano DUE confini: y0 e y1."""
    W, H = img.size

    px = _min_direzionale(img, 0, 1).tobytes()   # linee H: finestra verticale
    raw_h = []
    for y in range(H):
        row = px[y * W:(y + 1) * W]
        start = None
        for x in range(W):
            if row[x] < SOGLIA:
                if start is None:
                    start = x
            elif start is not None:
                if x - start >= MIN_H:
                    raw_h.append((y, start, x))
                start = None
        if start is not None and W - start >= MIN_H:
            raw_h.append((y, start, W))

    px = _min_direzionale(img, 1, 0).tobytes()   # linee V: finestra orizzontale
    raw_v = []
    for x in range(W):
        col = px[x::W]
        start = None
        for y in range(H):
            if col[y] < SOGLIA:
                if start is None:
                    start = y
            elif start is not None:
                if y - start >= MIN_V:
                    raw_v.append((x, start, y))
                start = None
        if start is not None and H - start >= MIN_V:
            raw_v.append((x, start, H))

    hlist = [(s[0], s[1], s[2], s[3]) for s in _cluster(sorted(raw_h))]
    # le V restano run GREZZI: il clustering concatenerebbe i glifi del testo
    # (sovrapposti in y tra x adiacenti) in finti bordi a tutta altezza
    vlist = raw_v
    return hlist, vlist


def _lattice_ocr(righe_ocr, x_left, x_right):
    """Etichette hh:mm delle gutter -> t_of(y) piecewise (minuti, snap 15')."""
    cand = []
    for l in righe_ocr:
        xc = (l["x0"] + l["x1"]) / 2
        if x_left < xc < x_right:
            continue
        m = re.fullmatch(r"(\d{1,2})[.:]?(\d{2})", l["text"].strip())
        if not m:
            continue
        mins = int(m.group(1)) * 60 + int(m.group(2))
        if mins % 15 or not 0 <= mins < 24 * 60:
            continue
        cand.append(((l["top"] + l["bottom"]) / 2, mins))   # riga = centro etichetta
    cand.sort()
    if len(cand) < 40:
        raise ValueError(f"solo {len(cand)} etichette lattice candidate")
    # la griglia va dalle 06:00 alle 06:00 del giorno dopo: il giro di
    # mezzanotte e' deterministico PER etichetta (mai in cascata: una sola
    # etichetta misletta avvelenerebbe tutto il seguito)
    mid_y = (cand[0][0] + cand[-1][0]) / 2
    base = [(top, mins + 24 * 60 if mins < 360 or (mins == 360 and top > mid_y)
             else mins) for top, mins in cand]
    # gli errori OCR residui si eliminano con la sottosequenza crescente
    # piu' lunga (top e minuti entrambi crescenti)
    n = len(base)
    best, prev = [1] * n, [-1] * n
    for i in range(n):
        for j in range(i):
            if (base[j][1] < base[i][1] and base[j][0] < base[i][0] - 1
                    and best[j] + 1 > best[i]):
                best[i], prev[i] = best[j] + 1, j
    i = max(range(n), key=lambda k: best[k])
    catena = []
    while i >= 0:
        catena.append(base[i])
        i = prev[i]
    catena.reverse()
    per_min = {}
    for top, m in catena:                       # doppioni dalle due gutter
        per_min.setdefault(m, []).append(top)
    pts = sorted((sum(tt) / len(tt), m) for m, tt in per_min.items())
    if len(pts) < 40:
        raise ValueError(f"solo {len(pts)} etichette lattice affidabili")

    def t_of(y):
        if y <= pts[0][0]:
            i = 0
        elif y >= pts[-1][0]:
            i = len(pts) - 2
        else:
            i = max(j for j in range(len(pts) - 1) if pts[j][0] <= y)
        (t0, m0), (t1, m1) = pts[i], pts[i + 1]
        m = round((m0 + (y - t0) * (m1 - m0) / (t1 - t0)) / 15) * 15
        # oltre l'ultima etichetta la banda notturna e' COMPRESSA: il fondo
        # griglia e' per costruzione le 06:00 del giorno dopo (30h)
        return max(360, min(m, 30 * 60))

    return t_of


def _barra_e_lettere(img_rgb):
    """Barra dei giorni e lettere D..S dai PIXEL (l'OCR delle singole lettere
    su fondo colorato e' instabile run-to-run). La barra = banda di righe a
    colore saturo; le lettere = 7 gruppi di pixel BIANCHI dentro la banda,
    stretti e a passo regolare (cosi' si scarta la banda del titolo).
    -> (y_bar_bottom, centri c0..c6)."""
    W, H = img_rgb.size
    xs = list(range(40, W - 40, 5))
    bande, cur = [], None
    for y in range(60, min(H, 600)):
        n = sum(1 for x in xs
                if max(p := img_rgb.getpixel((x, y))) - min(p) > 50 and max(p) > 90)
        if n > len(xs) * 0.35:
            cur = [y, y] if cur is None else [cur[0], y]
        elif cur is not None:
            if cur[1] - cur[0] >= 12:
                bande.append(cur)
            cur = None
    if cur is not None and cur[1] - cur[0] >= 12:
        bande.append(cur)

    for y0b, y1b in bande:
        bianchi = {}
        for x in range(40, W - 40):
            n = sum(1 for y in range(y0b + 2, y1b - 1)
                    if min(img_rgb.getpixel((x, y))) > 200)
            if n >= 3:
                bianchi[x] = n
        gruppi = []
        for x in sorted(bianchi):
            if gruppi and x - gruppi[-1][-1] <= 12:
                gruppi[-1].append(x)
            else:
                gruppi.append([x])
        gruppi = [g for g in gruppi if sum(bianchi[x] for x in g) >= 25
                  and g[-1] - g[0] <= 45]
        if not 7 <= len(gruppi) <= 10:
            continue
        cand = [sum(g) / len(g) for g in gruppi]
        # possono esserci gruppi bianchi spuri: cerca il sottoinsieme di 7
        # centri a passo regolare
        from itertools import combinations
        for combo in combinations(cand, 7):
            passi = [b - a for a, b in zip(combo, combo[1:])]
            med = sorted(passi)[len(passi) // 2]
            if all(abs(p - med) < 35 for p in passi):
                return y1b, list(combo)
    raise ValueError(f"barra dei giorni non identificata ({len(bande)} bande colore)")


def _celle_pagina(img, righe_ocr, img_rgb=None):
    """-> (rects [{cols, top, bot}], cols, t_of). Stessa logica del parser
    Cairo (bounds per colonna + union-find sui pari-confini), da pixel."""
    hlist, vlist = _segmenti(img)
    grid_top, centri = _barra_e_lettere(img_rgb)
    # Le colonne NON sono uniformi (la D e' piu' stretta) e i bordi verticali
    # sono jitterati (run V inaffidabili). I bordi colonna si leggono dagli
    # ENDPOINT dei segmenti H: ogni cella contribuisce x0/x1 esattamente ai
    # bordi del suo box. Tra due colonne c'e' una COPPIA di picchi (x1 della
    # sinistra, x0 della destra): la colonna usa il SUO membro, cosi' i bordi
    # cella veri coincidono con (ca, cb) e i sub-box inset restano esclusi.
    n_x0, n_x1 = {}, {}
    for y0, y1, x0, x1 in hlist:
        n_x0[round(x0 / 3) * 3] = n_x0.get(round(x0 / 3) * 3, 0) + 1
        n_x1[round(x1 / 3) * 3] = n_x1.get(round(x1 / 3) * 3, 0) + 1

    def picco(hist, xa, xb):
        cand = {x: n for x, n in hist.items() if xa < x < xb and n >= 3}
        if not cand:
            return None
        best = max(cand, key=cand.get)
        vicini = [x for x in cand if abs(x - best) <= 8]
        return sum(x * cand[x] for x in vicini) / sum(cand[x] for x in vicini)

    cols = []
    for i in range(7):
        prev_c = centri[i - 1] if i else centri[0] - 2 * (centri[1] - centri[0])
        next_c = centri[i + 1] if i < 6 else centri[6] + 2 * (centri[6] - centri[5])
        ca = picco(n_x0, prev_c + 20, centri[i] - 15)
        cb = picco(n_x1, centri[i] + 15, next_c - 20)
        if ca is None or cb is None:
            passo = centri[1] - centri[0]
            ca = ca if ca is not None else centri[i] - passo / 2
            cb = cb if cb is not None else centri[i] + passo / 2
        cols.append((ca, cb))
    L, R = cols[0][0], cols[-1][1]

    t_of = _lattice_ocr(righe_ocr, L, R)

    # grid_top = fondo della barra colorata (gia' da _barra_e_lettere)
    # covers a +-5px: i SUB-BOX (alternative datate disegnate inset, margini
    # >=6px) non devono generare falsi confini; i bordi cella veri coincidono
    # coi bordi colonna raffinati
    TOL = 5
    # il bordo inferiore e' disegnato per-colonna (fondo sfalsato nella banda
    # notturna): grid_bot = il confine di colonna piu' profondo
    grid_bot = max(y for y0, y1, x0, x1 in hlist for y in (y0, y1)
                   if any(x0 <= ca + TOL and x1 >= cb - TOL for ca, cb in cols))
    if grid_bot - grid_top < 500:
        raise ValueError(f"griglia troppo bassa: {grid_top}..{grid_bot}")

    # il top griglia E' la riga delle 06:00 per costruzione (il fondo della
    # barra puo' cadere qualche px sotto la linea): forza il mapping
    t_interno = t_of
    def t_of(y):                                            # noqa: F811
        return 360 if y <= grid_top + 4 else t_interno(y)

    bounds = []
    for ca, cb in cols:
        bb = {grid_top, grid_bot}
        for y0, y1, x0, x1 in hlist:
            if x0 <= ca + TOL and x1 >= cb - TOL:
                for y in (y0, y1):
                    if grid_top < y < grid_bot:
                        bb.add(y)
        puliti = []
        for t in sorted(bb):
            if puliti and t - puliti[-1] < 8:   # spessori/artefatti
                continue
            puliti.append(t)
        if puliti[-1] != grid_bot and grid_bot - puliti[-1] < 8:
            puliti[-1] = grid_bot
        bounds.append(puliti)

    def separated(x_edge, y):
        return any(abs(x - x_edge) <= 8 and y0 - 2 <= y <= y1 + 2
                   for x, y0, y1 in vlist)

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
            if cj == ci + 1 and abs(u1 - t1) < 8 and abs(u2 - t2) < 8 \
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
                      "top": min(k[1] for k in ks), "bot": max(k[2] for k in ks)})

    # SUB-BOX inset (alternative datate disegnate dentro una cella, es.
    # "SPEC. A SUA IMMAGINE (60') 3/4"): coppie di segmenti H inset nella
    # stessa colonna -> rettangoli, agganciati alla cella che li contiene
    for r in rects:
        r["boxes"] = []
    for ci, (ca, cb) in enumerate(cols):
        cand = sorted((y0, y1, x0, x1) for y0, y1, x0, x1 in hlist
                      if x0 > ca + TOL and x1 < cb - TOL
                      and (x1 - x0) > 0.45 * (cb - ca)
                      and grid_top < y0 < grid_bot)
        usati = set()
        for a in range(len(cand)):
            if a in usati:
                continue
            for b in range(a + 1, len(cand)):
                if b in usati:
                    continue
                ya, yb = cand[a], cand[b]
                larg = min(ya[3], yb[3]) - max(ya[2], yb[2])
                if (18 < yb[0] - ya[1] < 420
                        and larg > 0.8 * min(ya[3] - ya[2], yb[3] - yb[2])):
                    box = (ya[1], yb[0], max(ya[2], yb[2]), min(ya[3], yb[3]), ci)
                    for r in rects:
                        if (ci in r["cols"] and r["top"] - 4 <= ya[0]
                                and yb[1] <= r["bot"] + 4):
                            r["boxes"].append(box)
                            break
                    usati.add(a)
                    usati.add(b)
                    break
    return rects, cols, t_of


# ── grammatica delle decorrenze ──────────────────────────────────────────────
def _dm(g, m, periodo_da, periodo_a):
    g, m = int(g), int(m)
    for anno in {periodo_da.year, periodo_a.year}:
        d = date(anno, m, g)
        if periodo_da - timedelta(days=7) <= d <= periodo_a + timedelta(days=7):
            return d
    raise ValueError(f"data {g}/{m} fuori periodo {periodo_da}..{periodo_a}")


def _ripara_date(t, dow_idx, periodo_da, periodo_a):
    """L'OCR del raster 165dpi perde spesso lo slash delle date ('303'=30/3,
    '185'=18/5, '314'=3/4 con lo slash letto '1'). Ripara i token numerici in
    POSIZIONE di data (fine alternativa o adiacenti a date/trattini) con due
    guardie: data valida nel periodo E, per le celle a colonna singola, giorno
    della settimana coerente. Ambiguo o non validabile = non si tocca."""
    def valida(g, m):
        try:
            d = date(periodo_da.year if m >= periodo_da.month else periodo_a.year,
                     m, g)
        except ValueError:
            return None
        if not (periodo_da - timedelta(days=7) <= d <= periodo_a + timedelta(days=7)):
            return None
        if dow_idx is not None and (d.weekday() + 1) % 7 != dow_idx:
            return None
        return d

    def candidati(tok):
        out = set()
        for cut in (1, 2):
            if cut < len(tok) and len(tok) - cut <= 2:
                if valida(int(tok[:cut]), int(tok[cut:])):
                    out.add((int(tok[:cut]), int(tok[cut:])))
        if len(tok) == 3 and tok[1] in "17":    # slash letto come '1' o '7'
            if valida(int(tok[0]), int(tok[2])):
                out.add((int(tok[0]), int(tok[2])))
        if len(tok) == 4:                       # slash letto come cifra: 1244=12/4
            if valida(int(tok[:2]), int(tok[3:])):
                out.add((int(tok[:2]), int(tok[3:])))
        return out

    def ripara(tok):
        cand = candidati(tok)
        if len(cand) == 1:
            g, mm = cand.pop()
            return f"{g}/{mm}"
        return None

    # '_' = artefatto dei bordi tratteggiati (rompe i \b); '@'/'©' = '®' misletto
    t = t.replace("_", " ").replace("@", "® ").replace("©", "® ")
    toks = t.split()
    ha_data = [bool(re.search(r"\d{1,2}/\d{1,2}", tk) or "-" in tk or tk in "&;,")
               for tk in toks]
    for i, tk in enumerate(toks):
        m = re.fullmatch(r"(\d{2,4})[.,;]?", tk)
        mr = re.fullmatch(r"(\d{2,4})-(\d{2,4})[.,;]?", tk)
        vicino = (i == len(toks) - 1
                  or (i > 0 and ha_data[i - 1])
                  or (i < len(toks) - 1 and ha_data[i + 1]))
        if m and vicino:
            r = ripara(m.group(1))
            if r:
                toks[i] = r + tk[len(m.group(1)):]
                ha_data[i] = True
        elif mr:                                # range con slash persi
            r1, r2 = ripara(mr.group(1)), ripara(mr.group(2))
            if r1 and r2:
                toks[i] = f"{r1}-{r2}" + tk[mr.end(2):]
                ha_data[i] = True
    return " ".join(toks)


def _parse_alt(txt, dow_idx, periodo_da, periodo_a):
    """Un'alternativa -> titolo + finestra/date/eccezioni + flag.
    dow_idx None = cella su piu' giorni (i range diventano solo finestra)."""
    t = " ".join(txt.split())
    out = {"replica": False, "valido_da": None, "valido_a": None,
           "date_list": None, "escluse": None, "t_dich": None,
           "nel_corso": None, "finestra_illeggibile": False}
    m = RE_ORA.match(t)
    if m and int(m.group(1)) < 30:
        out["t_dich"] = int(m.group(1)) * 60 + int(m.group(2))
        t = t[m.end():]
    # "nel corso: 8.00 TG1 (18') ..." = sotto-eventi annotati dentro la cella
    # (box tratteggiati): fuori dal titolo, conservati in nota
    m = re.search(r"\bnel\s+corso\b", t, re.I)
    if m:
        out["nel_corso"] = t[m.end():].strip(" :.-") or None
        t = t[:m.start()]
    if "®" in t:
        out["replica"] = True
        t = t.replace("®", " ")
    t = RE_DUR.sub(" ", t)
    m = RE_ESCL.search(t)
    if m:
        out["escluse"] = [_dm(*d, periodo_da, periodo_a).isoformat()
                          for d in RE_DATA.findall(m.group(1))]
        t = t[:m.start()] + t[m.end():]

    # 1) RANGE (prima di dal/fino: "d. 7/4-12/5" contiene entrambi i pattern)
    dates, windows = [], []
    while (m := RE_RANGE.search(t)):
        g1, m1, g2, m2 = m.groups()
        d2 = _dm(g2, m2, periodo_da, periodo_a)
        d1 = _dm(g1, m1 or m2, periodo_da, periodo_a)
        if dow_idx is None:
            windows.append((d1, d2))
        else:
            d = d1
            while d <= d2:
                if (d.weekday() + 1) % 7 == dow_idx:
                    dates.append(d)
                d += timedelta(days=1)
            if not dates:
                windows.append((d1, d2))
        t = t[:m.start()] + " " + t[m.end():]
    # 2) fino al / dal
    m = RE_FINO.search(t)
    if m:
        out["valido_a"] = _dm(m.group(1), m.group(2), periodo_da, periodo_a)
        t = t[:m.start()] + " " + t[m.end():]
    m = RE_DAL.search(t)
    if m:
        out["valido_da"] = _dm(m.group(1), m.group(2), periodo_da, periodo_a)
        t = t[:m.start()] + " " + t[m.end():]
    # 3) date singole residue
    for g, mm in RE_DATA.findall(t):
        dates.append(_dm(g, mm, periodo_da, periodo_a))
    t = RE_DATA.sub(" ", t)

    if windows and not dates:
        out["valido_da"] = min(w[0] for w in windows)
        out["valido_a"] = max(w[1] for w in windows)
    elif dates:
        tutte = sorted(set(dates))
        out["valido_da"] = min([tutte[0]] + [w[0] for w in windows])
        out["valido_a"] = max([tutte[-1]] + [w[1] for w in windows])
        if len(tutte) > 1 and not windows:
            out["date_list"] = [d.isoformat() for d in tutte]
    assert not (out["valido_da"] and out["valido_a"]
                and out["valido_da"] > out["valido_a"]), f"finestra rovesciata: {txt!r}"

    # data SPEZZATA nel testo ("f. al 24/" col mese illeggibile): la finestra
    # dell'alternativa e' inaffidabile — meglio nessuna finestra e un flag di
    # curatela che un dato sbagliato che sembra giusto (lo slot resta visibile
    # tutti i giorni invece di sparire in quelli sbagliati)
    out["finestra_illeggibile"] = bool(RE_DATA_ROTTA.search(t))
    if out["finestra_illeggibile"]:
        out["valido_da"] = out["valido_a"] = None
        out["date_list"] = None

    t = RE_RESIDUI.sub(" ", t)              # 'd.'/'f. al' rimasti senza data
    out["titolo"] = " ".join(t.split()).strip(" -–;,+.")
    out["generico"] = out["titolo"].rstrip(".").upper() in GENERICI
    return out


def _segmenta_alternative(righe):
    """Righe OCR (testo, conf) di una cella -> [(alternativa, conf_min)]:
    una riga che termina con una data (o range) CHIUDE l'alternativa corrente;
    una riga che inizia con un orario dichiarato ("10.55 SANTA MESSA") ne APRE
    una nuova (sequenza). La confidenza dell'alternativa e' il minimo delle
    sue righe: basta una riga storpiata a renderla da curare."""
    chiude = re.compile(r"\d{1,2}/\d{1,2}\s*[;,]?\s*$")
    apre = re.compile(r"^\d{1,2}[.:]\d{2}\b")
    alts, cur = [], []
    for r, c in righe:
        if cur and apre.match(r):
            alts.append((" ".join(t for t, _ in cur), min(k for _, k in cur)))
            cur = []
        cur.append((r, c))
        if chiude.search(r):
            alts.append((" ".join(t for t, _ in cur), min(k for _, k in cur)))
            cur = []
    if cur:
        alts.append((" ".join(t for t, _ in cur), min(k for _, k in cur)))
    return alts


# ── parser documento ─────────────────────────────────────────────────────────
def _periodo_e_pubblicazione(pdf) -> tuple:
    testo = " ".join((pg.extract_text() or "") for pg in pdf.pages[:4]).upper()
    mesi_rx = "|".join(MESI)
    m = re.search(rf"(\d{{1,2}})\s*({mesi_rx})?\s*[–-]\s*(\d{{1,2}})\s*({mesi_rx})\s*(\d{{4}})", testo)
    if not m:
        raise ValueError("header periodo non trovato")
    anno = int(m.group(5))
    a = date(anno, MESI[m.group(4)], int(m.group(3)))
    da = date(anno, MESI[m.group(2)] if m.group(2) else MESI[m.group(4)], int(m.group(1)))
    if da > a:
        da = da.replace(year=anno - 1)
    mp = re.search(rf"AGGIORNATO\s+AL\s+(\d{{1,2}})\s+({mesi_rx})\s+(\d{{4}})", testo)
    if not mp:
        raise ValueError("data 'Aggiornato al' non trovata")
    pub = date(int(mp.group(3)), MESI[mp.group(2)], int(mp.group(1)))
    return da, a, pub


def _rete_di(img_rgb) -> str | None:
    """Colore del riquadro del logo in testata -> RAI1/RAI2/RAI3.
    (Vision legge solo 'Rai': la cifra e' grafica. I colori del marchio sono
    stabili nei doc 2026: blu (70,73,198), rosso (230,41,47), verde (10,183,115);
    la pagina RaiSport ha un blu piu' scuro (16,34,200) e meta' dei pixel.)"""
    crop = img_rgb.crop((40, 60, 260, 160))
    sat = [p for p in crop.getdata()
           if max(p) > 90 and max(p) - min(p) > 60]
    if len(sat) < 6000:
        return None
    r = sum(p[0] for p in sat) / len(sat)
    g = sum(p[1] for p in sat) / len(sat)
    b = sum(p[2] for p in sat) / len(sat)
    if 40 < r < 110 and 40 < g < 110 and b > 160:
        return "RAI1"
    if r > 180 and g < 90 and b < 90:
        return "RAI2"
    if g > 140 and r < 80 and b < 160:
        return "RAI3"
    return None


def parse_griglia_rai(path: Path, conn, tmpdir: Path | None = None,
                      debug=None) -> dict:
    """Parsa un tvprogram Rai (RAI1/2/3) e inserisce doc + slot base.
    Idempotente per doc_id. debug: dict che riceve le celle per pagina."""
    import tempfile
    doc_id = path.stem
    tmpdir = Path(tmpdir or tempfile.mkdtemp(prefix="rai_ocr_"))
    with pdfplumber.open(str(path)) as pdf:
        periodo_da, periodo_a, pubblicato = _periodo_e_pubblicazione(pdf)
        n_pagine = len(pdf.pages)

    conn.execute("DELETE FROM slot_eccezione WHERE doc_id = ?", [doc_id])
    conn.execute("DELETE FROM slot_programmato WHERE doc_id = ?", [doc_id])
    conn.execute("DELETE FROM doc_sorgente WHERE doc_id = ?", [doc_id])
    conn.execute("INSERT INTO doc_sorgente VALUES (?,?,?,?,?,?,?,?,?)", [
        doc_id, "rai", "griglia", str(path), periodo_da, periodo_a,
        pubblicato, "stampata", "griglie raster OCR (Vision); reti RAI1/2/3"])

    pdfdoc = pdfium.PdfDocument(str(path))
    n_slot, per_rete = 0, {}
    for i in range(1, n_pagine):
        img_rgb = pdfdoc[i].render(scale=SCALE).to_pil().convert("RGB")
        rete = _rete_di(img_rgb)
        if rete is None:
            continue
        img = img_rgb.convert("L")
        png = tmpdir / f"{doc_id}_p{i}.png"
        img_rgb.save(png)                   # OCR sul COLORE: il grayscale
        righe_ocr = _ocr(png)               # degrada Vision (slash persi ecc.)
        rects, cols, t_of = _celle_pagina(img, righe_ocr, img_rgb)

        # tiling per colonna: ogni giorno copre l'intera griglia
        span = t_of(max(r["bot"] for r in rects)) - t_of(min(r["top"] for r in rects))
        for ci in range(7):
            tot = sum(t_of(r["bot"]) - t_of(r["top"]) for r in rects if ci in r["cols"])
            assert abs(tot - span) <= 15, \
                f"{doc_id} {rete} col {ci}: copertura {tot}' != {span}'"

        # testo celle dall'OCR whole-page (le ROI per cella degradano Vision:
        # meno contesto = piu' errori), assegnato per centro del bounding box
        L, R = cols[0][0], cols[-1][1]
        for r in rects:
            r["_linee"] = []
            r["_box_linee"] = {}
        for l in righe_ocr:
            txt = l["text"].strip()
            if not txt or re.fullmatch(r"[-–—_.·\s]+", txt):
                continue                    # artefatti dei bordi tratteggiati
            xc, yc = (l["x0"] + l["x1"]) / 2, (l["top"] + l["bottom"]) / 2
            if not (L < xc < R):
                continue                    # etichette orario delle gutter
            for r in rects:
                if (cols[r["cols"][0]][0] - 2 <= xc < cols[r["cols"][-1]][1] + 2
                        and r["top"] <= yc < r["bot"]):
                    for bi, (by0, by1, bx0, bx1, bci) in enumerate(r["boxes"]):
                        if by0 <= yc < by1 and bx0 - 2 <= xc < bx1 + 2:
                            r["_box_linee"].setdefault(bi, []).append(
                                (l["top"], l["x0"], txt, l["conf"]))
                            break
                    else:
                        r["_linee"].append((l["top"], l["x0"], txt, l["conf"]))
                    break
        for r in rects:
            r["righe"] = [(t, c) for _, _, t, c in sorted(r.pop("_linee"))]
            r["alt_box"] = [(" ".join(t for _, _, t, _ in sorted(ll)),
                             min(c for _, _, _, c in ll), r["boxes"][bi][4])
                            for bi, ll in sorted(r.pop("_box_linee").items())]

        # cella vuota su banda a bordi sfalsati: eredita dal sibling a pari top
        for r in rects:
            if r["righe"]:
                continue
            sib = [s for s in rects if s is not r and s["righe"]
                   and abs(s["top"] - r["top"]) < 8]
            if sib:
                best = min(sib, key=lambda s: abs(s["bot"] - r["bot"]))
                r["righe"] = best["righe"]
                r["ereditata"] = True

        if debug is not None:
            debug[(doc_id, rete)] = [dict(r, t1=t_of(r["top"]), t2=t_of(r["bot"]))
                                     for r in rects]

        for r in sorted(rects, key=lambda r: (r["top"], r["cols"][0])):
            if not r.get("righe"):
                continue
            mask = "".join("1" if c in r["cols"] else "0" for c in range(7))
            t1, t2 = t_of(r["top"]), t_of(r["bot"])
            if t2 <= t1:
                continue
            shift = 0
            if t1 >= 26 * 60:               # banda 02-06 del giorno successivo
                shift, t1, t2 = 1, t1 - 24 * 60, t2 - 24 * 60
                mask = mask[-1] + mask[:-1]
            else:
                t2 = min(t2, 26 * 60)       # il giorno TV finisce alle 02:00
            dow_idx = (r["cols"][0] + shift) % 7 if len(r["cols"]) == 1 else None
            righe_rip = [(_ripara_date(t_, dow_idx, periodo_da, periodo_a), c_)
                         for t_, c_ in r["righe"]]
            specs = [(a, cf, dow_idx, None)
                     for a, cf in _segmenta_alternative(righe_rip)]
            # i sub-box sono slot autonomi con la maschera della SOLA colonna
            # del box: la specificita' base-vs-base del composer li fa vincere
            # nei loro giorni senza toccare le alternanze della cella
            for testo_box, conf_box, bci in r.get("alt_box", []):
                db = (bci + shift) % 7
                specs.append((_ripara_date(testo_box, db, periodo_da, periodo_a),
                              conf_box, db,
                              "".join("1" if c == db else "0" for c in range(7))))
            parsed, pend_nc = [], None
            for alt, conf, dow_a, mask_a in specs:
                try:
                    p = _parse_alt(alt, dow_a, periodo_da, periodo_a)
                except (ValueError, AssertionError):
                    p = {"replica": False, "valido_da": None, "valido_a": None,
                         "date_list": None, "escluse": None, "t_dich": None,
                         "nel_corso": None, "finestra_illeggibile": False,
                         "titolo": " ".join(alt.split()), "generico": False}
                if not p["titolo"]:
                    # sotto-eventi 'nel corso' senza titolo: la nota passa
                    # all'alternativa successiva della cella
                    if p.get("nel_corso"):
                        pend_nc = p["nel_corso"]
                    continue
                if pend_nc and not p.get("nel_corso") and mask_a is None:
                    p["nel_corso"] = pend_nc
                pend_nc = None
                td = p["t_dich"]
                if td is not None and t1 - 15 <= td + 24 * 60 <= t2:
                    td += 24 * 60
                if td is not None and not (t1 - 20 <= td <= t2):
                    td = None
                parsed.append((p, td, mask_a, conf))
            # SEQUENZA a orari dichiarati ("20.30 CINQUE MINUTI ... 20.35
            # AFFARI TUOI"): eventi in successione nella stessa cella, non
            # alternanza. Trigger: >=2 orari distinti, nessuna alternativa
            # datata, al piu' la PRIMA senza orario.
            princ = [it for it in parsed if it[2] is None]
            tds = [it[1] for it in princ]
            sequenza = (len(princ) > 1
                        and all(td is not None for td in tds[1:])
                        and len({td for td in tds if td is not None})
                        == sum(td is not None for td in tds) >= 1
                        and not any(it[0]["valido_da"] or it[0]["valido_a"]
                                    or it[0]["date_list"] for it in princ))
            gruppo = (f"{rete}:{mask}:{t1}"
                      if len(princ) > 1 and not sequenza else None)
            for seq, (p, td, mask_a, conf) in enumerate(parsed):
                ts = td if td is not None else t1
                te = t2
                if sequenza and mask_a is None:
                    # fine = il primo orario dichiarato DOPO il proprio (le
                    # annotazioni tratteggiate non seguono l'ordine di lettura)
                    succ = sorted(it[1] for it in parsed
                                  if it[2] is None and it[1] is not None
                                  and it[1] > ts)
                    if succ:
                        te = succ[0]
                mask_slot = mask_a or mask
                slot_id = f"{doc_id}:{rete}:{mask_slot}:{t1}:{seq}"
                note = {}
                if r.get("ereditata"):
                    note["ereditata"] = True
                if mask_a is not None:
                    note["sub_box"] = True
                if td is not None and td != t1:
                    note["t_dichiarato"] = td * 60
                if p["t_dich"] is None:
                    note["orario_lattice"] = True
                if p.get("nel_corso"):
                    note["nel_corso"] = p["nel_corso"]
                # incertezza di LETTURA (nostra), distinta dall'incertezza di
                # palinsesto (alternanza_irrisolta): finisce in curatela
                if conf < CONF_AFFIDABILE:
                    note["ocr_conf"] = round(conf, 2)
                if p.get("finestra_illeggibile"):
                    note["finestra_illeggibile"] = True
                if (conf < CONF_AFFIDABILE or p.get("finestra_illeggibile")
                        or RE_TITOLO_SOSPETTO.search(p["titolo"])):
                    note["lettura_incerta"] = True
                n_slot += 1
                per_rete[rete] = per_rete.get(rete, 0) + 1
                conn.execute("""INSERT INTO slot_programmato
                    (slot_id, doc_id, rete, kind, dow_mask, valido_da, valido_a,
                     t_start, t_end, fascia, titolo_grezzo, generico, gruppo_alt,
                     prima_tv, replica, tipo, note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
                    slot_id, doc_id, rete, "base", mask_slot,
                    p["valido_da"], p["valido_a"], ts * 60, te * 60,
                    fascia_di(conn, "rai", ts * 60), p["titolo"], p["generico"],
                    gruppo if mask_a is None else None,
                    None, p["replica"], None, json.dumps(note)])
                if p["date_list"]:
                    conn.execute("""INSERT INTO slot_eccezione
                        (ecc_id, doc_id, slot_id, tipo, date_list)
                        VALUES (?,?,?,?,?)""", [
                        f"{slot_id}:solo", doc_id, slot_id, "solo",
                        json.dumps(p["date_list"])])
                if p["escluse"]:
                    conn.execute("""INSERT INTO slot_eccezione
                        (ecc_id, doc_id, slot_id, tipo, date_list)
                        VALUES (?,?,?,?,?)""", [
                        f"{slot_id}:escl", doc_id, slot_id, "escluso",
                        json.dumps(p["escluse"])])
    return {"doc_id": doc_id, "periodo": (str(periodo_da), str(periodo_a)),
            "pubblicato": str(pubblicato), "slot": n_slot, "per_rete": per_rete}
