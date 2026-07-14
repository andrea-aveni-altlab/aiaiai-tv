# AIAIAI TV — convenzioni di progetto

## Architettura
- **App ascolti** (`backend/` + `frontend/`): FastAPI + DuckDB, deploy Railway
  (project `beneficial-abundance`, service `aiaiai-tv`, volume `/data`); frontend
  React/Vite su Netlify. `git push origin main` deploya ENTRAMBI.
- **`palinsesto/`**: DB del palinsesto programmato+emesso (`data/palinsesto.duckdb`),
  package separato, CLI — **mai importato dal backend di produzione**.
  Ripartenza lavori: leggere `HANDOFF_PALINSESTO.md`.

## Decisioni permanenti (non ridiscutere, non regredire)
- **Celle atomiche** in `audience_cache`: 34 celle su 3 blocchi (kids 1, demo 28,
  cse 5) in 2 partizioni parallele (`age` = kids+demo, `cse`). I target sono somme
  di celle **entro una sola partizione**: mai sommare tra partizioni (doppio
  conteggio). Share = SUM(num)/SUM(den), mai media di share. Vedi `backend/cells.py`.
- **Calcola-e-scarta**: statements/individui si eliminano dopo il calcolo della
  cache. La cache è il prodotto; i tar.gz su S3 sono la source of truth
  ricostruibile. Reingest = ripescare da S3 con `force=true`.
- **Parser statement**: il feed reale usa ora **HHMMSS** e durata **in SECONDI**
  (il tracciato ufficiale dice HHMM/minuti e mente). Mai reintrodurre HHMM o ×60:
  `backend/tests/test_parse_statements.py` fallisce rumorosamente se succede.
- **Ancore di plausibilità post-ingest** (`_check_anchors`): Affari Tuoi / TG1
  Sera / La Ruota / TG5 contro range noti dal mondo reale, esito in
  `ingest_log.note`, WARNING se fuori. Non rimuoverle: il bug HHMM visse mesi
  perché tutto era solo internamente coerente.
- **Trappola DuckDB nelle migrazioni** (verificata, morde su QUALUNQUE DB del
  progetto, incluso tv.duckdb): `ALTER TABLE ADD COLUMN IF NOT EXISTS` su una
  colonna GIÀ esistente non è un no-op — RIAZZERA i valori al DEFAULT a ogni
  esecuzione. Mai metterla in un init/startup che gira a ogni connessione:
  guardia esplicita prima (`PRAGMA table_info`), poi ALTER solo se manca.
- **Le fasce orarie sono convenzioni commerciali, non proprietà del tempo**:
  ogni concessionaria definisce access/prime come le serve (per Publitalia
  l'access arriva alle 21:25: la Ruota delle 20:35 è un prodotto access).
  Mai confrontare "access" di due fonti senza verificarne le definizioni in
  `fascia_def` — sarebbe un confronto tra cose diverse.

## Fonti dati
- S3 `altlabanalysisdata`, prefix `AuditelTA/AltlabFilteredMDA/`, regione
  `eu-south-1` (un tar.gz/giorno; creds via `railway run`). `ProcessedRaw/` = grezzi.
- Emesso: `backend/static_data/programmi_master.xlsx` (limita il reingest: usare
  `end` = ultima data coperta dal file, non "oggi").
- Palinsesti programmati: PDF in `~/Antigravity/palinsesti_pdf/recuperati_20260713/`
  (fonti pubbliche; pattern URL nell'handoff).

## Non toccare senza richiesta esplicita di Andrea
- La produzione Railway (deploy, reingest, variabili).
- Lo schema di `audience_cache` senza migrazione esplicita (drop+ricrea+reingest,
  mai formati misti).

## Come lavora Andrea (rispettare sempre)
- **Impianto prima del codice**: diagnosi → strategia/schema → sua validazione →
  implementazione. Mai editare su decisioni architetturali non validate.
- **Verifica empirica prima di dichiarare valido**: numeri provati sui dati reali,
  giorni campione confrontati a vista, mai asserzioni.
- **Ancorare al mondo reale**: i risultati si controllano contro valori noti
  (ordini di grandezza Auditel pubblici), non solo contro la coerenza interna.
