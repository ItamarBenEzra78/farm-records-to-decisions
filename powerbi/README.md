# Power BI dashboard

Five pages on top of the warehouse: the four money questions plus data quality.
Synthetic data only ("Oren Fields"); the real farm data never leaves the private build.

```
python generate_data.py && python build.py && python export_powerbi.py
```
`export_powerbi.py` writes a star schema to `powerbi/data/*.csv` and the numbers every page must show to
`powerbi/expected.csv`. Before writing, it recomputes each number from the exported tables with the same logic as
the DAX below and compares it with the SQL marts (43 values); a mismatch fails the export and CI.

## 1. Load
**Browser (app.powerbi.com, work/school account):** upload `powerbi/data/farm_model.xlsx` (one sheet per table) to
My workspace → it becomes one semantic model → "Open data model" to set types, relationships and measures below.

**Power BI Desktop (Windows):**
Get data → Text/CSV → each file in `powerbi/data/`. In Power Query set types:
`date`, `ship_date` = Date · `amount`, `vat`, `signed_amount`, `net_kg`, `own_kg`, `kg_month`, `ils_month`, `lag_months` = Decimal ·
`year`, `month`, `season`, `farm_month_order`, `farm_year_start` = Whole number · `is_valid`, `is_own`, `is_export_buyer` = True/False ·
`counterparty_id`, `buyer_id`, `note_no`, `reference`, `record_key` = Text.

## 2. Model
| From (one) | To (many) | Direction |
|---|---|---|
| `dim_date[date]` | `fact_vat_line[date]` | single |
| `dim_date[date]` | `fact_shipment[ship_date]` | single |
| `dim_counterparty[counterparty_id]` | `fact_vat_line[counterparty_id]` | single |
| `dim_counterparty[counterparty_id]` | `fact_shipment[buyer_id]` | single |

`dq_issue` and `mart_settlement_lag` stay unrelated. Mark `dim_date` as the date table (column `date`).
In `dim_date`, sort `month_name` by `farm_month_order` (farming year Sep → Aug). Hide the fact keys.

## 3. Measures
Create an empty table `_Measures` and add these. `2022` is `cfg_param.from_year`.

```DAX
-- Q1 who pays us
Sales ILS =
CALCULATE ( SUM ( fact_vat_line[amount] ),
    fact_vat_line[direction] = "sale", fact_vat_line[is_valid] = TRUE (),
    KEEPFILTERS ( dim_date[year] >= 2022 ) )

Share of sales = DIVIDE ( [Sales ILS], CALCULATE ( [Sales ILS], REMOVEFILTERS ( dim_counterparty ) ) )

Largest customer share = MAXX ( ALL ( dim_counterparty[counterparty_id] ), [Share of sales] )

Top 2 share =
VAR t = TOPN ( 2, ALL ( dim_counterparty[counterparty_id] ), [Sales ILS] )
RETURN DIVIDE ( CALCULATE ( [Sales ILS], t ), CALCULATE ( [Sales ILS], REMOVEFILTERS ( dim_counterparty ) ) )

Top 5 share =
VAR t = TOPN ( 5, ALL ( dim_counterparty[counterparty_id] ), [Sales ILS] )
RETURN DIVIDE ( CALCULATE ( [Sales ILS], t ), CALCULATE ( [Sales ILS], REMOVEFILTERS ( dim_counterparty ) ) )

HHI = SUMX ( ALL ( dim_counterparty[counterparty_id] ), [Share of sales] ^ 2 )   -- 1 = one customer

-- Q2 what a kilogram of own export earns
Own export kg = SUM ( fact_shipment[own_kg] )

Own containers = CALCULATE ( COUNTROWS ( fact_shipment ), fact_shipment[own_kg] > 0 )

Settled export ILS =
CALCULATE ( [Sales ILS], fact_vat_line[vat] = 0, dim_counterparty[is_export_buyer] = TRUE () )

ILS per kg =
IF ( [Own containers] >= 30 && MAX ( dim_date[year] ) >= 2022,
     DIVIDE ( [Settled export ILS], [Own export kg] ) )

Change vs previous season =
VAR y = SELECTEDVALUE ( dim_date[year] )
VAR prev = CALCULATE ( [ILS per kg], REMOVEFILTERS ( dim_date ), dim_date[year] = y - 1 )
RETURN IF ( NOT ISBLANK ( prev ), DIVIDE ( [ILS per kg], prev ) - 1 )

-- Q3 cash through the farming year (VAT-visible flows only: no payroll, no payment terms)
Years in range =
CALCULATE ( DISTINCTCOUNT ( fact_vat_line[year] ), REMOVEFILTERS ( dim_date ), dim_date[year] >= 2022 )

Avg net cash ILS =
DIVIDE ( CALCULATE ( SUM ( fact_vat_line[signed_amount] ), fact_vat_line[is_valid] = TRUE (),
                     KEEPFILTERS ( dim_date[year] >= 2022 ) ),
         [Years in range] )

Cumulative cash ILS =
VAR m = MAX ( dim_date[farm_month_order] )
RETURN CALCULATE ( [Avg net cash ILS], REMOVEFILTERS ( dim_date ), dim_date[farm_month_order] <= m )

-- Q4 how steady each channel is
Channel CV =
VAR t = FILTER ( ADDCOLUMNS ( VALUES ( dim_date[year] ), "@s", [Sales ILS] ), NOT ISBLANK ( [@s] ) )
RETURN DIVIDE ( STDEVX.S ( t, [@s] ), AVERAGEX ( t, [@s] ) )

-- Data quality
Rows affected = COUNTROWS ( dq_issue )
```
Formats: shares, HHI, change and CV as %; ILS as whole numbers with thousands separator; ILS per kg with 2 decimals.

## 4. Pages
| Page | Visuals |
|---|---|
| **Who pays us** | Cards: Largest customer share, Top 5 share, HHI · Bar: Share of sales by `channel` · Table: `counterparty_id`, `channel`, `channel_reason`, Sales ILS, Share of sales |
| **What a kilo earns** | Line and clustered column, x = `dim_date[year]` (filter 2022–2025): columns Own export kg, line ILS per kg · Table: year, ILS per kg, Change vs previous season |
| **Cash through the year** | Line: x = `month_name` (farm order), y = Cumulative cash ILS · Columns: Avg net cash ILS by month · Table: `mart_settlement_lag` (season, lag_months) · Text box: "VAT-visible flows only" |
| **How steady each channel is** | Bar: Channel CV by `channel` · Line: Sales ILS by `year`, legend `channel` |
| **Data quality** | Bar: Rows affected by `issue_code` · Table: `issue_code`, `action`, Rows affected |

One sentence of conclusion at the top of each page, written from the numbers on it, not from the README.

## 5. Check before saving
Every number must match `powerbi/expected.csv` (rounded):

| Page | Value |
|---|---|
| Who pays us | largest customer 36.6% · top 2 54.7% · top 5 85.3% · HHI 0.213 · Export 53.5%, Packing 19.7%, Contract 18.2%, Local 8.6% |
| What a kilo earns | 2022 1.71 · 2023 1.83 (+7.2%) · 2024 2.39 (+30.1%) · 2025 1.44 (−39.7%) ILS/kg |
| Cash through the year | lowest point −6.80M ILS in March (order 6) · end of August +1.32M ILS |
| How steady | CV: Packing 1.0%, Contract 4.9%, Local 8.3%, Export 24.1% |
| Data quality | 12 issue types, 7,562 rows; largest: workbook weight in tons (3,055) |

Save as `powerbi/farm-records-to-decisions.pbix` and export one PNG per page to `reports/figures/powerbi_*.png`.
