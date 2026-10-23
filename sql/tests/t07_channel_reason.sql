-- every channel assignment has a stated reason
SELECT * FROM dim_counterparty WHERE channel IS NULL OR channel_reason IS NULL;
