from dataclasses import dataclass

@dataclass
class Target:
    id: str
    label: str
    sql_where: str
    short: str

TARGETS: dict[str, Target] = {
    "4plus": Target(
        id="4plus", label="Individui 4+", sql_where="TRUE", short="4+",
    ),
    "1564": Target(
        id="1564", label="15-64",
        sql_where="nuove_classi_eta BETWEEN 5 AND 10", short="15-64",
    ),
    "2554": Target(
        id="2554", label="Adulti 25-54",
        sql_where="nuove_classi_eta BETWEEN 7 AND 9", short="25-54",
    ),
    "f1564": Target(
        id="f1564", label="Donne 15-64",
        sql_where="sesso = 2 AND nuove_classi_eta BETWEEN 5 AND 10", short="D 15-64",
    ),
    "m1564": Target(
        id="m1564", label="Uomini 15-64",
        sql_where="sesso = 1 AND nuove_classi_eta BETWEEN 5 AND 10", short="U 15-64",
    ),
    "ra": Target(
        id="ra", label="Resp. Acquisto", sql_where="resp_acquisto = 1", short="RA",
    ),
    "cse_alta": Target(
        id="cse_alta", label="CSE Alta (M/Alta + Alta)",
        sql_where="cse IN (4, 5)", short="CSE Alta",
    ),
    "kids": Target(
        id="kids", label="Bambini 4-14",
        sql_where="nuove_classi_eta BETWEEN 1 AND 4", short="4-14",
    ),
}

DEFAULT_TARGET = "4plus"

def get_target(target_id: str) -> Target:
    if target_id not in TARGETS:
        raise ValueError(f"Target sconosciuto: {target_id}. Disponibili: {list(TARGETS.keys())}")
    return TARGETS[target_id]
