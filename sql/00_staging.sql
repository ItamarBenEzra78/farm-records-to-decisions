-- =====================================================================
-- STAGING: one contract for every source.
-- A loader (load_synthetic.py / the private load_real.py) fills these
-- tables with typed values and NOTHING else: no dedup, no fixing, no
-- business rules. Every row keeps source_file + row_num for lineage.
-- =====================================================================

-- ---------- configuration the loader provides (data, not code) ----------
CREATE OR REPLACE TABLE cfg_variety_alias (          -- spelling roots -> canonical variety
    variety      VARCHAR NOT NULL,                  -- canonical English name
    alias        VARCHAR NOT NULL                   -- lower-case root to search for
);
CREATE OR REPLACE TABLE cfg_buyer (                 -- who the export buyers are, and their names on paper
    counterparty_id VARCHAR NOT NULL,
    name_on_paper   VARCHAR NOT NULL,
    label           VARCHAR NOT NULL
);
CREATE OR REPLACE TABLE cfg_workbook (              -- what each marketing workbook is
    book          VARCHAR PRIMARY KEY,
    role          VARCHAR NOT NULL,                 -- 'master' | 'buyer' | 'other'
    buyer_id      VARCHAR,                          -- set when role = 'buyer'
    default_grower VARCHAR                          -- grower to assume when the row leaves it blank
);
CREATE OR REPLACE TABLE cfg_param (
    key   VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL                          -- e.g. own_grower_regex, own_entity, vat_rate_2024
);

-- ---------- VAT filings ----------
CREATE OR REPLACE TABLE stg_vat_line (
    entity        VARCHAR,                          -- reporting business (one farm may file for several)
    period        VARCHAR,                          -- YYYYMM of the filing
    doc_date      DATE,
    direction     VARCHAR,                          -- 'sale' | 'input'
    record_type   VARCHAR,
    counterparty  VARCHAR,
    reference     VARCHAR,
    amount        DOUBLE,                           -- ILS ex-VAT, signed
    vat           DOUBLE,
    source_file   VARCHAR,
    row_num       INTEGER
);
CREATE OR REPLACE TABLE stg_vat_report (            -- header totals of each monthly filing (optional)
    entity        VARCHAR,
    period        VARCHAR,
    header_sales_vat   DOUBLE,
    header_input_vat   DOUBLE,
    source_file   VARCHAR
);

-- ---------- export delivery notes ----------
CREATE OR REPLACE TABLE stg_note (
    note_no       INTEGER,
    note_date     DATE,
    buyer_raw     VARCHAR,                          -- buyer as written on the note (name or id)
    grower_raw    VARCHAR,
    variety_raw   VARCHAR,
    net_kg        DOUBLE,
    port          VARCHAR,
    container     VARCHAR,
    doc_hash      VARCHAR,                          -- hash of the whole document content
    source_file   VARCHAR,
    row_num       INTEGER
);
CREATE OR REPLACE TABLE stg_note_line (             -- pallet / bulk-bin lines inside a note (optional)
    note_no       INTEGER,
    variety_raw   VARCHAR,
    weight_kg     DOUBLE,
    size          VARCHAR,
    grade         VARCHAR,
    is_total_row  BOOLEAN,
    source_file   VARCHAR,
    row_num       INTEGER
);

-- ---------- marketing workbooks (Excel, many copies of the same shipments) ----------
CREATE OR REPLACE TABLE stg_workbook_row (
    book          VARCHAR,
    sheet         VARCHAR,
    row_num       INTEGER,
    date_raw      VARCHAR,
    note_no       INTEGER,
    grower_raw    VARCHAR,
    variety_raw   VARCHAR,
    weight_raw    DOUBLE,                           -- may be tons or kg
    destination   VARCHAR,
    is_subtotal_hint BOOLEAN                        -- loader's raw signal (empty note, 'סה"כ' label)
);

-- ---------- optional tables: filled only by the private real-data loader ----------
-- stg_vat_report, stg_note_line and the field tables below are part of the contract so the same
-- schema serves the real farm (pallet lines, plot yields, sowing, rotation, prices, plans).
-- The synthetic loader leaves them empty; no public mart depends on them yet.
CREATE OR REPLACE TABLE stg_plot_yield (            -- per plot x variety x season, as reported to the insurer
    season INTEGER, term VARCHAR, plot_no VARCHAR, plot_name VARCHAR,
    variety_raw VARCHAR, dunam DOUBLE, yield_t_per_dunam DOUBLE, source_file VARCHAR, row_num INTEGER
);
CREATE OR REPLACE TABLE stg_sowing (
    sow_date DATE, plot_no VARCHAR, plot_name VARCHAR, variety_raw VARCHAR, dunam DOUBLE,
    damage_date DATE, yield_to_damage DOUBLE, is_duplicate_copy BOOLEAN, source_file VARCHAR, row_num INTEGER
);
CREATE OR REPLACE TABLE stg_rotation (
    plot_no VARCHAR, plot_name VARCHAR, dunam DOUBLE, year INTEGER, crop VARCHAR
);
CREATE OR REPLACE TABLE stg_local_price (
    price_date DATE, variety_raw VARCHAR, size VARCHAR, price_ils_per_kg DOUBLE, kg DOUBLE, source_file VARCHAR
);
CREATE OR REPLACE TABLE stg_plan (
    season INTEGER, buyer_raw VARCHAR, variety_raw VARCHAR, planned_dunam DOUBLE, source_file VARCHAR
);
