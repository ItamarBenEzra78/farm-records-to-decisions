-- flagged VAT lines never reach the marts: the cash curve, scaled back by years, equals the clean total in the window
WITH w AS (SELECT * FROM fact_vat_line WHERE year >= CAST(param('from_year') AS INTEGER))
SELECT 1 WHERE abs((SELECT sum(net_ils) FROM mart_cash_curve) * (SELECT count(DISTINCT year) FROM w)
                 - (SELECT sum(CASE WHEN direction = 'sale' THEN amount ELSE -amount END) FROM w WHERE dq_flag IS NULL)) > 1;
