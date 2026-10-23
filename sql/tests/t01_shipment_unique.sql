-- every shipment appears once
SELECT note_no, count(*) FROM fact_shipment GROUP BY note_no HAVING count(*) > 1;
