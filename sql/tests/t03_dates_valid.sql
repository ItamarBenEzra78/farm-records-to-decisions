-- every shipment has a date inside the business years
SELECT * FROM fact_shipment WHERE ship_date IS NULL OR year(ship_date) NOT BETWEEN 2009 AND 2030;
