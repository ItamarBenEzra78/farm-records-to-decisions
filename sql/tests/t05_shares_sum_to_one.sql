-- shares add up to 100%
SELECT 1 WHERE abs((SELECT sum(share) FROM mart_channel_share) - 1) > 1e-9;
