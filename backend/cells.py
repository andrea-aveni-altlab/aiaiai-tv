"""
Modello a celle atomiche componibili per audience_cache.

Tre blocchi, 34 celle, su DUE partizioni indipendenti dello stesso universo 4+:
 - partizione 'age' = blocco kids (1 cella, classi 1-4) + blocco demo
   (28 celle = 7 classi adulte 5-11 × 2 sessi × 2 stati RA);
 - partizione 'cse' = blocco cse (5 celle, livelli socio-economici 1-5).

Entro UNA partizione le celle sono disgiunte per individuo → sommabili. Le due
partizioni tassellano lo stesso universo in modo ortogonale (una persona sta in
una cella 'age' E in una cella 'cse') → sommare celle di partizioni diverse
doppia-conta le persone. Regola invariante: un target somma celle di una sola
partizione.

audience_cache memorizza per (evento × cella) i tre ingredienti grezzi
(num_audience, den_auditel, den_reale, copertura), MAI la share divisa. Un
target — preset o custom — è un insieme di celle; la ricomposizione a
query-time è SUM(num)/SUM(den) per la share e SUM(copertura) per la copertura,
tutto entro una partizione. Vedi event_stage().
"""
from dataclasses import dataclass

# ── Partizioni e blocchi ──────────────────────────────────────────────────────
PART_AGE = "age"
PART_CSE = "cse"
BLOCK_KIDS = "kids"
BLOCK_DEMO = "demo"
BLOCK_CSE  = "cse"

ADULT_CLASSES = range(5, 12)   # nuove_classi_eta 5..11 (15-64 = 5-10, 65+ = 11)
SESSI     = (1, 2)             # 1 = uomini, 2 = donne
RA_STATI  = (0, 1)            # resp_acquisto: 1 = responsabile acquisti
CSE_LEVELS = range(1, 6)       # cse 1..5 (alta = 4,5)


@dataclass(frozen=True)
class Cell:
    cell_id: str            # univoco globale, prefisso per blocco: 'K', 'D07_1_1', 'C3'
    partizione: str         # 'age' | 'cse'  → unità di sommabilità
    block: str              # 'kids' | 'demo' | 'cse'
    pop_where: str          # predicato su individui (membership popolazione)
    age_class: int | None = None
    sesso: int | None = None
    ra: int | None = None
    cse_level: int | None = None


def all_cells() -> list[Cell]:
    """Le 34 celle atomiche. Sorgente unica: il build (ingest._build_audience_cache)
    deve produrre esattamente questi cell_id; un test lo verifica."""
    cells: list[Cell] = []
    cells.append(Cell("K", PART_AGE, BLOCK_KIDS, "nuove_classi_eta BETWEEN 1 AND 4"))
    for ac in ADULT_CLASSES:
        for s in SESSI:
            for r in RA_STATI:
                cells.append(Cell(
                    f"D{ac:02d}_{s}_{r}", PART_AGE, BLOCK_DEMO,
                    f"nuove_classi_eta = {ac} AND sesso = {s} AND resp_acquisto = {r}",
                    age_class=ac, sesso=s, ra=r,
                ))
    for lv in CSE_LEVELS:
        cells.append(Cell(f"C{lv}", PART_CSE, BLOCK_CSE, f"cse = {lv}", cse_level=lv))
    return cells


# Configurazione dei blocchi per il build a 3 query (una per blocco, GROUP BY
# sulle dimensioni-cella). gcols = colonne di individui che definiscono la cella.
BLOCKS = [
    {"block": BLOCK_DEMO, "partizione": PART_AGE,
     "pop": "nuove_classi_eta BETWEEN 5 AND 11 AND sesso IN (1,2) AND resp_acquisto IN (0,1)",
     "gcols": ["nuove_classi_eta", "sesso", "resp_acquisto"]},
    {"block": BLOCK_KIDS, "partizione": PART_AGE,
     "pop": "nuove_classi_eta BETWEEN 1 AND 4",
     "gcols": []},
    {"block": BLOCK_CSE, "partizione": PART_CSE,
     "pop": "cse BETWEEN 1 AND 5",
     "gcols": ["cse"]},
]


# ── Target (preset) come selezione di celle ───────────────────────────────────
@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    short: str
    partizione: str
    cell_where: str        # predicato su audience_cache (celle da sommare)


PRESETS: dict[str, Preset] = {
    "4plus":    Preset("4plus", "Individui 4+", "4+", PART_AGE, "block IN ('kids','demo')"),
    "1564":     Preset("1564", "15-64", "15-64", PART_AGE, "block = 'demo' AND age_class BETWEEN 5 AND 10"),
    "2554":     Preset("2554", "Adulti 25-54", "25-54", PART_AGE, "block = 'demo' AND age_class BETWEEN 7 AND 9"),
    "f1564":    Preset("f1564", "Donne 15-64", "D 15-64", PART_AGE, "block = 'demo' AND age_class BETWEEN 5 AND 10 AND sesso = 2"),
    "m1564":    Preset("m1564", "Uomini 15-64", "U 15-64", PART_AGE, "block = 'demo' AND age_class BETWEEN 5 AND 10 AND sesso = 1"),
    "ra":       Preset("ra", "Resp. Acquisto", "RA", PART_AGE, "block = 'demo' AND ra = 1"),
    "cse_alta": Preset("cse_alta", "CSE Alta (M/Alta + Alta)", "CSE Alta", PART_CSE, "block = 'cse' AND cse_level IN (4,5)"),
    "kids":     Preset("kids", "Bambini 4-14", "4-14", PART_AGE, "block = 'kids'"),
}
DEFAULT_TARGET = "4plus"


def list_presets() -> list[dict]:
    return [{"id": p.id, "label": p.label, "short": p.short} for p in PRESETS.values()]


def preset_where(target_id: str) -> str:
    p = PRESETS.get(target_id)
    if p is None:
        raise ValueError(f"Target sconosciuto: {target_id}. Disponibili: {list(PRESETS)}")
    return p.cell_where


# ── Target custom — una sola partizione, "niente incroci tra blocchi" ──────────
def custom_demo(age_from: int, age_to: int, sessi, ra) -> str:
    if not (5 <= age_from <= age_to <= 11):
        raise ValueError(f"range classi età demo fuori 5..11: {age_from}-{age_to}")
    ss = _in_list("sesso", sessi, SESSI)
    rr = _in_list("ra", ra, RA_STATI)
    return f"block = 'demo' AND age_class BETWEEN {age_from} AND {age_to} AND {ss} AND {rr}"


def custom_cse(levels) -> str:
    return f"block = 'cse' AND {_in_list('cse_level', levels, CSE_LEVELS)}"


def custom_kids() -> str:
    return "block = 'kids'"


def _in_list(col: str, values, allowed) -> str:
    vals = sorted({int(v) for v in values})
    if not vals or any(v not in allowed for v in vals):
        raise ValueError(f"valori {col} non validi: {values} (ammessi {list(allowed)})")
    return f"{col} IN ({','.join(str(v) for v in vals)})"


# ── Ricomposizione a query-time ───────────────────────────────────────────────
def event_stage(cell_where: str, row_where: str) -> str:
    """
    CTE `evento`: compone le CELLE di un target (una partizione) in righe a grana
    EVENTO — una riga per (data, cod_emit, tv, programma, t_start, t_end,
    durata_min) con audience/share/copertura ricomposte per somma+divisione. Le
    query ci appendono il loro roll-up evento→programma (media pesata sulla
    durata), invariato rispetto al modello a target monolitici.

    `cell_where` deve essere confinato a UNA sola partizione (usa preset_where o i
    custom_* builder, mai stringhe grezze cross-blocco). `row_where` filtra le
    righe (data/programma/fascia) con placeholder ? posizionali.
    """
    return f"""
        WITH evento AS (
            SELECT data, cod_emit, tv, programma, t_start, t_end, durata_min,
                   SUM(num_audience)                                 AS audience,
                   SUM(num_audience) / NULLIF(SUM(den_auditel), 0) * 100 AS share_auditel,
                   SUM(num_audience) / NULLIF(SUM(den_reale),   0) * 100 AS share_reale,
                   SUM(copertura)                                    AS copertura
            FROM audience_cache
            WHERE ({cell_where}) AND ({row_where})
            GROUP BY data, cod_emit, tv, programma, t_start, t_end, durata_min
        )"""


def validate_single_partition(conn, cell_where: str) -> str:
    """Difensivo (usato nei test): verifica che cell_where risolva a una sola
    partizione. Rialza se attraversa più partizioni (doppio conteggio)."""
    parts = {r[0] for r in conn.execute(
        f"SELECT DISTINCT partizione FROM audience_cache WHERE {cell_where}"
    ).fetchall()}
    if len(parts) > 1:
        raise ValueError(f"cell_where attraversa più partizioni {parts}: doppio conteggio")
    return next(iter(parts)) if parts else PART_AGE
