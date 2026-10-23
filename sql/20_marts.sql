-- =====================================================================
-- MARTS: one view per business question. Charts and the README read these.
-- =====================================================================

-- Q1 who pays us, and how concentrated is it
CREATE OR REPLACE VIEW mart_channel_share AS
SELECT channel, sum(sales_ils) AS sales_ils, sum(sales_ils) / (SELECT sum(sales_ils) FROM dim_counterparty) AS share,
       count(*) AS customers
FROM dim_counterparty GROUP BY channel ORDER BY sales_ils DESC;

CREATE OR REPLACE VIEW mart_concentration AS
WITH c AS (SELECT counterparty_id, channel, sales_ils, sales_ils / sum(sales_ils) OVER () AS share,
                  row_number() OVER (ORDER BY sales_ils DESC) AS rk FROM dim_counterparty)
SELECT max(CASE WHEN rk = 1 THEN share END)        AS largest_customer_share,
       max(CASE WHEN rk = 1 THEN channel END)      AS largest_customer_channel,
       sum(CASE WHEN rk <= 2 THEN share END)       AS top2_share,
       sum(CASE WHEN rk <= 5 THEN share END)       AS top5_share,
       sum(share * share)                          AS hhi                  -- 1 = one customer, ~0 = fragmented
FROM c;

-- Q2 what a kilogram of own export earns per season, and the change year over year
CREATE OR REPLACE VIEW mart_price_per_kg_season AS
WITH kg AS (SELECT season, sum(own_kg) AS own_export_kg, count(*) FILTER (WHERE own_kg > 0) AS containers
            FROM fact_shipment GROUP BY season),
ils AS (SELECT year AS season, sum(amount) AS settled_ils FROM fact_vat_line      -- produce is zero-VAT; buyers' VAT-charged
        WHERE direction = 'sale' AND dq_flag IS NULL AND vat = 0                      -- invoices are other services
          AND counterparty IN (SELECT counterparty_id FROM cfg_buyer)
        GROUP BY year)
SELECT kg.season, kg.containers, kg.own_export_kg, ils.settled_ils,
       ils.settled_ils / kg.own_export_kg AS ils_per_kg,
       ils.settled_ils / kg.own_export_kg / lag(ils.settled_ils / kg.own_export_kg) OVER (ORDER BY kg.season) - 1 AS change_vs_prev
FROM kg JOIN ils USING (season)
WHERE kg.containers >= 30 AND kg.season >= CAST(param('from_year') AS INTEGER)     -- skip stray seasons with a handful of notes
ORDER BY season;

-- Q3a settlement lag: ILS-weighted month minus kg-weighted month, per season
CREATE OR REPLACE VIEW mart_settlement_lag AS
WITH k AS (SELECT season, sum(own_kg * month(ship_date)) / sum(own_kg) AS kg_month FROM fact_shipment WHERE own_kg > 0 GROUP BY season),
i AS (SELECT year AS season, sum(amount * month) / sum(amount) AS ils_month FROM fact_vat_line
      WHERE direction = 'sale' AND dq_flag IS NULL AND vat = 0 AND counterparty IN (SELECT counterparty_id FROM cfg_buyer) GROUP BY year)
SELECT season, kg_month, ils_month, ils_month - kg_month AS lag_months FROM k JOIN i USING (season)
WHERE season IN (SELECT season FROM mart_price_per_kg_season) ORDER BY season;

-- Q3b cumulative cash over the farming year (Sep -> Aug), averaged over seasons. VAT-visible flows only:
--     no payroll, no supplier payment terms, no VAT refunds.
CREATE OR REPLACE VIEW mart_cash_curve AS
WITH m AS (
    SELECT month,
           sum(CASE WHEN direction = 'sale' THEN amount ELSE -amount END)
             / (SELECT count(DISTINCT year) FROM fact_vat_line WHERE year >= CAST(param('from_year') AS INTEGER)) AS net_ils
    FROM fact_vat_line WHERE dq_flag IS NULL AND year >= CAST(param('from_year') AS INTEGER) GROUP BY month
),
o AS (SELECT month, net_ils, (month + 3) % 12 AS farm_order FROM m)              -- Sep = 0 ... Aug = 11
SELECT month, farm_order, net_ils, sum(net_ils) OVER (ORDER BY farm_order) AS cumulative_ils
FROM o ORDER BY farm_order;

-- Q4 how steady each channel is from year to year
CREATE OR REPLACE VIEW mart_channel_stability AS
WITH y AS (
    SELECT c.channel, v.year, sum(v.amount) AS ils
    FROM fact_vat_line v JOIN dim_counterparty c ON v.counterparty = c.counterparty_id
    WHERE v.direction = 'sale' AND v.dq_flag IS NULL AND v.year >= CAST(param('from_year') AS INTEGER) GROUP BY ALL
)
SELECT channel, avg(ils) AS avg_ils, stddev_samp(ils) / avg(ils) AS cv, count(*) AS years
FROM y GROUP BY channel ORDER BY cv;

-- extra: what the packing house earns per container it packs for other growers
CREATE OR REPLACE VIEW mart_packing_fee_per_container AS
WITH fee AS (SELECT year AS season, sum(amount) AS service_ils FROM fact_vat_line v
             JOIN dim_counterparty c ON v.counterparty = c.counterparty_id
             WHERE c.channel = 'Packing services' AND v.direction = 'sale' AND v.dq_flag IS NULL
               AND v.year >= CAST(param('from_year') AS INTEGER) GROUP BY year),
cnt AS (SELECT season, count(*) AS other_containers, sum(net_kg) AS other_kg FROM fact_shipment WHERE grower_class = 'other' GROUP BY season)
SELECT season, service_ils, other_containers, service_ils / other_containers AS ils_per_container, service_ils / other_kg AS ils_per_kg
FROM fee JOIN cnt USING (season) ORDER BY season;

-- data-quality summary: the README's "data challenges" table is generated from this
CREATE OR REPLACE VIEW dq_summary AS
SELECT issue_code, count(*) AS rows_affected, any_value(action) AS action FROM dq_issue GROUP BY issue_code ORDER BY rows_affected DESC;
