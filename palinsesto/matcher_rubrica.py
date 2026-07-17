"""
Matcher programma→rubrica: collega le rubriche di vendita dei listini
(rubrica_listino) agli slot del palinsesto programmato (match_rubrica).

Fase 1 — solo doppia copertura:
  Rai: RAI1/2/3, finestre 21/12/2025-5/9/2026 (i tre doc rai_tvprogram 2026).
       Le 12 reti tematiche non hanno griglia: FUORI, contate nel report.
  Publitalia: CAN5/ITA1/RETE4, tutti i doc 2024-2026 (griglia nello stesso doc).
       LA7 ha griglia ma nessun listino: FUORI.

Fatti misurati che guidano il disegno (analisi 17/7/2026):
- l'orario da solo aggancia ~55% delle rubriche Rai (delta mediano 5');
  il ~44% e' un BREAK INTERNO a un contenitore (offset mediano 40-55');
  1 rubrica su 3 e' ambigua a parita' di orario -> serve il titolo.
- previsione.rete lato Publitalia e' la rete-CONTENITORE della pagina, non
  quella del programma (32-36% dei match e' cross-rete): si matcha per doc_id
  su TUTTE le reti della griglia e la rete vera si eredita dallo slot.
- il fuzzy puro accetta falsi positivi tipo 'Anteprima TG5 20.00'->'ANTEPRIMA
  TG4' (0.75): le CIFRE (rete, orari) sono un PRE-FILTRO ESCLUDENTE, mai una
  penalita' -- se i numeri non coincidono il candidato e' fuori prima del
  calcolo di similarita'.
- BREAK-IN e LATE EVENING sono prodotti multi-break (0% match per costruzione);
  'Prima serata'/'Meridiana'/'Access X' sono fasce, non programmi.
"""
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

CSV_LISTINI_RAI = Path(
    "~/Antigravity/palinsesti_pdf/listini_raipub/palinsesto_listini.csv"
).expanduser()
CURATELA_RUBRICA = Path(__file__).parent / "curatela_rubrica.csv"

# ── costanti nominate della cascata (soglie approvate 17/7/2026) ─────────────
TOLLERANZA_ORARIO_SEC = 600      # ±10': p90 dei delta rubrica-slot misurati
SOGLIA_TITOLO = 0.85             # sotto, il titolo non conferma nulla
MARGINE_TITOLO = 0.10            # distacco minimo dal 2o titolo concorrente
CONF_ORARIO_TITOLO = 0.95        # orario ±10' + titolo >=0.85 univoco
CONF_ORARIO_UNICO = 0.85         # orario ±10' con candidato unico
CONF_ORARIO_GIORNI = 0.85        # orario ±10' disambiguato dai giorni esatti
CONF_TITOLO = 0.75               # titolo univoco senza riscontro orario (Pub)
CONF_CONTENIMENTO_TITOLO = 0.75  # break interno CON conferma del titolo
CONF_CONTENIMENTO = 0.60         # break interno senza conferma: fuori dai KPI
CONF_FASCIA = 0.50               # sinonimo di fascia: MAI nei KPI
SOGLIA_KPI = 0.75                # usabile_per_kpi: slot/break_interno >= 0.75
SIM_SOTTOINSIEME = 0.90          # 'AVANTI UN ALTRO' ⊂ 'AVANTI UN ALTRO BONOLIS':
                                 # sottoinsieme di token (>=2) = stesso programma
                                 # con appendici (conduttore, 'Show', 'Lun')

FASE1_RAI_DA, FASE1_RAI_A = date(2025, 12, 21), date(2026, 9, 5)
RETI_RAI_FASE1 = {"RAI1", "RAI2", "RAI3"}
# doppia identita' nel naming dei listini dal 21/12/2025 in poi
ALIAS_RETE = {"RAI MOVIE": "RAIMOVIE", "RAI NEWS": "RAINEWS",
              "RAI PREMIUM": "RAIPREMIUM", "RAI SPORT": "RAISPORT"}

PRODOTTI_NOTI = {"BREAK-IN", "NEWS E SPORT", "GOLDEN MINUTE",
                 "LATE EVENING", "ENTERTAINMENT", "INLOGO"}
PRODOTTI_MULTI = {"BREAK-IN", "LATE EVENING"}   # prodotti di break multi-slot
# nomi commerciali che identificano intervalli/contenitori, non programmi
FASCE_COMMERCIALI = {
    "prima serata", "seconda serata", "preserale", "meridiana", "pomeriggio",
    "day", "daytime", "notte", "gransera", "gransera 2", "prime time",
    "seratissima", "cinema 1", "cinema 2", "news 1", "fiction 5",
    "sera 4", "sera 5", "sera i1", "night5", "nightone", "5", "1", "4",
}
RE_SUFFISSO_RAI = re.compile(r"\s+(a|b|c|start|chiusura|sabato|domenica)$")
RE_TEMPO = re.compile(r"\b(\d{1,2})[.:](\d{2})\b")
RE_DURATA = re.compile(r"\(\s*\d+\s*[\"”']?\s*\)")
_ACCENTI = str.maketrans("ÀÈÉÌÍÎÒÓÙÚ", "AEEIIIOOUU")


# ── normalizzazione condivisa ────────────────────────────────────────────────
def norm_chiave(s: str) -> str:
    """Chiave di join con previsione.posizione: case-fold + spazi collassati
    (i listini variano il case della stessa rubrica tra un'edizione e l'altra)."""
    return " ".join(str(s or "").split()).lower()


def _tempo_tv(h: int, mi: int) -> int:
    if h < 2:                    # oltre mezzanotte -> giorno televisivo 02-26h
        h += 24
    return h * 3600 + mi * 60


def parse_orario_listino(txt: str) -> int | None:
    """Orari del CSV Rai, 3 forme: 'HH:MM' (anche con zero spurio '010:00'),
    datetime Excel '1900-01-01 00:10:00' (post-mezzanotte), altrimenti scarto
    esplicito (multi-fuso RAI ITALIA)."""
    t = str(txt or "").strip()
    if not t:
        return None
    m = re.fullmatch(r"\d{4}-01-01 (\d{2}):(\d{2}):\d{2}", t)
    if m:
        return _tempo_tv(int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"0*(\d{1,2})[.:](\d{2})", t)
    if m:
        return _tempo_tv(int(m.group(1)), int(m.group(2)))
    return None


def _norm_titolo(s: str) -> str:
    """Testo per la similarita': maiuscole senza accenti, via durate '(30\")',
    orari e junk OCR (token >2 cifre, misti tipo '10Л'); restano le sigle
    alfanumeriche (TG1, K2) e le cifre corte dei titoli ('4 DI SERA')."""
    s = str(s or "").upper().translate(_ACCENTI)
    s = RE_DURATA.sub(" ", s)
    s = RE_TEMPO.sub(" ", s)
    toks = []
    for t in re.split(r"[^A-Z0-9]+", s):
        if not t:
            continue
        if t.isalpha() or re.fullmatch(r"[A-Z]{1,6}\d{1,2}", t) \
                or (t.isdigit() and len(t) <= 2):
            toks.append(t)
    return " ".join(toks)


def _alternative(titolo: str) -> list[str]:
    """Un titolo di slot/listino puo' elencare alternanze: 'X / Y', 'X + Y'."""
    out = []
    for alt in re.split(r"\s*[/+]\s*", str(titolo or "")):
        n = _norm_titolo(alt)
        if n:
            out.append(n)
    return out or [""]


def _cifre(norm: str) -> list[str]:
    return sorted(re.findall(r"\d+", norm))


def _cifre_compatibili(a: str, b: str) -> bool:
    """PRE-FILTRO ESCLUDENTE (requisito esplicito): se entrambi i lati hanno
    cifre e non coincidono esatte, il candidato e' fuori PRIMA del fuzzy.
    E' il caso 'Anteprima TG5 20.00' vs 'ANTEPRIMA TG4'."""
    ca, cb = _cifre(a), _cifre(b)
    return not ca or not cb or ca == cb


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _punteggio(a: str, b: str) -> float:
    """Similarita' con la regola del sottoinsieme: se i token di un lato
    (almeno 2) sono tutti contenuti nell'altro, e' lo stesso programma con
    appendici — il ratio puro li boccerebbe ('LE IENE' vs 'LE IENE SHOW')."""
    v = _sim(a, b)
    ta, tb = set(a.split()), set(b.split())
    if (len(ta) >= 2 and ta <= tb) or (len(tb) >= 2 and tb <= ta):
        v = max(v, SIM_SOTTOINSIEME)
    return v


def _dow_overlap(a: str, b: str) -> bool:
    if not a or not b or len(a) != 7 or len(b) != 7:
        return True
    return any(x == "1" and y == "1" for x, y in zip(a, b))


def _hhmm(t) -> str:
    return f"{int(t)//3600:02d}:{int(t)%3600//60:02d}" if t is not None else "-"


# ── registro rubriche: Rai (dal CSV dei tabellari) ───────────────────────────
RE_NOTA_DAL = re.compile(r"\bdal\s+(\d{1,2})/(\d{1,2})")
RE_NOTA_AL = re.compile(r"\b(?:fino\s+al|al)\s+(?:al\s+)?(\d{1,2})/(\d{1,2})")


def _finestra_da_nota(nota: str, da: str, a: str) -> tuple[str, str]:
    """'dal 22/4 al 13/5' nella colonna note del CSV RESTRINGE la finestra
    della rubrica (senza, il matcher aggancia la variante di gruppo_alt
    sbagliata). I segmenti 'no ...' sono esclusioni-giorno, non finestre; i
    numeri puri sono richiami a pie' di pagina."""
    def dentro(g: int, m: int) -> str | None:
        for anno in {da[:4], a[:4]}:
            iso = f"{anno}-{m:02d}-{g:02d}"
            if da <= iso <= a:
                return iso
        return None
    nota = re.sub(r"\([^)]*\)", " ", nota or "")
    for seg in re.split(r"[.;]", nota):
        if re.match(r"\s*no\b", seg, flags=re.I):
            continue
        m = RE_NOTA_DAL.search(seg)
        if m:
            iso = dentro(int(m.group(1)), int(m.group(2)))
            if iso:
                da = max(da, iso)
        m = RE_NOTA_AL.search(re.sub(RE_NOTA_DAL.pattern, " ", seg))
        if m:
            iso = dentro(int(m.group(1)), int(m.group(2)))
            if iso:
                a = min(a, iso)
    return da, a


def _rubriche_rai() -> list[dict]:
    if not CSV_LISTINI_RAI.exists():
        raise FileNotFoundError(f"CSV listini Rai non trovato: {CSV_LISTINI_RAI}")
    grezzi = defaultdict(list)
    for r in csv.DictReader(open(CSV_LISTINI_RAI, encoding="utf-8")):
        rete = ALIAS_RETE.get(r["rete"].strip(), r["rete"].strip())
        grezzi[(rete, norm_chiave(r["rubrica_vendita"]), r["periodo"])].append(r)
    # per (rete, rubrica, stagione) vince l'aggiornamento piu' recente
    per_chiave = {}
    for (rete, pos, _periodo), righe in grezzi.items():
        righe.sort(key=lambda r: r["aggiornamento"] or "")
        file_vince = righe[-1]["file"]
        righe = [r for r in righe if r["file"] == file_vince]
        base = righe[0]
        da = min(r["data_inizio"] for r in righe if r["data_inizio"])
        a = max(r["data_fine"] for r in righe if r["data_fine"])
        nota_csv = next((r["note"].strip() for r in righe if r["note"].strip()), "")
        if nota_csv:
            da, a = _finestra_da_nota(nota_csv, da, a)
        m = RE_SUFFISSO_RAI.search(pos)
        chiave = (rete, pos, da)
        cand = {
            "sorgente": "rai_listino", "rete_previsione": rete,
            "posizione_norm": pos, "tipo_giorno": "tutti",
            "periodo_da": da, "periodo_a": a,
            "posizione_orig": base["rubrica_vendita"],
            "programma": base["programma"].strip() or None,
            "prodotto": None,
            "t_ancora": parse_orario_listino(base["orario_inizio"]),
            "giorni_mask": (base["giorni_mask"] or "").strip() or None,
            "famiglia": RE_SUFFISSO_RAI.sub("", pos) if m else pos,
            "suffisso": m.group(1) if m else "",
            "content": base["content"].strip() or None,
            "note": {"file": base["file"],
                     "orario_grezzo": base["orario_inizio"],
                     **({"nota_listino": nota_csv} if nota_csv else {})},
            "aggiornamento": base["aggiornamento"],
        }
        gia = per_chiave.get(chiave)
        if gia is None or cand["aggiornamento"] > gia["aggiornamento"]:
            per_chiave[chiave] = cand
    return list(per_chiave.values())


# ── registro rubriche: Publitalia (dalle posizioni di previsione) ────────────
_RE_PREFISSO_PUB = re.compile(r"^([A-Z][A-Z \-]+?):\s*(.*)$")
_SUFFISSI_PUB = [(re.compile(r"\s*\[primissima\]\s*$", re.I), "[primissima]"),
                 (re.compile(r"\s+FPT$"), "fpt"),
                 (re.compile(r"\s+saluti$", re.I), "saluti"),
                 (re.compile(r"\s+promozione$", re.I), "promozione"),
                 (re.compile(r"\s*\(TZ[^)]*\)\**\s*$", re.I), "tz")]


def _scomponi_posizione_pub(pos: str) -> dict:
    prodotto, base = None, pos.strip()
    m = _RE_PREFISSO_PUB.match(base)
    if m and m.group(1).strip() in PRODOTTI_NOTI:
        prodotto, base = m.group(1).strip(), m.group(2).strip()
    base = re.sub(r"^golden minute\s+", "", base, flags=re.I)
    # componenti di break: '(di cui TG5 8:00 - Tgcom)' -> 'TG5 8:00';
    # 'TG5 8.00-brk 8.30' -> 'TG5 8.00' (il break di un'edizione del TG)
    m = re.fullmatch(r"\(di cui\s+(.*?)(?:\s*-\s*Tgcom)?\)", base, flags=re.I)
    if m:
        base = m.group(1).strip()
    base = re.sub(r"\s*-\s*brk\s*\d{1,2}[.:]\d{2}$", "", base, flags=re.I)
    # 'Anteprima TG5 20.00': il break si vende addosso al programma adiacente
    anteprima = bool(re.match(r"^anteprima\s+", base, flags=re.I))
    if anteprima:
        base = re.sub(r"^anteprima\s+", "", base, flags=re.I)
    suffissi = []
    cambiato = True
    while cambiato:
        cambiato = False
        for rx, nome in _SUFFISSI_PUB:
            nuovo = rx.sub("", base)
            if nuovo != base:
                base, cambiato = nuovo.strip(), True
                suffissi.insert(0, nome)
    mt = RE_TEMPO.search(base)
    t = _tempo_tv(int(mt.group(1)), int(mt.group(2))) if mt else None
    # 'Sera 5 La ruota della fortuna' (doc 2024-25): il programma e' dopo
    # il contenitore; 'Sera 5' nudo resta una fascia
    ms = re.match(r"^(sera\s+(?:5|4|i1))\s+(\S.*)$", base, flags=re.I)
    if ms:
        base = ms.group(2).strip()
    return {"prodotto": prodotto, "base": base, "t_ancora": t,
            "suffisso": " ".join(suffissi), "anteprima": anteprima,
            "fascia": (norm_chiave(base) in FASCE_COMMERCIALI
                       # 'acces rete 4': typo ricorrente nei listini
                       or re.match(r"^acces{1,2}\b", norm_chiave(base)) is not None)}


def _rubriche_publitalia(conn) -> list[dict]:
    righe = conn.execute("""
        SELECT DISTINCT p.doc_id, coalesce(p.rete,''), p.posizione,
               coalesce(NULLIF(p.tipo_giorno,''),'tutti'), d.periodo_da, d.periodo_a
        FROM previsione p JOIN doc_sorgente d USING (doc_id)
        WHERE p.sorgente='publitalia_listino'""").fetchall()
    per_chiave = {}
    for doc_id, rete, pos, tg, da, a in righe:
        sc = _scomponi_posizione_pub(pos)
        chiave = (rete, norm_chiave(pos), tg, da)
        if chiave in per_chiave:
            continue                      # varianti di solo case: prima vince
        per_chiave[chiave] = {
            "sorgente": "publitalia_listino", "rete_previsione": rete,
            "posizione_norm": norm_chiave(pos), "tipo_giorno": tg,
            "periodo_da": da, "periodo_a": a,
            "posizione_orig": pos, "programma": sc["base"] or None,
            "prodotto": sc["prodotto"], "t_ancora": sc["t_ancora"],
            "giorni_mask": None, "famiglia": norm_chiave(sc["base"]),
            "suffisso": sc["suffisso"], "content": None,
            "note": {"doc_id": doc_id, "fascia": sc["fascia"],
                     **({"alt_anteprima": f"Anteprima {sc['base']}"}
                        if sc["anteprima"] else {})},
        }
    return list(per_chiave.values())


def ricostruisci_rubriche(conn) -> dict:
    rai = _rubriche_rai()
    pub = _rubriche_publitalia(conn)
    conn.execute("DELETE FROM rubrica_listino")
    for r in rai + pub:
        conn.execute("""INSERT INTO rubrica_listino VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", [
            r["sorgente"], r["rete_previsione"], r["posizione_norm"],
            r["tipo_giorno"], r["periodo_da"], r["periodo_a"],
            r["posizione_orig"], r["programma"], r["prodotto"],
            r["t_ancora"], r["giorni_mask"], r["famiglia"], r["suffisso"],
            r["content"], json.dumps(r["note"])])
    return {"rai": len(rai), "publitalia": len(pub)}


# ── candidati slot ───────────────────────────────────────────────────────────
def _slot_rai(conn):
    """Slot base Rai con finestra efficace: lo slot puo' restringere la
    finestra del doc, mai allargarla; se manca, vale il periodo del doc."""
    out = defaultdict(list)
    for r in conn.execute("""
        SELECT s.slot_id, s.rete, s.dow_mask, s.t_start, s.t_end,
               s.titolo_grezzo, s.gruppo_alt,
               coalesce(s.valido_da, d.periodo_da), coalesce(s.valido_a, d.periodo_a)
        FROM slot_programmato s JOIN doc_sorgente d USING (doc_id)
        WHERE s.doc_id LIKE 'rai_tvprogram_%' AND s.kind='base'
          AND s.t_start IS NOT NULL AND s.t_end IS NOT NULL""").fetchall():
        out[r[1]].append({
            "slot_id": r[0], "rete": r[1], "dow_mask": r[2],
            "t_start": r[3], "t_end": r[4], "titolo": r[5], "gruppo": r[6],
            "da": r[7], "a": r[8], "alts": _alternative(r[5])})
    return out


def _slot_publitalia(conn):
    """Slot griglia per doc listino: la validita' e' il periodo del doc
    (valido_da/a e data sono NULL su questi slot: senza il fallback il join
    temporale si svuota in silenzio)."""
    out = defaultdict(list)
    for r in conn.execute("""
        SELECT s.doc_id, s.slot_id, s.rete, s.dow_mask, s.t_start, s.t_end,
               s.titolo_grezzo, s.gruppo_alt
        FROM slot_programmato s
        WHERE s.doc_id LIKE 'publitalia_listino_%' AND s.kind='base'""").fetchall():
        out[r[0]].append({
            "slot_id": r[1], "rete": r[2], "dow_mask": r[3],
            "t_start": r[4], "t_end": r[5], "titolo": r[6], "gruppo": r[7],
            "alts": _alternative(r[6])})
    return out


# ── cascata di match ─────────────────────────────────────────────────────────
def _vince_per_titolo(rubrica_alts: list[str], slots: list[dict]):
    """Vincitori PER ALTERNATIVA della rubrica: il listino dichiara spesso
    un'alternanza ('Tv Movie/Film/Rai doc') e ogni alternativa aggancia il SUO
    titolo di slot — non e' ambiguita', e' il caso M:N. L'ambiguita' vera e'
    una singola alternativa contesa tra titoli DIVERSI entro il margine.
    Ritorna (titolo_rappresentativo, sim_max, slot_vinti) o (None, best, [])."""
    per_titolo = defaultdict(list)        # titolo slot -> [(sim, ra, slot)]
    for s in slots:
        for alt in set(s["alts"]):
            for ra in rubrica_alts:
                if not _cifre_compatibili(ra, alt):
                    continue             # pre-filtro cifre: escluso, niente fuzzy
                per_titolo[alt].append((_punteggio(ra, alt), ra, s))
    vinti, sim_max, best_sotto = {}, 0.0, 0.0
    for ra in rubrica_alts:
        punteggi = {}                     # titolo -> sim di QUESTA alternativa
        for alt, triple in per_titolo.items():
            vs = [v for v, r, _ in triple if r == ra]
            if vs:
                punteggi[alt] = max(vs)
        if not punteggi:
            continue
        ordinati = sorted(punteggi.items(), key=lambda kv: -kv[1])
        t1, s1 = ordinati[0]
        # concorrente vero = titolo DIVERSO entro il margine; due titoli
        # simili >= soglia TRA LORO sono lo stesso programma letto male
        # dall'OCR ('AFFARI TUO' vs 'AFFARI TUOI') e si uniscono al gruppo
        concorrenti = [t2 for t2, s2 in ordinati[1:]
                       if s2 > s1 - MARGINE_TITOLO and _sim(t1, t2) < SOGLIA_TITOLO]
        if s1 >= SOGLIA_TITOLO and not concorrenti:
            sim_max = max(sim_max, s1)
            gruppo = [t2 for t2, s2 in ordinati
                      if s2 >= SOGLIA_TITOLO and _sim(t1, t2) >= SOGLIA_TITOLO]
            for t2 in gruppo or [t1]:
                for v, r, s in per_titolo[t2]:
                    if r == ra:
                        vinti.setdefault(id(s), (t1, s))
        else:
            best_sotto = max(best_sotto, s1)
    if vinti:
        titoli = {t for t, _ in vinti.values()}
        return sorted(titoli)[0], sim_max, [s for _, s in vinti.values()]
    return None, best_sotto, []


def _monotitolo(slots: list[dict]) -> bool:
    """True se tutti gli slot raccontano lo stesso programma (gli split per
    giorno del TG): piu' candidati ma nessuna ambiguita' reale."""
    return len({s["alts"][0] for s in slots}) == 1


def _selezione_cifre(r_alts: list[str], vicini: list[dict]) -> list[dict] | None:
    """Le cifre come selettore positivo, oltre che come esclusione: se la
    rubrica dichiara numeri ('TG5') e tra i vicini un solo programma li porta
    tutti nel titolo, quello e' identificato dalle cifre — il fuzzy non serve."""
    cr = sorted({c for ra in r_alts for c in _cifre(ra)})
    if not cr:
        return None
    con = [s for s in vicini if any(_cifre(a) == cr for a in s["alts"])]
    if con and _monotitolo(con):
        return con
    return None


def _match_una_rai(rb: dict, slots: list[dict]) -> list[dict]:
    """-> lista righe match (M:N) oppure [] = 'nessuno' (curatela)."""
    r_alts = _alternative(rb["programma"] or rb["posizione_orig"])
    cand = [s for s in slots
            if _dow_overlap(rb["giorni_mask"], s["dow_mask"])
            and s["da"] <= (rb["periodo_a"] or rb["periodo_da"])
            and rb["periodo_da"] <= s["a"]]
    t = rb["t_ancora"]
    if t is None:
        return []
    vicini = [s for s in cand if abs(t - s["t_start"]) <= TOLLERANZA_ORARIO_SEC]
    if vicini:
        titolo, sim, vinti = _vince_per_titolo(r_alts, vicini)
        if titolo:
            return [_riga(rb, s, "slot", "orario+titolo", CONF_ORARIO_TITOLO,
                          {"sim": round(sim, 3), "delta_min": (t - s["t_start"]) // 60})
                    for s in vinti]
        scelti = (vicini if len(vicini) == 1 or _monotitolo(vicini)
                  else _selezione_cifre(r_alts, vicini))
        if scelti:
            return [_riga(rb, s, "slot", "orario", CONF_ORARIO_UNICO,
                          {"delta_min": (t - s["t_start"]) // 60,
                           "sim": round(sim, 3)})
                    for s in scelti]
        stessi_giorni = [s for s in vicini if s["dow_mask"] == rb["giorni_mask"]]
        titoli_sg = {s["alts"][0] for s in stessi_giorni}
        if stessi_giorni and len(titoli_sg) == 1:
            # guardia: se un ALTRO vicino somiglia al programma dichiarato piu'
            # del gruppo scelto, i giorni esatti non bastano (il caso R3 P.M.
            # agganciato al blocco OCR-junk mentre IN CAMMINO era a delta 0)
            def best(gruppo):
                return max((_punteggio(ra, a) for s in gruppo for a in s["alts"]
                            for ra in r_alts), default=0.0)
            altri = [s for s in vicini if s not in stessi_giorni]
            if not altri or best(altri) < best(stessi_giorni) + MARGINE_TITOLO:
                return [_riga(rb, s, "slot", "orario+giorni", CONF_ORARIO_GIORNI,
                              {"delta_min": (t - s["t_start"]) // 60})
                        for s in stessi_giorni]
        return []                        # ambiguo: curatela, mai a caso
    contenitori = [s for s in cand if s["t_start"] <= t < s["t_end"]]
    if contenitori:
        titolo, sim, vinti = _vince_per_titolo(r_alts, contenitori)
        if titolo:
            return [_riga(rb, s, "break_interno", "contenimento+titolo",
                          CONF_CONTENIMENTO_TITOLO,
                          {"sim": round(sim, 3),
                           "offset_min": (t - s["t_start"]) // 60})
                    for s in vinti]
        return [_riga(rb, s, "break_interno", "contenimento", CONF_CONTENIMENTO,
                      {"offset_min": (t - s["t_start"]) // 60,
                       "titolo_slot": s["titolo"][:60]})
                for s in contenitori]
    return []


def _match_una_pub(rb: dict, slots: list[dict]) -> list[dict]:
    if rb["prodotto"] in PRODOTTI_MULTI:
        return [_riga(rb, None, "prodotto_multi", "prodotto", None,
                      {"prodotto": rb["prodotto"]})]
    if rb["note"].get("fascia"):
        return [_riga(rb, None, "fascia", "sinonimo", CONF_FASCIA,
                      {"fascia": rb["programma"]})]
    r_alts = _alternative(rb["programma"] or rb["posizione_orig"])
    if rb["note"].get("alt_anteprima"):
        # 'Anteprima Tg4 Sera': il token 'Anteprima' e' proprio quello che
        # punta allo slot ANTEPRIMA TG4 — si prova anche la forma integrale
        r_alts += _alternative(rb["note"]["alt_anteprima"])
    t = rb["t_ancora"]
    titolo, sim, vinti = _vince_per_titolo(r_alts, slots)
    if titolo:
        note = {"sim": round(sim, 3)}
        if t is not None:
            # il vincolo temporale si applica SLOT PER SLOT: il gruppo di
            # titolo unisce le edizioni di un programma (TG5 8/13/20) e
            # l'orario nel nome della rubrica e' cio' che le distingue
            vic = [s for s in vinti if abs(t - s["t_start"]) <= TOLLERANZA_ORARIO_SEC]
            if vic:
                return [_riga(rb, s, "slot", "orario+titolo", CONF_ORARIO_TITOLO,
                              {**note, "delta_min": (t - s["t_start"]) // 60})
                        for s in vic]
            cont = [s for s in vinti if s["t_start"] <= t < s["t_end"]]
            if cont:
                return [_riga(rb, s, "break_interno", "contenimento+titolo",
                              CONF_CONTENIMENTO_TITOLO,
                              {**note, "offset_min": (t - s["t_start"]) // 60})
                        for s in cont]
            return []                    # l'orario contraddice il titolo: curatela
        return [_riga(rb, s, "slot", "titolo", CONF_TITOLO, note) for s in vinti]
    if t is not None:
        # NIENTE fallback orario generico cross-rete: la rubrica Publitalia
        # nomina un programma, e una coincidenza di minuto su un'altra rete
        # e' spuria ('The wall 19:20' -> 'CSI: MIAMI'). Solo la selezione per
        # cifre (evidenza positiva: 'TG5' nel nome e nel titolo) e' ammessa.
        vicini = [s for s in slots
                  if abs(t - s["t_start"]) <= TOLLERANZA_ORARIO_SEC
                  and all(_cifre_compatibili(ra, a)
                          for ra in r_alts for a in s["alts"])]
        scelti = _selezione_cifre(r_alts, vicini)
        if scelti:
            return [_riga(rb, s, "slot", "orario", CONF_ORARIO_UNICO,
                          {"delta_min": (t - s["t_start"]) // 60})
                    for s in scelti]
    return []


def _riga(rb: dict, slot: dict | None, livello: str, metodo: str,
          conf: float | None, note: dict) -> dict:
    return {
        "sorgente": rb["sorgente"], "rete_previsione": rb["rete_previsione"],
        "posizione_norm": rb["posizione_norm"], "tipo_giorno": rb["tipo_giorno"],
        "periodo_da": rb["periodo_da"], "periodo_a": rb["periodo_a"],
        "slot_id": slot["slot_id"] if slot else "",
        "rete_slot": slot["rete"] if slot else None,
        "livello": livello, "metodo": metodo, "confidenza": conf,
        "usabile_per_kpi": (livello in ("slot", "break_interno")
                            and conf is not None and conf >= SOGLIA_KPI),
        "note": note,
    }


def _demote_collisioni_sottoinsieme(righe: list[dict], rubriche_map: dict,
                                    slot_alts: dict) -> list[dict]:
    """Guardia anti-collisione della regola sottoinsieme: se DUE famiglie
    diverse ('le iene show' e 'le iene speciale') reclamano lo STESSO slot
    entrambe perche' il titolo di griglia ('LE IENE') e' sottoinsieme di
    entrambe, i token extra distinguono prodotti che la griglia non sa
    distinguere: tiene la famiglia col ratio GREZZO migliore, l'altra va in
    curatela (mai assegnata a caso)."""
    def chiave_rb(r):
        return (r["sorgente"], r["rete_previsione"], r["posizione_norm"],
                r["tipo_giorno"], r["periodo_da"])

    def boost_da_slot(rb, alts):
        r_alts = _alternative(rb["programma"] or rb["posizione_orig"])
        for alt in alts:
            ta = set(alt.split())
            if len(ta) >= 2 and any(ta < set(ra.split()) for ra in r_alts):
                return True
        return False

    def ratio_grezzo(rb, alts):
        r_alts = _alternative(rb["programma"] or rb["posizione_orig"])
        return max((_sim(ra, a) for ra in r_alts for a in alts), default=0.0)

    gruppi = defaultdict(list)
    for r in righe:
        if r["slot_id"] and r["metodo"] in ("titolo", "contenimento+titolo",
                                            "orario+titolo"):
            gruppi[(r["sorgente"], r["slot_id"], r["tipo_giorno"],
                    r["periodo_da"])].append(r)
    scarta = set()
    for rr in gruppi.values():
        per_famiglia = defaultdict(list)
        for r in rr:
            rb = rubriche_map.get(chiave_rb(r))
            if rb and boost_da_slot(rb, slot_alts.get(r["slot_id"], [])):
                per_famiglia[rb["famiglia"]].append((r, rb))
        if len(per_famiglia) < 2:
            continue
        migliore = max(per_famiglia, key=lambda f: max(
            ratio_grezzo(rb, slot_alts.get(r["slot_id"], []))
            for r, rb in per_famiglia[f]))
        for fam, coppie in per_famiglia.items():
            if fam != migliore:
                scarta |= {id(r) for r, _ in coppie}
    return [r for r in righe if id(r) not in scarta]


# ── esecuzione ───────────────────────────────────────────────────────────────
def esegui_match(conn) -> dict:
    rubriche = conn.execute("SELECT * FROM rubrica_listino").fetchall()
    cols = [d[0] for d in conn.description]
    rubriche = [dict(zip(cols, r)) for r in rubriche]
    for rb in rubriche:
        rb["note"] = json.loads(rb["note"]) if rb["note"] else {}

    curati = {tuple(r) for r in conn.execute("""
        SELECT DISTINCT sorgente, rete_previsione, posizione_norm,
               tipo_giorno, periodo_da
        FROM match_rubrica WHERE curato""").fetchall()}
    conn.execute("DELETE FROM match_rubrica WHERE NOT curato")

    slot_rai = _slot_rai(conn)
    slot_pub = _slot_publitalia(conn)
    finestre_doc = conn.execute("""
        SELECT periodo_da, periodo_a FROM doc_sorgente
        WHERE doc_id LIKE 'rai_tvprogram_%'""").fetchall()
    righe, senza, fuori = [], [], Counter()
    for rb in rubriche:
        chiave = (rb["sorgente"], rb["rete_previsione"], rb["posizione_norm"],
                  rb["tipo_giorno"], rb["periodo_da"])
        if chiave in curati:
            continue
        if rb["sorgente"] == "rai_listino":
            if rb["rete_previsione"] not in RETI_RAI_FASE1:
                fuori["rai: rete tematica senza griglia"] += 1
                continue
            # fuori se la finestra non tocca fase 1 O nessun doc griglia la
            # copre (es. 21/12-3/1: listino si', tvprogram no) — inutile in
            # curatela, non c'e' niente a cui agganciarla
            fine = rb["periodo_a"] or rb["periodo_da"]
            if fine < FASE1_RAI_DA or rb["periodo_da"] > FASE1_RAI_A \
                    or not any(da <= fine and rb["periodo_da"] <= a
                               for da, a in finestre_doc):
                fuori["rai: finestra senza doc tvprogram"] += 1
                continue
            out = _match_una_rai(rb, slot_rai.get(rb["rete_previsione"], []))
        else:
            doc = rb["note"].get("doc_id")
            out = _match_una_pub(rb, slot_pub.get(doc, []))
        if out:
            righe += out
        else:
            senza.append(rb)

    slot_alts = {s["slot_id"]: s["alts"]
                 for gruppo in list(slot_rai.values()) + list(slot_pub.values())
                 for s in gruppo}
    rubriche_map = {(rb["sorgente"], rb["rete_previsione"], rb["posizione_norm"],
                     rb["tipo_giorno"], rb["periodo_da"]): rb for rb in rubriche}
    prima = {(r["sorgente"], r["rete_previsione"], r["posizione_norm"],
              r["tipo_giorno"], r["periodo_da"]) for r in righe}
    righe = _demote_collisioni_sottoinsieme(righe, rubriche_map, slot_alts)
    dopo = {(r["sorgente"], r["rete_previsione"], r["posizione_norm"],
             r["tipo_giorno"], r["periodo_da"]) for r in righe}
    for chiave in prima - dopo:           # famiglie retrocesse: in curatela
        if chiave in rubriche_map:
            senza.append(rubriche_map[chiave])

    viste = set()
    for r in righe:
        k = (r["sorgente"], r["rete_previsione"], r["posizione_norm"],
             r["tipo_giorno"], r["periodo_da"], r["slot_id"])
        if k in viste:
            continue
        viste.add(k)
        conn.execute("INSERT INTO match_rubrica VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            r["sorgente"], r["rete_previsione"], r["posizione_norm"],
            r["tipo_giorno"], r["periodo_da"], r["periodo_a"], r["slot_id"],
            r["rete_slot"], r["livello"], r["metodo"], r["confidenza"],
            r["usabile_per_kpi"], False, json.dumps(r["note"])])

    with open(CURATELA_RUBRICA, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sorgente", "rete_previsione", "posizione", "tipo_giorno",
                    "periodo_da", "orario", "giorni_mask", "programma_listino"])
        for rb in sorted(senza, key=lambda r: (r["sorgente"],
                                               r["rete_previsione"],
                                               str(r["t_ancora"]))):
            w.writerow([rb["sorgente"], rb["rete_previsione"],
                        rb["posizione_orig"], rb["tipo_giorno"],
                        rb["periodo_da"], _hhmm(rb["t_ancora"]),
                        rb["giorni_mask"] or "", rb["programma"] or ""])

    rubriche_ok = {(r["sorgente"], r["rete_previsione"], r["posizione_norm"],
                    r["tipo_giorno"], r["periodo_da"]) for r in righe}
    return {"righe_match": len(viste),
            "rubriche_collegate": len(rubriche_ok),
            "senza_match": len(senza), "fuori_fase1": dict(fuori),
            "per_livello": Counter(r["livello"] for r in righe),
            "per_metodo": Counter(r["metodo"] for r in righe),
            "kpi_ok": sum(1 for r in righe if r["usabile_per_kpi"]),
            "curatela": str(CURATELA_RUBRICA)}
