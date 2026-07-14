# HANDOFF — DB palinsesto (programmato + emesso)

Documento di ripartenza per una sessione senza contesto pregresso.
Ultimo aggiornamento: 13/07/2026 · commit di riferimento: `1c2bf5f6`.

## Cos'è e dove vive

DB del palinsesto TV per il modello di previsione ascolti: **programmato** (documenti
commerciali delle concessionarie: Rai Pubblicità, CairoRCS/LA7, Publitalia/Mediaset)
+ **emesso** (rilevato, `backend/static_data/programmi_master.xlsx`), con identità
gerarchica dei programmi e query a orizzonte temporale.

- DB: `data/palinsesto.duckdb` (in .gitignore) — **separato** da tv.duckdb di produzione.
- Codice: package `palinsesto/` (CLI, mai importato da `backend/`). Non tocca l'app.
- PDF sorgente: `~/Antigravity/palinsesti_pdf/recuperati_20260713/` (+ archivio originale
  nella cartella madre). Fonti tutte pubbliche; pattern URL per riscaricarle nella memoria
  di sessione `palinsesti-programmati-fonti` e in fondo a questo file.
- Emesso: copre gen–apr 2026; arriverà aggiornato → il DB lo assorbe a batch (vedi schema).

## Stato di avanzamento

FATTO:
1. Schema v2 **approvato da Andrea** (DDL integrale sotto; source of truth: `palinsesto/schema.sql`).
2. Package: `db.py` (connect/init/seed fasce+target+metriche), `compose.py`
   (`palinsesto_del_giorno` a 3 strati + `costruisci_cache`), `previsioni.py`
   (API `registra()`/`confronto()`), `build.py` (CLI), `parsers/settimanale_pt.py`.
3. **Parser 1/4 — settimanali Prime Time Publitalia: COMPLETO E VALIDATO.**
   22 doc (`pt_2025_w53` + `pt_2026_w01..w21`), 1.014 slot puntuali C5/I1/R4,
   154 giorni contigui 28/12/2025→30/5/2026. Giorno campione (15/4, 12/4, 13/4, 1/5)
   confrontato a vista col PDF: corrispondenza 1:1. Orizzonte verificato
   (15/4 visto dall'1/4 = vuoto; dall'8/4 = presente). pubblicato_il = PDF
   CreationDate, sempre 5-7 gg prima della settimana.

4. **Parser griglie Cairo/LA7: COMPLETO E VERIFICATO** (`parsers/griglia_cairo.py`).
   5 doc gen-giu 2026 (validità continua 4/1→27/6), 46-48 celle e 50-63 slot/doc.
   Celle dalla geometria (segmenti H/V, fusione colonne dai gap nelle verticali),
   lattice 10' PIECEWISE tra etichette adiacenti (maggio 2026 ha passo non
   uniforme in una banda), testo celle dai CHARS (extract_words frammenta i
   titoli a spaziatura espansa), '+' separa alternative come '/', date con nome
   mese ("il 4 e 11 e 18 giugno") e catene "e il". Verifica: 0 char persi sui 5
   doc; giorni campione 6/4, 13/4, 12/4, 6/1 confrontati a vista col PDF: 1:1.
   (p.4 "Palinsesto pubblicitario" e LA7d p.9: fuori perimetro, dopo.)
5. **Parser griglie Rai: COMPLETO E VERIFICATO** (`parsers/griglia_rai.py`).
   ⚠️ le griglie sono IMMAGINI RASTER (~165dpi) dentro il PDF: niente layer
   testo utile (i "glifi doppi" erano solo celle vettoriali residue). Pipeline:
   rendering scala 3 → geometria dai PIXEL (run scuri con finestre direzionali
   ±1px per l'ondeggiamento; MAI MinFilter quadrato: fonde il testo bold e i
   tratteggi) → barra giorni per COLORE + lettere D..S come pixel bianchi
   (l'OCR delle singole lettere è instabile run-to-run) → colonne dagli ENDPOINT
   dei segmenti H tra i centri lettera (NON uniformi: la D è più stretta; coppie
   di bordi) → lattice 15' da OCR etichette con wrap deterministico + LIS (una
   etichetta misletta avvelenerebbe la cascata) → OCR whole-page su RGB (le ROI
   per cella e il grayscale degradano Vision) → riparatore date vincolato
   ('303'→30/3, '1244-305'→12/4-3/5, guardie: periodo + dow colonna) →
   sub-box inset = slot autonomi mono-colonna; sequenze a orari dichiarati
   ("20.30 CINQUE MINUTI … 20.35 AFFARI TUOI") ≠ alternanze; 'nel corso' in
   nota. OCR via `parsers/ocr_helper.swift` (Vision, compilato al volo).
   3 doc 2026 (inverno+primavera+estate, 4/1→5/9 contigui), 333-437 slot/doc,
   162 eccezioni datate. Verifica: 0 righe OCR perse sulle 9 pagine RAI1/2/3;
   0 finestre incoerenti; campioni 31/3 e 12/4 confrontati a vista col PDF.
   RESIDUI NOTI (da curatela, non regressioni): titoli sporchi d'OCR qua e là
   ('L COMMISSARI IONTALBANO', 'HCTON 1S'=FICTION ® 19/5); L'EREDITÀ primavera
   con finestra [25/5..] spuria (il "f. al 24/5" è illeggibile nel raster →
   buco 18:40 feriale prima del 25/5); annotazioni inline non boxate che
   sporcano il titolo (SPEC. A SUA IMMAGINE dentro LA VOLTA BUONA); banda
   02-06 troncata alle 26:00 (simulcast).
   DUE RITOCCHI AL CORE (approvati da Andrea): (a) compose.py, la specificità
   base-vs-base sopprime solo finestre-EVENTO — costante nominata
   `FINESTRA_EVENTO_MAX_GIORNI = 31` (documentata nel codice: quando un evento
   la supererà, alzarla lì); senza il limite, "f. al 29/5" (≈tutto il periodo)
   uccideva i vicini su overlap di 15' da lattice; (b) costruisci_cache scrive
   t_start=-1 per gli slot solo-fascia (t_start è nella PK;
   certezza_orario='solo_fascia' li descrive).
   INCERTEZZA DI LETTURA ≠ INCERTEZZA DI PALINSESTO (requisito di Andrea):
   la confidenza Vision è tracciata fino allo slot (bimodale: 1.0 buono,
   0.3-0.5 esattamente sulle righe storpiate). note JSON: ocr_conf,
   lettura_incerta, finestra_illeggibile. Una data SPEZZATA ("f. al 24/" col
   mese illeggibile) ANNULLA la finestra dell'alternativa e flagga: meglio
   uno slot visibile tutti i giorni con flag di curatela che un buco che
   sembra un dato. Colonna `lettura_incerta` in palinsesto_composto (distinta
   da alternanza_irrisolta), flag 'L' nel CLI giorno, comando
   `python -m palinsesto.build curatela` = lista da correggere (157 slot sui
   3 doc Rai; estate2026 ne ha ~90: griglia molto più densa).
   ⚠️ TRAPPOLA DUCKDB (verificata): "ALTER TABLE ADD COLUMN IF NOT EXISTS" su
   colonna GIÀ esistente RIAZZERA i valori al DEFAULT a ogni esecuzione — mai
   usarla nelle migrazioni a ogni connect; guardia esplicita via
   PRAGMA table_info (vedi db.init_schema).
6. **Parser listini Publitalia: COMPLETO E VERIFICATO**
   (`parsers/listino_publitalia.py`). 3 doc (gen_feb v82601, mar_apr v25601,
   mag_giu v40701; 4/1→27/6 contigui; pubblicato_il stampato nel TOC p.1;
   versione = codice a 5 cifre stampato su ogni pagina). GRIGLIE pp.2-20:
   testo vettoriale con GLIFI RADDOPPIATI a coppie (e quadruplicati) →
   `_dimezza` ricorsivo per token; testo celle dai CHARS (il prime frammenta
   extract_words in singole lettere); righe ancorate alle etichette orario
   (niente lattice); i bordi includono i RECT PIENI (cornice e celle
   ombreggiate: su RETE4 nessun segmento è full-width); rete dalle firme
   (TG5→CAN5, STUDIO APERTO→ITA1, TG4→RETE4) con FIRST-WINS (le repliche
   sulle tematiche contengono le stesse firme). Alternanze '/' non datate
   restano irrisolte NEL LISTINO e si risolvono con l'overlay dei settimanali
   PT — verificato sul 15/4 CAN5: daytime dal listino, FORBIDDEN FRUIT
   [puntuale/ereditato] dal PT, LA RUOTA (access) preservata. STIME → tabella
   `previsione` (347/364/487 righe, target 15_64, amr_migliaia, sottoperiodi
   come periodo_label, ' weekend' → tipo_giorno, colonna sinistra di ogni
   coppia = [primissima]): perimetro = pagina con firma generalista O prima
   del suo prodotto; le tematiche tradite dalla ripetizione interna delle
   etichette; dedup first-wins finale (registrato == memorizzato, verificato).
   Spot-check PDF p.114: TG5 20.00 = 2481/2409/2203/2139 ✓.
   RITOCCHI CORRELATI: `previsioni.registra` mette sentinelle sui campi PK
   (tipo_giorno→'tutti', rete/posizione→''), l'API del modello non inciampa
   sui NULL; `fascia_def` è ora configurazione-nel-codice (refresh a ogni
   init) con fasce PUBLITALIA dedicate: access fino alle 21:25 ("Ruota della
   fortuna ACCESS" nelle sue stesse Stime) — senza, l'overlay PT per fascia
   sopprimeva anche la Ruota delle 20:35.
7. Loader **emesso** (batch + vista corrente + conversione giorno TV) e **matcher**
   programmato↔emesso (pipeline sotto). Seed identità (clustering + 2 file).
8. Deck (semi-assistiti) e "Aggiorn. palinsesti" (variazioni): per ultimi.

Verifiche per fonte (concordate): conteggi righe vs pagine; materializzazione di
giorni campione confrontata A VISTA col PDF prima di proseguire; per l'emesso la
tripletta `mappati_a_blocco + generici_riempiti + orfani = 100%` dei minuti.

## Tre requisiti aggiuntivi di Andrea (vincolanti)

0. **La fascia è una convenzione commerciale, non una proprietà del tempo.**
   Ogni concessionaria la definisce come le serve: l'access Publitalia arriva
   alle 21:25 (la Ruota delle 20:35 è "Ruota della fortuna ACCESS" nelle sue
   stesse Stime), quello Rai/Cairo alle 20:30. Le definizioni vivono in
   `fascia_def` per concessionaria (commento nel DDL). Nei confronti tra
   fonti (stime Publitalia vs centro media vs modello) MAI matchare le
   etichette di fascia senza passare dalle definizioni: sarebbe un confronto
   tra cose diverse.

1. **`alternanza_irrisolta` è informazione, non difetto**: deve arrivare fino
   all'output di `palinsesto_del_giorno` E alla cache `palinsesto_composto`
   (colonna propria) — il modello allarga la banda dove il palinsesto stesso
   non sa cosa andrà in onda. (Già implementato: non regredire.)
2. **`previsione` è la tabella di confronto** concessionaria / centro media /
   modello di Andrea contro il consuntivo: il modello ci scrive DAL PRIMO RUN
   via `previsioni.registra(conn, sorgente='aiaiai', versione=..., righe=[...])`.
   Dimensioni target/metrica auto-upsert; grana 'giorno' e 'periodo' convivono
   distinte dal campo `grana`. (Già implementato: non regredire.)

## Le 10 decisioni nate dalla verifica adversariale (NON reintrodurre questi difetti)

Composizione (lente 1):
1. **Mai winner-takes-all a livello documento.** I doc coesistono: listini = griglia
   base; settimanali PT/variazioni = overlay. Se "vince il più recente" un settimanale
   (solo prime), il daytime sparisce. → Base scelta tra PARI-TIPO per (concessionaria,
   rete); puntuali/variazioni = overlay ordinati per pubblicato_il a livello SLOT.
2. **`fascia` NOT NULL su tutti gli slot** (derivata da `fascia_def` se non dichiarata)
   e **eredità oraria**: l'overlay senza orario eredita t_start/t_end dalla base che
   sopprime. Senza: il confronto per fascia non matcha mai (prime doppio) e la riga
   puntuale resta senza orari. Output con DUE assi: `certezza_contenuto`
   (puntuale|derivato) e `certezza_orario` (dichiarato|ereditato|solo_fascia).
3. **'solo' = INTERSEZIONE** (restringe alle date elencate), mai additivo:
   "solo sabato 30/5" letto additivo = in onda 8 sabati invece di 1.
4. **Alternanze datate: la finestra più stretta vince** e sopprime le sorelle nei
   giorni che copre ("TAGADÁ / EDEN il 6.1" → il 6/1 va EDEN, senza flag).
   `alternanza_irrisolta` solo per alternanze non datate (FILM/SOAP).
   Correlati: specificità base-vs-base (finestra di validità più corta sopprime la
   più lunga: eventi > regolare); `t_end_aperto` per fine variabile; le eccezioni
   si filtrano per orizzonte **via il LORO doc_id** (senza: leak dal futuro nei
   backtest); variazioni = slot sostitutivi + eccezioni cross-doc (aggancio logico
   rete+fascia/blocco, non solo slot_id).

Identità (lente 2):
5. **Il match programmato↔emesso è a GRANA EVENTO** (`match_evento`), non solo
   dizionario: "TG1" secco esiste in 5 collocazioni, una riga (titolo, rete) le
   collassa. Il dizionario `titolo_rilevato` è derivato, solo per titoli univoci.
6. **Parentela lessicale PRIMA della sovrapposizione temporale**: "TG1 SERA
   ANTEPRIMA" (19:57-20:00) si sovrappone al 100% con L'EREDITÀ in griglia →
   il temporale da solo sbaglia CON confidenza 1.0 (errore ripetuto = confermato).
   Ordine: exact → lessicale (titolo contiene canonico di blocco attivo ±30') →
   temporale (maggioranza PESATA SUI MINUTI, tie → orfano 'ambiguo') → generici.
7. **Slot generici = esito positivo distinto** (`slot_generico_id`): un film su
   slot FILM è "generico riempito", NON orfano — altrimenti la vista di curatela
   annega in centinaia di titoli one-shot. Copertura = blocco + generico + orfani.
   Correlati: eventi one-off = edizione degenere `dow_tipo='evento'` (l'eredità di
   slot per un evento non esiste); componente = (blocco_id, ruolo), ANTEPRIMA/
   SALUTI/code NON generano mai blocchi propri (DECISIONE ESPLICITA di Andrea).

Estensibilità (lente 3):
8. **Emesso a batch con vista `emesso_corrente`**: per data vince il batch più
   recente che la copre; TUTTE le query leggono la vista (ricaricare gen-apr con
   marzo corretto non deve raddoppiare gli eventi). `caricato_il` = orizzonte del
   consuntivo. Loader: convenzione GIORNO TV (eventi <02:00 → giorno precedente,
   t_start += 86400) — senza, la seconda serata non matcha mai.
9. **Seed identità a due file**: `seed_proposto.csv` (rigenerabile, mai editato) +
   `identita_curata.csv` (solo umano, VINCE SEMPRE; il generatore propone solo righe
   non coperte). Slug IMMUTABILI (rinominare cambia `nome`, mai la PK). Colonna
   `origine` auto|curato nel DB. Senza: ogni rebuild butta la curatela di Andrea.
10. **`titolo_norm` come chiave dell'emesso** (orari "(20.30)" e date strippati,
    regole documentate): senza, ogni slittamento di palinsesto rigenera orfani già
    curati. Correlati: `previsione` con dimensioni target/metrica (non stringhe
    libere), `grana`, `tipo_giorno`, `versione_sorgente`, `posizione_break`
    (mappa break→blocco/orari, indispensabile per confrontare col consuntivo);
    `prima_tv/replica/tipo` colonne native (mai solo dentro JSON).

## Note operative sul parser settimanali PT (per non regredire)

- Solo pagina 0 = C5 (x 140-357), I1 (360-572), R4 (575-790); pp. 1-3 = tematiche
  (fuori perimetro per ora). Ancore riga: parole 'prime'/'seconda' a x<120;
  ancore giorno: DOMENICA..SABATO a x<120.
- Una cella = TUTTE le sue righe unite; l'unico separatore di titoli multipli è
  **'/'** (i ritorni a capo dei titoli lunghi non lo portano mai; verificato).
- Genere dal COLORE dei riquadri prime (legenda: film blu scuro (0,.31,.545),
  fiction_serie azzurro (.118,.561,.859), produzioni arancio (.91,.388,0),
  sport verde (0,.502,0)) — match per distanza colore, soglia 0.15.
- 'R' replica: token isolato in coda O IN TESTA (artefatto di rendering:
  "REALPOLITIK R" può estrarsi come "R REALPOLITIK"). '1a TV' → prima_tv.
  Tipi: (Film|Tf|Doc|Minis|Serie).
- San Silvestro (31/12) ha 5 celle su 6: realtà della fonte, non bug.

## Fatti chiave sulle fonti (dalla ricognizione)

- Rai: `raipubblicita.it/wp-content/uploads/public-documents/palinsesti-editoriali/
  tv/{anno}/{stagione}{anno}/tvprogram_{stagione}{anno}.pdf` (inverno2026 senza
  underscore: `tvprograminverno2026.pdf`). Stagioni 2026: inverno 4/1-28/3
  (pubbl. ~dic), primavera 29/3-30/5 (pubbl. 12/2), estate 31/5-5/9 (pubbl. 22/4).
- Cairo: `static.cairorcsmedia.it/wp-content/uploads/2021/10/NETWORK-LA7-POLITICA-
  COMMERCIALE-TABELLARE-{MESE}-{ANNO}.pdf`; griglia editoriale a p.3 (indice p.2).
- Publitalia: tutto su publitalia.it (link `/binary/...`, titoli via JS → browser);
  listini 2026: gen-feb (RateTable_317), mar-apr (323), mag-giu (325), estate (329).
- Il feed ascolti (stmtastd) usa HHMMSS e durate in SECONDI (il tracciato ufficiale
  dice HHMM/minuti e MENTE) — irrilevante per il palinsesto ma vitale per l'emesso
  futuro da altre fonti.

## Curatela dei titoli (workflow)

`palinsesto/curatela_slot.csv` (in git, SOLO umano, mai rigenerato — stesso
principio di identita_curata.csv): una riga per slot da correggere, campi
vuoti = non toccare, '-' = azzera, `azione` = ''(correggi) | elimina | nuovo.
Andrea rivede/edita e poi `python -m palinsesto.build applica-curatela`
(idempotente; da RILANCIARE dopo ogni re-parse, che sovrascrive gli slot).
Il file oggi contiene i 77 flaggati di inverno+primavera 2026 con proposte
automatiche marcate "proposta" nella nota (da rivedere, non applicate) +
3 righe nuove (REAZIONE A CATENA dal 25/5, MORGANE 28/5, INTRATT. 30/5).
Gli slot corretti perdono `lettura_incerta` e guadagnano `curato: true`.
estate2026 (~80 flag) NON è nel file: fuori dall'esperimento corrente.

## CLI

```
PYTHONPATH=<pylibs con pdfplumber/pypdf> python -m palinsesto.build init
python -m palinsesto.build parse-pt <dir_settimanali>
python -m palinsesto.build giorno 2026-04-15 [--rete CAN5] [--orizzonte 2026-04-01]
python -m palinsesto.build cache 2026-01-01 2026-05-30 [--orizzonte ...]
python -m palinsesto.build report
```
Dipendenze parser (pdfplumber, pypdf, pypdfium2) NON sono nel venv del backend:
installarle in un target separato o aggiungerle a un requirements dedicato.

## DDL integrale (schema v2 approvato — source of truth: palinsesto/schema.sql)

```sql
CREATE TABLE IF NOT EXISTS doc_sorgente (
    doc_id           VARCHAR PRIMARY KEY,
    concessionaria   VARCHAR NOT NULL,      -- rai | cairo | publitalia
    tipo_doc         VARCHAR NOT NULL,      -- griglia | listino_griglia | settimanale_pt | variazione | deck
    file             VARCHAR NOT NULL,
    periodo_da       DATE NOT NULL,
    periodo_a        DATE NOT NULL,
    pubblicato_il    DATE NOT NULL,         -- stampata > pdf_meta > http
    pubblicato_fonte VARCHAR NOT NULL,
    note             VARCHAR
);

CREATE TABLE IF NOT EXISTS formato (
    formato_id  VARCHAR PRIMARY KEY,        -- slug IMMUTABILE
    nome        VARCHAR NOT NULL,
    genere      VARCHAR,
    origine     VARCHAR NOT NULL DEFAULT 'auto'
);
CREATE TABLE IF NOT EXISTS edizione (
    edizione_id VARCHAR PRIMARY KEY,
    formato_id  VARCHAR NOT NULL REFERENCES formato,
    nome        VARCHAR NOT NULL,
    rete        VARCHAR,
    fascia      VARCHAR,
    dow_tipo    VARCHAR NOT NULL,           -- feriale|weekend|quotidiana|settimanale|evento
    origine     VARCHAR NOT NULL DEFAULT 'auto'
);
CREATE TABLE IF NOT EXISTS blocco (
    blocco_id       VARCHAR PRIMARY KEY,
    edizione_id     VARCHAR NOT NULL REFERENCES edizione,
    nome_canonico   VARCHAR NOT NULL,
    genere_override VARCHAR,
    origine         VARCHAR NOT NULL DEFAULT 'auto'
);

CREATE TABLE IF NOT EXISTS slot_programmato (
    slot_id       VARCHAR PRIMARY KEY,
    doc_id        VARCHAR NOT NULL REFERENCES doc_sorgente,
    rete          VARCHAR NOT NULL,
    kind          VARCHAR NOT NULL,          -- base | puntuale
    dow_mask      VARCHAR,                   -- 'DLMMGVS' (base), indice 0=domenica
    valido_da     DATE, valido_a DATE,
    data          DATE,                      -- puntuale
    t_start       INTEGER, t_end INTEGER,    -- secondi, giorno TV 02-26h
    t_end_aperto  BOOLEAN DEFAULT FALSE,
    fascia        VARCHAR NOT NULL,
    blocco_id     VARCHAR REFERENCES blocco,
    titolo_grezzo VARCHAR NOT NULL,
    generico      BOOLEAN DEFAULT FALSE,
    gruppo_alt    VARCHAR,
    prima_tv      BOOLEAN,
    replica       BOOLEAN,
    tipo          VARCHAR,
    note          JSON,
    CHECK ((kind = 'base') = (data IS NULL))
);
CREATE TABLE IF NOT EXISTS slot_eccezione (
    ecc_id        VARCHAR PRIMARY KEY,
    doc_id        VARCHAR NOT NULL REFERENCES doc_sorgente,
    slot_id       VARCHAR REFERENCES slot_programmato,
    target_rete   VARCHAR,
    target_fascia VARCHAR,
    target_blocco VARCHAR REFERENCES blocco,
    tipo          VARCHAR NOT NULL,          -- escluso | solo (solo = INTERSEZIONE)
    data_da       DATE, data_a DATE,
    dow_mask      VARCHAR,
    date_list     JSON
);
CREATE TABLE IF NOT EXISTS fascia_def (
    concessionaria VARCHAR, fascia VARCHAR,
    t_da INTEGER NOT NULL, t_a INTEGER NOT NULL,
    PRIMARY KEY (concessionaria, fascia)
);

CREATE TABLE IF NOT EXISTS palinsesto_composto (
    orizzonte_label   VARCHAR NOT NULL,
    giorno            DATE NOT NULL,
    rete              VARCHAR NOT NULL,
    t_start           INTEGER, t_end INTEGER,
    fascia            VARCHAR NOT NULL,
    blocco_id         VARCHAR,
    titolo            VARCHAR NOT NULL,
    certezza_contenuto VARCHAR NOT NULL,     -- puntuale | derivato
    certezza_orario    VARCHAR NOT NULL,     -- dichiarato | ereditato | solo_fascia
    generico          BOOLEAN,
    alternanza_irrisolta BOOLEAN,            -- REQUISITO: mai persa
    prima_tv          BOOLEAN, replica BOOLEAN, tipo VARCHAR,
    genere            VARCHAR,
    doc_id            VARCHAR, pubblicato_il DATE,
    PRIMARY KEY (orizzonte_label, giorno, rete, fascia, titolo, t_start)
);

CREATE TABLE IF NOT EXISTS emesso_batch (
    batch_id    VARCHAR PRIMARY KEY,
    file        VARCHAR NOT NULL,
    caricato_il DATE NOT NULL,
    periodo_da  DATE, periodo_a DATE
);
CREATE TABLE IF NOT EXISTS emesso_evento (
    batch_id    VARCHAR NOT NULL REFERENCES emesso_batch,
    data        DATE NOT NULL,               -- GIORNO TV
    rete        VARCHAR NOT NULL,
    titolo_raw  VARCHAR NOT NULL,
    titolo_norm VARCHAR NOT NULL,
    t_start     INTEGER NOT NULL, t_end INTEGER NOT NULL,
    PRIMARY KEY (batch_id, data, rete, t_start, titolo_raw)
);
CREATE OR REPLACE VIEW emesso_corrente AS
    SELECT e.* FROM emesso_evento e
    JOIN emesso_batch b USING (batch_id)
    WHERE b.caricato_il = (
        SELECT MAX(b2.caricato_il) FROM emesso_batch b2
        WHERE e.data BETWEEN b2.periodo_da AND b2.periodo_a
    );

CREATE TABLE IF NOT EXISTS match_evento (
    batch_id VARCHAR, data DATE, rete VARCHAR, titolo_norm VARCHAR, t_start INTEGER,
    blocco_id        VARCHAR REFERENCES blocco,
    slot_generico_id VARCHAR,
    ruolo            VARCHAR,                -- core | anteprima | coda | saluti
    metodo           VARCHAR,                -- exact | lessicale | temporale | manuale
    confidenza       DOUBLE,
    PRIMARY KEY (batch_id, data, rete, t_start, titolo_norm)
);
CREATE TABLE IF NOT EXISTS titolo_rilevato (
    titolo_norm VARCHAR, rete VARCHAR,
    blocco_id VARCHAR REFERENCES blocco,
    ruolo VARCHAR, metodo VARCHAR, confidenza DOUBLE,
    PRIMARY KEY (titolo_norm, rete)
);

CREATE TABLE IF NOT EXISTS target (
    target_id  VARCHAR PRIMARY KEY,
    label_fonte VARCHAR,
    definizione VARCHAR
);
CREATE TABLE IF NOT EXISTS metrica (
    metrica_id VARCHAR PRIMARY KEY,
    unita      VARCHAR
);
CREATE TABLE IF NOT EXISTS posizione_break (
    sorgente VARCHAR, posizione VARCHAR, rete VARCHAR,
    t_start INTEGER, t_end INTEGER,
    blocco_id VARCHAR REFERENCES blocco,
    PRIMARY KEY (sorgente, posizione)
);
CREATE TABLE IF NOT EXISTS previsione (
    sorgente          VARCHAR NOT NULL,
    versione_sorgente VARCHAR NOT NULL,
    doc_id            VARCHAR REFERENCES doc_sorgente,
    pubblicato_il     DATE NOT NULL,
    grana             VARCHAR NOT NULL,      -- 'periodo' | 'giorno'
    periodo_label     VARCHAR,
    periodo_da        DATE NOT NULL, periodo_a DATE NOT NULL,
    tipo_giorno       VARCHAR,
    rete              VARCHAR,
    posizione         VARCHAR,
    blocco_id         VARCHAR REFERENCES blocco,
    target_id         VARCHAR NOT NULL REFERENCES target,
    metrica_id        VARCHAR NOT NULL REFERENCES metrica,
    valore            DOUBLE NOT NULL,
    PRIMARY KEY (sorgente, versione_sorgente, periodo_da, rete, posizione,
                 tipo_giorno, target_id, metrica_id)
);
```

## Semantica di palinsesto_del_giorno(conn, giorno, rete=None, orizzonte=None)

1. AMMISSIONE: doc con pubblicato_il ≤ orizzonte e periodo che copre il giorno.
   Il filtro vale per slot E eccezioni, ciascuno via il PROPRIO doc_id.
2. BASE: tra griglia/listino_griglia vince il più recente per (concessionaria, rete)
   — SOLO tra pari-tipo. Attivi = dow ∧ finestra − 'escluso' ∩ 'solo'
   + alternanze (finestra stretta vince) + specificità (finestra corta sopprime).
3. OVERLAY: tutti gli slot puntuali del giorno, in ordine di pubblicato_il;
   conflitto = stessa rete ∧ overlap orario (o stessa fascia se orario assente);
   eredità oraria dalla base soppressa; la base soppressa resta interrogabile.
4. OUTPUT: rete, orari/fascia, blocco→edizione→formato, certezza_contenuto,
   certezza_orario, flag (generico, alternanza_irrisolta, prima_tv, replica, tipo,
   genere), provenienza (doc_id, pubblicato_il).
