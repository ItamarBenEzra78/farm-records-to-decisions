-- no subtotal or non-positive weights reached the fact table
SELECT * FROM fact_shipment WHERE net_kg IS NULL OR net_kg <= 0 OR net_kg > 60000;
