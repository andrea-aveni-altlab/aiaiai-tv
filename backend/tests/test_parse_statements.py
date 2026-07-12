"""
Test di sanita' del parser statement — anti-regressione sul bug HHMM/x60.

Il feed AltlabFilteredMDA fornisce ora inizio in HHMMSS e durata in SECONDI
(il tracciato ufficiale dice HHMM/minuti: e' il tracciato a non descrivere il
feed reale). Fino a luglio 2026 il parser interpretava HHMM + minuti*60:
uno statement '191600'/'300' (19:16:00 per 5 minuti) diventava 45:40->50:40,
l'89,7% degli statement finiva oltre le 26h e l'audience era gonfiata/garbled.

Eseguibile standalone (python tests/test_parse_statements.py) o con pytest.
"""
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from ingest import _parse_statements, _hhmmss_to_sec

DAY = date(2026, 1, 14)

def _stmt_line(ora: str, durata: str) -> str:
    # 21 campi come il feed: L|data|panel|I|prg|circuito|emit|ora|durata|mdu|
    # piatt|dataplay|oraplay|durplay|mduplay|piattplay|prgguest|toteml|classif|device|digvod
    f = [""] * 21
    f[0] = "L"; f[1] = "2026-01-14"; f[2] = "0101083"; f[3] = "I"; f[4] = "1"
    f[5] = "0000"; f[6] = "0004"; f[7] = ora; f[8] = durata; f[10] = "2"
    f[18] = "1"; f[20] = "0"
    return "|".join(f)

def test_hhmmss_e_durata_secondi():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="latin-1") as tf:
        tf.write(_stmt_line("191600", "300") + "\n")   # statement reale del 14/01
        path = Path(tf.name)
    rows = _parse_statements(path, DAY)
    assert len(rows) == 1, f"atteso 1 statement, trovati {len(rows)}"
    t_start, t_end = rows[0][5], rows[0][6]

    # corretto: 19:16:00 -> 19:21:00
    assert t_start == 19 * 3600 + 16 * 60, (
        f"REGRESSIONE ORA: t_start={t_start} ({t_start/3600:.2f}h). "
        f"Atteso 69360 (19:16:00). Se vale 164400 (45:40) il parser sta "
        f"rileggendo HHMMSS come HHMM: il campo 8 del feed ha 6 cifre."
    )
    assert t_end - t_start == 300, (
        f"REGRESSIONE DURATA: {t_end - t_start}s per durata grezza '300'. "
        f"Attesi 300s (il feed e' GIA' in secondi). Se vale 18000 e' tornato "
        f"il *60 che scambia secondi per minuti."
    )
    assert t_end == 69660, f"t_end={t_end}, atteso 69660 (19:21:00)"

def test_ore_oltre_mezzanotte():
    # il giorno TV arriva a 25:59:59: '253000' = 25:30:00
    assert _hhmmss_to_sec("253000") == 25 * 3600 + 30 * 60

def test_nessuno_statement_oltre_le_26h():
    # qualunque HHMMSS valido del feed (02-25) deve restare nel giorno TV
    for ora in ("020000", "125959", "195900", "255959"):
        assert _hhmmss_to_sec(ora) < 26 * 3600, f"{ora} fuori dal giorno TV"

if __name__ == "__main__":
    test_hhmmss_e_durata_secondi()
    test_ore_oltre_mezzanotte()
    test_nessuno_statement_oltre_le_26h()
    print("OK — parser statement: HHMMSS + durata in secondi verificati")
