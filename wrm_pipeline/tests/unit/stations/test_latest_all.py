"""Focused unit tests for the unpartitioned "latest data" station assets.

``latest_all.py`` introduces a second, unpartitioned path next to the daily
``processed_all`` / ``enhanced_all`` archive assets:

* it consumes the single S3 key emitted by ``wrm_stations_raw_data`` (no S3
  listing at all),
* it reuses the shared parser (``parse_raw_stations_csv``) and the real
  pandera contracts,
* it always overwrites the stable ``processed/latest`` and ``enhanced/latest``
  parquet keys instead of emitting timestamped daily keys.

The tests below deliberately exercise the *real* schemas (unlike the legacy
asset tests, which patch ``processed_data_schema`` out) and pin the two
properties that make this path safe for a 15-minute refresh loop:

1. output keys are stable/unpartitioned (overwritten, never timestamped), and
2. malformed input fails closed: no ``put_object`` and no success metadata.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import Mock

import pandas as pd
import pytest
from dagster import AssetKey, DagsterTypeCheckDidNotPass, build_asset_context

from wrm_pipeline.assets.stations.latest_all import (
    ENHANCED_COLUMNS,
    WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
    WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
    _raw_file_timestamp,
    wrm_stations_enhanced_data_latest_asset,
    wrm_stations_processed_data_latest_asset,
)
from wrm_pipeline.config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX
from wrm_pipeline.models.stations import enhanced_daily_schema, processed_data_schema

# ---------------------------------------------------------------------------
# Payload builders / fixtures
# ---------------------------------------------------------------------------

RAW_KEY = (
    f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/"
    "wrm_stations_2024-01-15_10-30-45.txt"
)

HEADER = (
    "#id,1705147845.123|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,"
    "temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs"
)


def make_row(
    station_id: str = "001",
    packed: str = "1705147845.123|3600|-3600",
    name: str = "Station 1",
    lat: str = "51.1089",
    lon: str = "17.0377",
    bikes: str = "5",
    spaces: str = "10",
    installed: str = "true",
    locked: str = "false",
    temporary: str = "false",
    total_docks: str = "15",
    givesbonus: str = "false",
    pedelecs: str = "2",
) -> str:
    """Build one raw-format row (comma-joined, no quoting)."""
    return ",".join(
        [
            station_id,
            packed,
            name,
            lat,
            lon,
            bikes,
            spaces,
            installed,
            locked,
            temporary,
            total_docks,
            givesbonus,
            pedelecs,
        ]
    )


def make_payload(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


# Covers the three record classes used by the enhanced asset:
#   station -> numeric id, non-BIKE name
#   bike    -> fb* id AND BIKE* name
#   unknown -> everything else (fb* with a station name / BIKE name with a
#              numeric id are both deliberately *not* classified)
SAMPLE_ROWS = [
    make_row(station_id="001", name="Station 1"),
    make_row(
        station_id="fb001",
        name="BIKE 1",
        bikes="0",
        spaces="12",
        givesbonus="true",
        pedelecs="3",
    ),
    make_row(station_id="fb002", name="Station 2"),
    make_row(station_id="x001", name="BIKE 2"),
    make_row(station_id="002", name="Station 3", bikes="2", spaces="13"),
]
SAMPLE_PAYLOAD = make_payload(*SAMPLE_ROWS)


def stub_raw_object(s3: Mock, payload: str = SAMPLE_PAYLOAD) -> None:
    body = Mock()
    body.read.return_value = payload.encode("utf-8")
    s3.get_object.return_value = {"Body": body}


def make_context(s3: Mock):
    """Asset context whose metadata sink is a mock we can inspect."""
    context = build_asset_context(resources={"s3_resource": s3})
    context.add_output_metadata = Mock()
    return context


def run_processed(
    s3: Mock, payload: str = SAMPLE_PAYLOAD, raw_key: str = RAW_KEY
):
    stub_raw_object(s3, payload)
    context = make_context(s3)
    result = wrm_stations_processed_data_latest_asset(context, raw_key)
    return result, context


def written_kwargs(s3: Mock) -> dict:
    """Return the kwargs of the single expected S3 write."""
    assert s3.put_object.call_count == 1
    return s3.put_object.call_args.kwargs


def written_body(s3: Mock) -> pd.DataFrame:
    return pd.read_parquet(BytesIO(written_kwargs(s3)["Body"]))


def assert_no_s3_listing(s3: Mock) -> None:
    """The latest path must never enumerate the bucket."""
    assert s3.list_objects_v2.call_count == 0
    assert s3.list_objects.call_count == 0


@pytest.fixture
def processed_frame() -> pd.DataFrame:
    """A real, schema-validated processed row set derived from SAMPLE_PAYLOAD."""
    frame, _ = run_processed(Mock())
    return frame


# ---------------------------------------------------------------------------
# Stable / unpartitioned output keys
# ---------------------------------------------------------------------------


class TestStableLatestKeys:
    def test_key_constants_are_the_expected_stable_paths(self):
        assert WRM_STATIONS_PROCESSED_LATEST_S3_KEY == (
            f"{WRM_STATIONS_S3_PREFIX}processed/latest/"
            "all_processed_latest.parquet"
        )
        assert WRM_STATIONS_ENHANCED_LATEST_S3_KEY == (
            f"{WRM_STATIONS_S3_PREFIX}enhanced/latest/all_enhanced_latest.parquet"
        )

    def test_keys_are_unpartitioned_and_distinct(self):
        for key in (
            WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
            WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
        ):
            # No dt=YYYY-MM-DD partition segment and no timestamped filename.
            assert "dt=" not in key
            assert "/latest/" in key
            assert key.endswith(".parquet")
        assert (
            WRM_STATIONS_PROCESSED_LATEST_S3_KEY
            != WRM_STATIONS_ENHANCED_LATEST_S3_KEY
        )

    def test_processed_asset_overwrites_same_key_for_different_raw_snapshots(self):
        s3 = Mock()
        keys = []

        for raw_key in (
            RAW_KEY,
            f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-16/"
            "wrm_stations_2024-01-16_09-00-00.txt",
        ):
            run_processed(s3, raw_key=raw_key)
            keys.append(s3.put_object.call_args.kwargs["Key"])

        assert keys == [
            WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
            WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
        ]
        # Neither write may target a daily archive key.
        assert all("processed/all/" not in key for key in keys)

    def test_enhanced_asset_overwrites_same_key_for_different_inputs(self):
        s3 = Mock()
        keys = []

        first, _ = run_processed(
            s3,
            raw_key=f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/"
            "wrm_stations_2024-01-15_10-30-45.txt",
        )
        second, _ = run_processed(
            s3,
            raw_key=f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/"
            "wrm_stations_2024-01-15_12-00-00.txt",
        )

        for frame in (first, second):
            context = make_context(s3)
            wrm_stations_enhanced_data_latest_asset(context, frame)
            keys.append(s3.put_object.call_args.kwargs["Key"])

        assert keys == [
            WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
            WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
        ]
        assert all("enhanced/all/" not in key for key in keys)


# ---------------------------------------------------------------------------
# Processed latest asset: happy path, schema, metadata
# ---------------------------------------------------------------------------


class TestProcessedLatestAsset:
    def test_happy_path_returns_validated_frame_and_writes_parquet(self):
        s3 = Mock()
        result, context = run_processed(s3)

        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(SAMPLE_ROWS)
        # Real schema + exact column order (the contract downstream relies on).
        assert list(result.columns) == list(processed_data_schema.columns)
        processed_data_schema.validate(result)
        assert result["timestamp"].dtype == "datetime64[ns]"
        assert result["file_timestamp"].dtype == "datetime64[us]"

        # Exactly one read of the single raw key handed in by the caller.
        assert s3.get_object.call_count == 1
        assert s3.get_object.call_args.kwargs == {
            "Bucket": BUCKET_NAME,
            "Key": RAW_KEY,
        }

        kwargs = written_kwargs(s3)
        assert kwargs["Bucket"] == BUCKET_NAME
        assert kwargs["Key"] == WRM_STATIONS_PROCESSED_LATEST_S3_KEY
        assert kwargs["ContentType"] == "application/octet-stream"

        # The parquet body is schema-valid and matches the returned frame.
        roundtrip = written_body(s3)
        processed_data_schema.validate(roundtrip)
        pd.testing.assert_frame_equal(
            roundtrip, result.reset_index(drop=True), check_dtype=True
        )

        # No listing, no success metadata before a successful write.
        assert_no_s3_listing(s3)
        assert context.add_output_metadata.called

    def test_metadata_reports_stable_key_and_source_provenance(self):
        s3 = Mock()
        result, context = run_processed(s3)

        metadata = context.add_output_metadata.call_args.args[0]
        assert metadata["s3_key"] == WRM_STATIONS_PROCESSED_LATEST_S3_KEY
        assert metadata["raw_s3_key"] == RAW_KEY
        assert metadata["file_timestamp"] == "2024-01-15T10:30:45"
        assert metadata["schema_validation"] == "PASSED"
        assert metadata["total_records"] == len(SAMPLE_ROWS)
        assert metadata["columns"] == list(result.columns)
        assert metadata["column_types"] == dict(result.dtypes.astype(str))

    def test_source_columns_are_derived_from_the_single_raw_key(self):
        s3 = Mock()
        result, _ = run_processed(s3)

        assert result["s3_source_key"].tolist() == [RAW_KEY] * len(SAMPLE_ROWS)
        assert result["file_timestamp"].nunique() == 1
        assert pd.Timestamp(result["file_timestamp"].iloc[0]) == pd.Timestamp(
            "2024-01-15 10:30:45"
        )

    def test_upload_precedes_success_metadata(self):
        """Fail-closed ordering: never publish PASSED metadata for a failed write."""
        s3 = Mock()
        stub_raw_object(s3)
        context = make_context(s3)
        order: list[str] = []
        s3.put_object.side_effect = lambda **kwargs: order.append("put_object")
        context.add_output_metadata.side_effect = lambda *a, **k: order.append(
            "add_output_metadata"
        )

        wrm_stations_processed_data_latest_asset(context, RAW_KEY)

        assert order == ["put_object", "add_output_metadata"]

    def test_partially_malformed_payload_uploads_only_parseable_rows(self):
        payload = make_payload(
            make_row(station_id="001"),
            "002,corrupted_row_data",
            make_row(station_id="003", name="Station 3"),
        )
        s3 = Mock()
        result, _ = run_processed(s3, payload=payload)

        assert result["station_id"].tolist() == ["001", "003"]
        assert written_kwargs(s3)["Key"] == (
            WRM_STATIONS_PROCESSED_LATEST_S3_KEY
        )


# ---------------------------------------------------------------------------
# Raw-key timestamp derivation (the helper behind file_timestamp)
# ---------------------------------------------------------------------------


class TestRawFileTimestamp:
    def test_timestamp_is_parsed_from_the_raw_key(self):
        log = Mock()
        timestamp = _raw_file_timestamp(RAW_KEY, log)

        assert timestamp == datetime(2024, 1, 15, 10, 30, 45)
        log.warning.assert_not_called()

    def test_falls_back_to_now_with_a_warning_when_key_has_no_timestamp(self):
        log = Mock()
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        timestamp = _raw_file_timestamp("somewhere/latest_stations.txt", log)
        after = datetime.now(timezone.utc).replace(tzinfo=None)

        assert before <= timestamp <= after
        log.warning.assert_called_once()
        assert "somewhere/latest_stations.txt" in log.warning.call_args.args[0]


# ---------------------------------------------------------------------------
# Processed latest asset: malformed input fails closed (no upload)
# ---------------------------------------------------------------------------


class TestProcessedLatestFailClosed:
    def test_empty_string_raw_key_rejected_before_s3(self):
        s3 = Mock()
        context = make_context(s3)

        with pytest.raises(ValueError, match="did not provide an S3 key"):
            wrm_stations_processed_data_latest_asset(context, "")

        s3.get_object.assert_not_called()
        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()

    @pytest.mark.parametrize("raw_key", [None, 0, 123, 1.5])
    def test_non_string_raw_key_is_rejected_by_the_input_type_check(self, raw_key):
        """Dagster rejects the declared ``str`` input before compute runs.

        The asset's own ``isinstance`` guard is a second line of defence; the
        observable contract that matters for a refresh loop is identical:
        fail before any S3 read/write.
        """
        s3 = Mock()
        context = make_context(s3)

        with pytest.raises(DagsterTypeCheckDidNotPass):
            wrm_stations_processed_data_latest_asset(context, raw_key)

        s3.get_object.assert_not_called()
        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()

    def test_get_object_failure_is_wrapped_and_never_uploads(self):
        s3 = Mock()
        s3.get_object.side_effect = Exception("AccessDenied")
        context = make_context(s3)

        with pytest.raises(
            RuntimeError, match="Failed to read raw station data object"
        ):
            wrm_stations_processed_data_latest_asset(context, RAW_KEY)

        assert s3.get_object.call_count == 1
        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()

    @pytest.mark.parametrize(
        "payload",
        [
            "",
            HEADER,
            HEADER + "\n",
            make_payload("001,corrupted_row_data"),
            make_payload(make_row(packed="1705147845.123|not_a_number|-3600")),
            make_payload(make_row(lat="invalid_lat")),
        ],
        ids=[
            "empty-string",
            "header-only",
            "header-trailing-newline",
            "unparseable-row",
            "non-numeric-offset",
            "non-numeric-lat",
        ],
    )
    def test_unparseable_payload_raises_before_any_upload(self, payload):
        s3 = Mock()
        stub_raw_object(s3, payload=payload)
        context = make_context(s3)

        with pytest.raises(ValueError, match="contained no parseable rows"):
            wrm_stations_processed_data_latest_asset(context, RAW_KEY)

        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()

    def test_schema_violation_raises_before_any_upload(self):
        # Parses fine, but violates processed_data_schema (bikes must be >= 0).
        payload = make_payload(make_row(bikes="-1"))
        s3 = Mock()
        stub_raw_object(s3, payload=payload)
        context = make_context(s3)

        with pytest.raises(
            ValueError, match="does not match processed_data_schema"
        ):
            wrm_stations_processed_data_latest_asset(context, RAW_KEY)

        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()

    def test_write_failure_is_fail_closed(self):
        s3 = Mock()
        stub_raw_object(s3)
        s3.put_object.side_effect = Exception("S3 write failed")
        context = make_context(s3)

        with pytest.raises(Exception, match="S3 write failed"):
            wrm_stations_processed_data_latest_asset(context, RAW_KEY)

        assert s3.put_object.call_count == 1
        context.add_output_metadata.assert_not_called()


# ---------------------------------------------------------------------------
# Enhanced latest asset
# ---------------------------------------------------------------------------


class TestEnhancedLatestAsset:
    def test_classifies_records_and_writes_stable_enhanced_key(
        self, processed_frame
    ):
        s3 = Mock()
        context = make_context(s3)

        result = wrm_stations_enhanced_data_latest_asset(context, processed_frame)

        roundtrip = written_body(s3)
        assert roundtrip["record_type"].tolist() == [
            "station",
            "bike",
            "unknown",
            "unknown",
            "station",
        ]
        kwargs = written_kwargs(s3)
        assert kwargs["Bucket"] == BUCKET_NAME
        assert kwargs["Key"] == WRM_STATIONS_ENHANCED_LATEST_S3_KEY
        assert kwargs["ContentType"] == "application/octet-stream"
        assert result.metadata["station_records"] == 2
        assert result.metadata["bike_records"] == 1
        assert result.metadata["unknown_records"] == 2

    def test_output_uses_the_exact_enhanced_schema_contract(self, processed_frame):
        s3 = Mock()
        context = make_context(s3)

        result = wrm_stations_enhanced_data_latest_asset(context, processed_frame)
        roundtrip = written_body(s3)

        assert list(roundtrip.columns) == ENHANCED_COLUMNS
        assert list(roundtrip.columns) == list(enhanced_daily_schema.columns)
        enhanced_daily_schema.validate(roundtrip)
        assert roundtrip["date"].nunique() == 1
        assert pd.Timestamp(roundtrip["date"].iloc[0]) == pd.Timestamp("2024-01-15")
        assert result.metadata["columns"] == ENHANCED_COLUMNS

    def test_metadata_reports_counts_date_and_provenance(self, processed_frame):
        s3 = Mock()
        context = make_context(s3)

        result = wrm_stations_enhanced_data_latest_asset(context, processed_frame)
        metadata = result.metadata

        assert metadata["total_records"] == len(SAMPLE_ROWS)
        assert metadata["date"] == "2024-01-15T00:00:00"
        assert metadata["row_s3_source_key"] == RAW_KEY
        assert metadata["s3_key"] == WRM_STATIONS_ENHANCED_LATEST_S3_KEY
        assert metadata["schema_validation"] == "PASSED"
        column_types = metadata["column_types"]
        assert list(column_types) == ENHANCED_COLUMNS
        assert column_types["station_id"] == "str"
        assert column_types["record_type"] == "str"
        assert column_types["processed_at"].startswith("datetime64")

    def test_does_not_mutate_the_processed_input(self, processed_frame):
        before_columns = list(processed_frame.columns)
        s3 = Mock()
        context = make_context(s3)

        wrm_stations_enhanced_data_latest_asset(context, processed_frame)

        assert list(processed_frame.columns) == before_columns
        assert "record_type" not in processed_frame.columns
        assert "date" not in processed_frame.columns
        assert "processed_at" not in processed_frame.columns

    def test_data_date_prefers_dt_partition_over_file_timestamp(self):
        # Midnight boundary: the filename is 2024-01-16, but the dt= segment
        # (the partition the raw object was written under) must win.
        raw_key = (
            f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/"
            "wrm_stations_2024-01-16_00-00-01.txt"
        )
        processed, _ = run_processed(Mock(), raw_key=raw_key)

        s3 = Mock()
        context = make_context(s3)
        result = wrm_stations_enhanced_data_latest_asset(context, processed)

        assert result.metadata["date"] == "2024-01-15T00:00:00"

    def test_data_date_falls_back_to_raw_file_timestamp_without_dt_segment(self):
        raw_key = "somewhere/else/wrm_stations_2024-02-03_04-05-06.txt"
        processed, _ = run_processed(Mock(), raw_key=raw_key)

        s3 = Mock()
        context = make_context(s3)
        result = wrm_stations_enhanced_data_latest_asset(context, processed)

        assert result.metadata["date"] == "2024-02-03T00:00:00"
        assert result.metadata["row_s3_source_key"] == raw_key

    def test_never_lists_or_reads_s3_beyond_the_single_upload(self, processed_frame):
        s3 = Mock()
        context = make_context(s3)

        wrm_stations_enhanced_data_latest_asset(context, processed_frame)

        assert_no_s3_listing(s3)
        s3.get_object.assert_not_called()
        assert s3.put_object.call_count == 1


class TestEnhancedLatestFailClosed:
    def test_empty_processed_input_is_rejected_without_upload(self):
        s3 = Mock()
        context = make_context(s3)

        with pytest.raises(ValueError, match="no rows to enhance"):
            wrm_stations_enhanced_data_latest_asset(context, pd.DataFrame())

        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()

    @pytest.mark.parametrize("bad_input", [None, "not-a-frame", 42])
    def test_non_dataframe_input_is_rejected_by_the_input_type_check(
        self, bad_input
    ):
        """Dagster rejects non-DataFrame inputs before compute runs."""
        s3 = Mock()
        context = make_context(s3)

        with pytest.raises(DagsterTypeCheckDidNotPass):
            wrm_stations_enhanced_data_latest_asset(context, bad_input)

        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()

    def test_schema_violation_raises_before_any_upload(self, processed_frame):
        bad = processed_frame.copy()
        bad["bikes"] = -1  # violates bikes >= 0 in enhanced_daily_schema
        s3 = Mock()
        context = make_context(s3)

        with pytest.raises(
            ValueError, match="does not match enhanced_daily_schema"
        ):
            wrm_stations_enhanced_data_latest_asset(context, bad)

        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()

    def test_missing_required_column_fails_before_any_upload(self, processed_frame):
        bad = processed_frame.drop(columns=["lat"])
        s3 = Mock()
        context = make_context(s3)

        # Current behaviour raises KeyError at the ENHANCED_COLUMNS projection
        # (not a ValueError); the contract that matters here is fail-closed.
        with pytest.raises((KeyError, ValueError)):
            wrm_stations_enhanced_data_latest_asset(context, bad)

        s3.put_object.assert_not_called()
        context.add_output_metadata.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end: one raw key -> processed latest -> enhanced latest
# ---------------------------------------------------------------------------


class TestRawKeyToProcessedToEnhancedFlow:
    def test_flow_writes_both_stable_keys_in_order_without_listing(self):
        s3 = Mock()
        stub_raw_object(s3)

        processed_context = make_context(s3)
        processed = wrm_stations_processed_data_latest_asset(
            processed_context, RAW_KEY
        )

        enhanced_context = make_context(s3)
        enhanced = wrm_stations_enhanced_data_latest_asset(
            enhanced_context, processed
        )

        # The raw key is read exactly once and never listed.
        assert s3.get_object.call_count == 1
        assert_no_s3_listing(s3)

        # Exactly two writes, processed before enhanced, on stable keys.
        keys = [call.kwargs["Key"] for call in s3.put_object.call_args_list]
        assert keys == [
            WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
            WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
        ]

        processed_roundtrip = pd.read_parquet(
            BytesIO(s3.put_object.call_args_list[0].kwargs["Body"])
        )
        enhanced_roundtrip = pd.read_parquet(
            BytesIO(s3.put_object.call_args_list[1].kwargs["Body"])
        )

        processed_data_schema.validate(processed_roundtrip)
        enhanced_daily_schema.validate(enhanced_roundtrip)
        assert enhanced_roundtrip["s3_source_key"].nunique() == 1
        assert enhanced_roundtrip["s3_source_key"].iloc[0] == RAW_KEY
        assert enhanced.metadata["row_s3_source_key"] == RAW_KEY
        assert set(enhanced_roundtrip["record_type"]) <= {
            "station",
            "bike",
            "unknown",
        }

    def test_two_refreshes_reuse_both_keys(self):
        s3 = Mock()
        all_keys: list[str] = []

        for raw_key in (
            f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/"
            "wrm_stations_2024-01-15_10-30-45.txt",
            f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/"
            "wrm_stations_2024-01-15_10-45-45.txt",
        ):
            processed, _ = run_processed(s3, raw_key=raw_key)
            context = make_context(s3)
            wrm_stations_enhanced_data_latest_asset(context, processed)
            all_keys.extend(
                [
                    s3.put_object.call_args_list[-2].kwargs["Key"],
                    s3.put_object.call_args_list[-1].kwargs["Key"],
                ]
            )

        assert all_keys == [
            WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
            WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
            WRM_STATIONS_PROCESSED_LATEST_S3_KEY,
            WRM_STATIONS_ENHANCED_LATEST_S3_KEY,
        ]


# ---------------------------------------------------------------------------
# Registration in assets/assets.py
# ---------------------------------------------------------------------------


class TestLatestAssetRegistration:
    def test_latest_assets_are_exported_unpartitioned_and_wired(self):
        from wrm_pipeline.assets import assets

        processed_name = "wrm_stations_processed_data_latest_asset"
        enhanced_name = "wrm_stations_enhanced_data_latest_asset"

        processed = getattr(assets, processed_name)
        enhanced = getattr(assets, enhanced_name)

        assert processed_name in assets.__all__
        assert enhanced_name in assets.__all__
        assert processed.partitions_def is None
        assert enhanced.partitions_def is None
        assert {key.path[-1] for key in processed.keys} == {
            "wrm_stations_processed_data_latest"
        }
        assert {key.path[-1] for key in enhanced.keys} == {
            "wrm_stations_enhanced_data_latest"
        }

        # raw -> processed latest -> enhanced latest
        assert processed.asset_deps == {
            AssetKey(["wrm_stations_processed_data_latest"]): {
                AssetKey(["wrm_stations_raw_data"])
            }
        }
        assert enhanced.asset_deps == {
            AssetKey(["wrm_stations_enhanced_data_latest"]): {
                AssetKey(["wrm_stations_processed_data_latest"])
            }
        }
