from dagster import DailyPartitionsDefinition

# Daily partitions are pinned to UTC so partition keys (dt=YYYY-MM-DD) are
# stable regardless of the machine's local timezone.
daily_partitions = DailyPartitionsDefinition(
    start_date="2025-05-01",
    timezone="UTC",
)

