-- =====================================================================
-- CORE: mapping tables, facts, dimensions and the data-quality log.
-- Every fix leaves a row in dq_issue (what, where, before, after).
-- =====================================================================

CREATE OR REPLACE TABLE dq_issue (
    issue_code  VARCHAR, table_name VARCHAR, record_key VARCHAR,
    value_before VARCHAR, value_after VARCHAR, action VARCHAR
);

CREATE OR REPLACE MACRO param(k) AS (SELECT value FROM cfg_param WHERE key = k);

-- ---------------------------------------------------------------- 1. spelling maps
-- strip sizes (35-45, 55+), 4-digit plot numbers and packaging words, then find the longest known root
CREATE OR REPLACE TABLE map_variety_alias AS
WITH raw AS (
    SELECT variety_raw FROM stg_note UNION SELECT variety_raw FROM stg_workbook_row
    UNION SELECT variety_raw FROM stg_note_line UNION SELECT variety_raw FROM stg_plot_yield
),
cleaned AS (
    SELECT variety_raw,
           lower(regexp_replace(variety_raw, '\d+\s*-\s*\d+|\d+\+|\b\d{4}\b|תפוא|צוברים|מיקס|זרעים', ' ', 'g')) AS t
    FROM raw WHERE variety_raw IS NOT NULL AND variety_raw <> 'סה"כ'
),
hits AS (
    SELECT c.variety_raw, a.variety, a.alias,
           row_number() OVER (PARTITION BY c.variety_raw ORDER BY length(a.alias) DESC) AS rk,
           count(DISTINCT a.variety) OVER (PARTITION BY c.variety_raw) AS n_varieties
    FROM cleaned c JOIN cfg_variety_alias a ON contains(c.t, a.alias)
),
fuzzy AS (   -- misspellings ("Parris", "Monic", "Rossana"): closest root by Jaro-Winkler, accepted at >= 0.90
    SELECT c.variety_raw, a.variety, a.alias, jaro_winkler_similarity(trim(c.t), a.alias) AS sim
    FROM cleaned c JOIN cfg_variety_alias a ON length(a.alias) >= 4
    WHERE c.variety_raw NOT IN (SELECT variety_raw FROM hits)
    QUALIFY row_number() OVER (PARTITION BY c.variety_raw ORDER BY sim DESC) = 1
)
SELECT c.variety_raw,
       coalesce(h.variety, CASE WHEN f.sim >= 0.90 THEN f.variety END)              AS variety,
       coalesce(h.alias, CASE WHEN f.sim >= 0.90 THEN f.alias END)                  AS matched_root,
       CASE WHEN h.variety IS NOT NULL AND h.n_varieties > 1 THEN 'mixed'
            WHEN h.variety IS NOT NULL THEN 'root'
            WHEN f.sim >= 0.90 THEN 'fuzzy'
            ELSE 'unmatched' END                                                   AS rule,
       round(f.sim, 3)                                                              AS fuzzy_score
FROM cleaned c LEFT JOIN hits h ON h.variety_raw = c.variety_raw AND h.rk = 1
LEFT JOIN fuzzy f ON f.variety_raw = c.variety_raw;

INSERT INTO dq_issue
SELECT 'variety_misspelling_fuzzy', 'map_variety_alias', variety_raw, variety_raw, variety || ' (' || fuzzy_score || ')', 'misspelt variety matched by similarity >= 0.90'
FROM map_variety_alias WHERE rule = 'fuzzy';

INSERT INTO dq_issue
SELECT 'variety_unmatched', 'map_variety_alias', variety_raw, variety_raw, NULL, 'no known root; left NULL and listed for review'
FROM map_variety_alias WHERE rule = 'unmatched';

CREATE OR REPLACE TABLE map_grower_alias AS
SELECT grower_raw,
       regexp_matches(grower_raw, param('own_grower_regex'))                    AS has_own,
       regexp_matches(grower_raw, '[,/]')                                        AS has_several,
       CASE WHEN NOT regexp_matches(grower_raw, param('own_grower_regex')) THEN 'other'
            WHEN regexp_matches(grower_raw, '[,/]') THEN 'mixed' ELSE 'own' END AS grower_class,
       -- share of the container that is the farm's own: 1 for own, 1/n for a container shared by n growers
       CASE WHEN NOT regexp_matches(grower_raw, param('own_grower_regex')) THEN 0.0
            ELSE 1.0 / len(list_filter(regexp_split_to_array(grower_raw, '[,/]'), x -> trim(x) <> '')) END AS own_share
FROM (SELECT grower_raw FROM stg_note UNION SELECT grower_raw FROM stg_workbook_row) g
WHERE grower_raw IS NOT NULL;

-- ---------------------------------------------------------------- 2. delivery notes
-- (a) identical files: same content hash
CREATE OR REPLACE TABLE int_note_unique AS
SELECT * FROM stg_note QUALIFY row_number() OVER (PARTITION BY doc_hash ORDER BY source_file) = 1;
INSERT INTO dq_issue
SELECT 'note_duplicate_file', 'stg_note', source_file, doc_hash, NULL, 'identical content to another file; dropped'
FROM stg_note QUALIFY row_number() OVER (PARTITION BY doc_hash ORDER BY source_file) > 1;

-- (b) same note number, different content (re-issued / edited note): keep the latest file, log the other
CREATE OR REPLACE TABLE int_note AS
SELECT * FROM int_note_unique QUALIFY row_number() OVER (PARTITION BY note_no ORDER BY source_file DESC) = 1;
INSERT INTO dq_issue
SELECT 'note_conflicting_versions', 'stg_note', CAST(note_no AS VARCHAR), source_file, NULL, 'two different documents share this note number; kept the latest file'
FROM int_note_unique QUALIFY row_number() OVER (PARTITION BY note_no ORDER BY source_file DESC) > 1;

-- ---------------------------------------------------------------- 3. workbooks
CREATE OR REPLACE TABLE int_workbook AS
SELECT w.* EXCLUDE (grower_raw), coalesce(nullif(trim(w.grower_raw), ''), b.default_grower) AS grower_raw,
       w.grower_raw IS NULL OR trim(w.grower_raw) = '' AS grower_defaulted, b.role, b.buyer_id,
       (w.is_subtotal_hint OR w.note_no IS NULL)                                 AS is_subtotal,
       CASE WHEN w.weight_raw < 1000 THEN w.weight_raw * 1000 ELSE w.weight_raw END AS net_kg,
       w.weight_raw < 1000                                                       AS was_tons,
       TRY_CAST(w.date_raw AS DATE)                                              AS d
FROM stg_workbook_row w LEFT JOIN cfg_workbook b USING (book);

INSERT INTO dq_issue
SELECT 'workbook_subtotal_row', 'stg_workbook_row', book || ':' || row_num, CAST(weight_raw AS VARCHAR), NULL, 'subtotal row; dropped'
FROM int_workbook WHERE is_subtotal;
INSERT INTO dq_issue
SELECT 'workbook_grower_blank', 'stg_workbook_row', book || ':' || row_num, NULL, grower_raw, 'blank grower in a workbook that lists one grower; filled from cfg_workbook.default_grower'
FROM int_workbook WHERE grower_defaulted AND grower_raw IS NOT NULL AND NOT is_subtotal;
INSERT INTO dq_issue
SELECT 'workbook_weight_in_tons', 'stg_workbook_row', book || ':' || row_num, CAST(weight_raw AS VARCHAR), CAST(net_kg AS VARCHAR), 'value < 1,000 read as tons; x1,000'
FROM int_workbook WHERE was_tons AND NOT is_subtotal;

-- a date is valid if it parses and falls in the business's years; otherwise borrow it from another copy of the same note
CREATE OR REPLACE TABLE int_workbook_dated AS
WITH ok AS (SELECT *, d IS NOT NULL AND year(d) BETWEEN 2009 AND 2030 AS date_ok FROM int_workbook WHERE NOT is_subtotal),
alt AS (SELECT note_no, min(d) AS d_alt FROM ok WHERE date_ok GROUP BY note_no)
SELECT ok.* EXCLUDE (d), CASE WHEN date_ok THEN d ELSE alt.d_alt END AS ship_date, date_ok
FROM ok LEFT JOIN alt USING (note_no);
INSERT INTO dq_issue
SELECT 'workbook_bad_date', 'stg_workbook_row', book || ':' || row_num, date_raw, CAST(ship_date AS VARCHAR),
       CASE WHEN ship_date IS NULL THEN 'impossible date, no other copy; dropped' ELSE 'impossible date; taken from another copy of the note' END
FROM int_workbook_dated WHERE NOT date_ok;

-- one row per note number: a buyer workbook beats the master sheet (it carries the buyer)
CREATE OR REPLACE TABLE int_workbook_shipment AS
SELECT * FROM int_workbook_dated WHERE ship_date IS NOT NULL
QUALIFY row_number() OVER (PARTITION BY note_no ORDER BY (role = 'buyer') DESC, book) = 1;
INSERT INTO dq_issue
SELECT 'workbook_copy', 'stg_workbook_row', book || ':' || row_num, CAST(note_no AS VARCHAR), NULL, 'same shipment already in another workbook; dropped'
FROM int_workbook_dated WHERE ship_date IS NOT NULL
QUALIFY row_number() OVER (PARTITION BY note_no ORDER BY (role = 'buyer') DESC, book) > 1;

-- ---------------------------------------------------------------- 4. fact_shipment = notes + workbook-only shipments
CREATE OR REPLACE TABLE fact_shipment AS
WITH notes AS (
    SELECT n.note_no,
           CASE WHEN year(n.note_date) BETWEEN 2009 AND 2030 THEN n.note_date ELSE w.ship_date END AS ship_date,
           coalesce(b.counterparty_id, n.buyer_raw) AS buyer_id,
           n.grower_raw, n.variety_raw, n.net_kg, n.port, n.container, 'delivery_note' AS source, n.source_file AS lineage
    FROM int_note n LEFT JOIN cfg_buyer b ON n.buyer_raw = b.name_on_paper
    LEFT JOIN int_workbook_shipment w USING (note_no)
),
wb_only AS (
    SELECT w.note_no, w.ship_date, w.buyer_id, w.grower_raw, w.variety_raw, w.net_kg, w.destination, NULL,
           'workbook_rebuild', w.book || ':' || w.row_num
    FROM int_workbook_shipment w
    WHERE w.note_no NOT IN (SELECT note_no FROM int_note) AND w.role IN ('master', 'buyer')
)
SELECT s.*, year(s.ship_date) AS season,
       v.variety, g.grower_class, g.grower_class = 'own' AS is_own,
       s.net_kg * coalesce(g.own_share, 0) AS own_kg
FROM (SELECT * FROM notes UNION ALL SELECT * FROM wb_only) s
LEFT JOIN map_variety_alias v USING (variety_raw)
LEFT JOIN map_grower_alias  g USING (grower_raw);

INSERT INTO dq_issue
SELECT 'note_bad_date', 'stg_note', CAST(n.note_no AS VARCHAR), CAST(n.note_date AS VARCHAR), CAST(f.ship_date AS VARCHAR),
       CASE WHEN f.ship_date IS NULL THEN 'no usable date anywhere' ELSE 'missing/impossible date on the note; taken from the workbook' END
FROM int_note n JOIN fact_shipment f USING (note_no)
WHERE n.note_date IS NULL OR year(n.note_date) NOT BETWEEN 2009 AND 2030;
DELETE FROM fact_shipment WHERE ship_date IS NULL;

INSERT INTO dq_issue
SELECT 'container_shared_by_growers', 'fact_shipment', CAST(note_no AS VARCHAR), grower_raw, CAST(round(own_kg) AS VARCHAR),
       'several growers in one container; own kg = net kg / number of growers'
FROM fact_shipment WHERE grower_class = 'mixed';

INSERT INTO dq_issue
SELECT 'season_rebuilt_from_workbooks', 'fact_shipment', CAST(note_no AS VARCHAR), NULL, CAST(net_kg AS VARCHAR), 'no delivery note exists; shipment taken from the workbooks'
FROM fact_shipment WHERE source = 'workbook_rebuild';

-- notes vs workbooks: when both exist, the note wins; log weight disagreements over 0.5%
INSERT INTO dq_issue
SELECT 'kg_mismatch_note_vs_workbook', 'fact_shipment', CAST(n.note_no AS VARCHAR), CAST(w.net_kg AS VARCHAR), CAST(n.net_kg AS VARCHAR),
       'workbook weight differs from the note; note kept'
FROM int_note n JOIN int_workbook_shipment w USING (note_no)
WHERE abs(w.net_kg - n.net_kg) > 0.005 * n.net_kg;

-- ---------------------------------------------------------------- 5. VAT
CREATE OR REPLACE TABLE fact_vat_line AS
SELECT *, CAST(left(period, 4) AS INTEGER) AS year, CAST(right(period, 2) AS INTEGER) AS month,
       CASE WHEN amount <> 0 AND vat / amount > 0.5 THEN 'vat_exceeds_amount' END AS dq_flag
FROM stg_vat_line
WHERE entity = param('own_entity');
INSERT INTO dq_issue
SELECT 'vat_exceeds_amount', 'fact_vat_line', period || ':' || coalesce(reference, ''), CAST(vat AS VARCHAR), NULL,
       'VAT larger than half the amount; flagged and excluded from totals'
FROM fact_vat_line WHERE dq_flag IS NOT NULL;

-- ---------------------------------------------------------------- 6. counterparties and channels (rules are data in this query, visible in one place)
CREATE OR REPLACE TABLE dim_counterparty AS
WITH s AS (
    SELECT counterparty, count(*) AS n_invoices, sum(amount) AS sales_ils, median(amount) AS median_invoice,
           avg(CASE WHEN vat > 0 THEN 1 ELSE 0 END) AS vat_share, count(DISTINCT period) AS active_months
    FROM fact_vat_line
    WHERE direction = 'sale' AND dq_flag IS NULL AND year >= CAST(param('from_year') AS INTEGER)
    GROUP BY counterparty
)
SELECT s.counterparty AS counterparty_id, s.*,
       CASE WHEN s.counterparty IN (SELECT counterparty_id FROM cfg_buyer)          THEN 'Export (monthly settlement)'
            WHEN s.vat_share > 0.5                                                 THEN 'Packing services'
            WHEN s.n_invoices * 1.0 / s.active_months > 3 AND s.median_invoice < 50000 THEN 'Local wholesale'
            WHEN s.sales_ils < 1000000                                             THEN 'Other'
            ELSE 'Contract buyer' END AS channel,
       CASE WHEN s.counterparty IN (SELECT counterparty_id FROM cfg_buyer) THEN 'named export buyer (appears on delivery notes)'
            WHEN s.vat_share > 0.5 THEN 'VAT charged on most invoices => service, not produce'
            WHEN s.n_invoices * 1.0 / s.active_months > 3 AND s.median_invoice < 50000 THEN 'many small invoices a month'
            WHEN s.sales_ils < 1000000 THEN 'small total'
            ELSE 'few large invoices' END AS channel_reason
FROM s;
