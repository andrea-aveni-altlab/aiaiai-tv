-- DB palinsesto: programmato + emesso con identita' gerarchica (impianto v2,
-- validato 13/07/2026). Vive in data/palinsesto.duckdb, fuori dalla produzione.

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

-- ── identita' gerarchica: formato (contenuto) → edizione (slot) → blocco (commerciale) ──
CREATE TABLE IF NOT EXISTS formato (
    formato_id  VARCHAR PRIMARY KEY,        -- slug IMMUTABILE
    nome        VARCHAR NOT NULL,
    genere      VARCHAR,
    origine     VARCHAR NOT NULL DEFAULT 'auto'   -- auto | curato (i rebuild non toccano 'curato')
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
-- livello 4 (componente) = (blocco_id, ruolo) su match_evento/titolo_rilevato:
-- ANTEPRIMA/SALUTI/code non generano mai blocchi propri.

-- ── programmato: base (settimana-tipo) + eccezioni. MAI appiattito per giorno ──
CREATE TABLE IF NOT EXISTS slot_programmato (
    slot_id       VARCHAR PRIMARY KEY,
    doc_id        VARCHAR NOT NULL REFERENCES doc_sorgente,
    rete          VARCHAR NOT NULL,
    kind          VARCHAR NOT NULL,          -- base | puntuale
    dow_mask      VARCHAR,                   -- 'DLMMGVS' es. '0111110' (base)
    valido_da     DATE, valido_a DATE,       -- base; decorrenze pure restringono la finestra
    data          DATE,                      -- puntuale
    t_start       INTEGER, t_end INTEGER,    -- secondi, giorno TV 02-26h
    t_end_aperto  BOOLEAN DEFAULT FALSE,     -- fine variabile ('+ proc. tappa')
    fascia        VARCHAR NOT NULL,          -- SEMPRE valorizzata (dichiarata o da fascia_def)
    blocco_id     VARCHAR REFERENCES blocco, -- NULL finche' il seed identita' non assegna
    titolo_grezzo VARCHAR NOT NULL,
    generico      BOOLEAN DEFAULT FALSE,
    gruppo_alt    VARCHAR,                   -- alternanza: righe sorelle
    prima_tv      BOOLEAN,
    replica       BOOLEAN,
    tipo          VARCHAR,                   -- film | tf | doc
    note          JSON,
    CHECK ((kind = 'base') = (data IS NULL))
);
CREATE TABLE IF NOT EXISTS slot_eccezione (
    ecc_id        VARCHAR PRIMARY KEY,
    doc_id        VARCHAR NOT NULL REFERENCES doc_sorgente,  -- doc PROPRIO (filtro orizzonte)
    slot_id       VARCHAR REFERENCES slot_programmato,       -- NULL = cross-doc per chiave logica
    target_rete   VARCHAR,
    target_fascia VARCHAR,
    target_blocco VARCHAR REFERENCES blocco,
    tipo          VARCHAR NOT NULL,          -- escluso | solo (solo = INTERSEZIONE)
    data_da       DATE, data_a DATE,
    dow_mask      VARCHAR,
    date_list     JSON
);
CREATE TABLE IF NOT EXISTS fascia_def (      -- confini orari per concessionaria (calibrabili)
    concessionaria VARCHAR, fascia VARCHAR,
    t_da INTEGER NOT NULL, t_a INTEGER NOT NULL,
    PRIMARY KEY (concessionaria, fascia)
);

-- ── cache composta (rigenerabile dal build; requisito: alternanza_irrisolta propagata) ──
CREATE TABLE IF NOT EXISTS palinsesto_composto (
    orizzonte_label   VARCHAR NOT NULL,      -- 'pieno' | 'YYYY-MM-DD'
    giorno            DATE NOT NULL,
    rete              VARCHAR NOT NULL,
    t_start           INTEGER,               -- -1 = solo_fascia (e' nella PK)
    t_end             INTEGER,
    fascia            VARCHAR NOT NULL,
    blocco_id         VARCHAR,
    titolo            VARCHAR NOT NULL,
    certezza_contenuto VARCHAR NOT NULL,     -- puntuale | derivato
    certezza_orario    VARCHAR NOT NULL,     -- dichiarato | ereditato | solo_fascia
    generico          BOOLEAN,
    alternanza_irrisolta BOOLEAN,            -- informazione di incertezza, MAI persa
    prima_tv          BOOLEAN, replica BOOLEAN, tipo VARCHAR,
    genere            VARCHAR,
    doc_id            VARCHAR, pubblicato_il DATE,
    lettura_incerta   BOOLEAN DEFAULT FALSE, -- incertezza di LETTURA (OCR/parsing),
                                             -- distinta da quella di palinsesto
    PRIMARY KEY (orizzonte_label, giorno, rete, fascia, titolo, t_start)
);

-- ── emesso (versionato a batch, il consuntivo ha il suo orizzonte) ──
CREATE TABLE IF NOT EXISTS emesso_batch (
    batch_id    VARCHAR PRIMARY KEY,
    file        VARCHAR NOT NULL,
    caricato_il DATE NOT NULL,
    periodo_da  DATE, periodo_a DATE
);
CREATE TABLE IF NOT EXISTS emesso_evento (
    batch_id    VARCHAR NOT NULL REFERENCES emesso_batch,
    data        DATE NOT NULL,               -- GIORNO TV (loader: eventi <02:00 → giorno prec, +86400)
    rete        VARCHAR NOT NULL,
    titolo_raw  VARCHAR NOT NULL,
    titolo_norm VARCHAR NOT NULL,
    t_start     INTEGER NOT NULL, t_end INTEGER NOT NULL,
    PRIMARY KEY (batch_id, data, rete, t_start, titolo_raw)
);
CREATE OR REPLACE VIEW emesso_corrente AS     -- per data vince il batch piu' recente che la copre
    SELECT e.* FROM emesso_evento e
    JOIN emesso_batch b USING (batch_id)
    WHERE b.caricato_il = (
        SELECT MAX(b2.caricato_il) FROM emesso_batch b2
        WHERE e.data BETWEEN b2.periodo_da AND b2.periodo_a
    );

-- ── match programmato↔emesso: verita' primaria a grana evento + dizionario derivato ──
CREATE TABLE IF NOT EXISTS match_evento (
    batch_id VARCHAR, data DATE, rete VARCHAR, titolo_norm VARCHAR, t_start INTEGER,
    blocco_id        VARCHAR REFERENCES blocco,
    slot_generico_id VARCHAR,                -- l'evento riempie uno slot generico (FILM...)
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

-- ── previsioni: concessionaria, centro media e modello sullo stesso asse ──
CREATE TABLE IF NOT EXISTS target (
    target_id  VARCHAR PRIMARY KEY,          -- 'individui', '15_64', '25_54', ...
    label_fonte VARCHAR,
    definizione VARCHAR
);
CREATE TABLE IF NOT EXISTS metrica (
    metrica_id VARCHAR PRIMARY KEY,          -- 'amr_migliaia', 'share_pct', 'amr_individui'
    unita      VARCHAR
);
CREATE TABLE IF NOT EXISTS posizione_break (
    sorgente VARCHAR, posizione VARCHAR, rete VARCHAR,
    t_start INTEGER, t_end INTEGER,
    blocco_id VARCHAR REFERENCES blocco,
    PRIMARY KEY (sorgente, posizione)
);
CREATE TABLE IF NOT EXISTS previsione (
    sorgente          VARCHAR NOT NULL,      -- 'publitalia_listino' | 'centromedia_x' | 'aiaiai'
    versione_sorgente VARCHAR NOT NULL,      -- listino_id / versione run del modello
    doc_id            VARCHAR REFERENCES doc_sorgente,
    pubblicato_il     DATE NOT NULL,
    grana             VARCHAR NOT NULL,      -- 'periodo' | 'giorno'
    periodo_label     VARCHAR,
    periodo_da        DATE NOT NULL, periodo_a DATE NOT NULL,
    tipo_giorno       VARCHAR,               -- feriale | sabato | domenica | NULL
    rete              VARCHAR,
    posizione         VARCHAR,               -- il break/oggetto come lo chiama la fonte
    blocco_id         VARCHAR REFERENCES blocco,
    target_id         VARCHAR NOT NULL REFERENCES target,
    metrica_id        VARCHAR NOT NULL REFERENCES metrica,
    valore            DOUBLE NOT NULL,
    PRIMARY KEY (sorgente, versione_sorgente, periodo_da, rete, posizione,
                 tipo_giorno, target_id, metrica_id)
);
