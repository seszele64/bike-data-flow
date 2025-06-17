from dagster import DailyPartitionsDefinition

# Define daily partitions with your local timezone
daily_partitions = DailyPartitionsDefinition(
    start_date="2025-05-01"
)

