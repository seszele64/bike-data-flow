"""Partitioning contract tests for the stations assets (T2-A2).

The daily partitions used by the stations assets must be pinned to UTC so
partition keys (``dt=YYYY-MM-DD``) are stable regardless of the local
timezone of the machine evaluating them. All assertions are pure in-memory:
no S3, no network, no filesystem access beyond test collection.
"""

from datetime import datetime, timezone

from wrm_pipeline.assets.stations.commons import daily_partitions

# 2025-05-01T00:00:00 UTC — the partition window's pinned start.
EXPECTED_START_UTC = datetime(2025, 5, 1, 0, 0, 0, tzinfo=timezone.utc)


class TestDailyPartitions:
    """Contract: daily partitions are UTC-pinned, starting 2025-05-01."""

    def test_timezone_is_pinned_to_utc(self):
        """timezone is explicitly UTC (dagster stores the tz as a string)."""
        assert daily_partitions.timezone == "UTC"

    def test_start_is_2025_05_01_utc(self):
        """The partition window starts at midnight UTC on 2025-05-01."""
        assert daily_partitions.start == EXPECTED_START_UTC
        assert daily_partitions.start_timestamp == EXPECTED_START_UTC.timestamp()

    def test_first_partition_key_matches_utc_start(self):
        """First key is the UTC start date, independent of local time."""
        assert daily_partitions.get_first_partition_key() == "2025-05-01"
