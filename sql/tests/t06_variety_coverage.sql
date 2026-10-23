-- unmatched varieties stay below 5% of shipped kg (they are listed in dq_issue)
SELECT 1 WHERE (SELECT sum(net_kg) FILTER (WHERE variety IS NULL) / sum(net_kg) FROM fact_shipment) > 0.05;
