"""Focused unit tests for the shared raw-stations parser.

``wrm_pipeline.assets.stations.transforms`` was extracted from the inline
parsing block that used to live in ``processed_all.py`` so that unpartitioned
assets can reuse it. These tests pin the helper's contract and, more
importantly, pin *behavioural parity* with that legacy inline block by
re-implementing it verbatim (``_parse_like_legacy``) and comparing outcomes.

Design notes
------------
* The helper deliberately leaves ``timestamp`` as float epoch seconds; the
  asset converts it to ``datetime64[ns]`` only after concatenating files. We
  therefore assert the float dtype here and separately assert that the
  asset-style finalisation (datetime conversion + ``s3_source_key`` +
  ``file_timestamp``) satisfies the real ``processed_data_schema`` fixture
  contract, without mocking pandera.
* ``processed_data_schema`` is imported and used for real on purpose: the
  existing asset tests stub it out, so the dtype contract between the helper
  and the model schema is otherwise untested.
"""

from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO
from unittest.mock import Mock

import pandas as pd
import pytest
from dagster import build_asset_context

from wrm_pipeline.assets.stations.processed_all import (
    wrm_stations_processed_data_all_asset,
)
from wrm_pipeline.assets.stations.transforms import (
    _convert_columns,
    _log,
    parse_raw_stations_csv,
)
from wrm_pipeline.config import WRM_STATIONS_S3_PREFIX
from wrm_pipeline.models.stations import processed_data_schema

# ---------------------------------------------------------------------------
# Fixtures / payload builders
# ---------------------------------------------------------------------------

HEADER = (
    "#id,1705147845.123|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,"
    "temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs"
)

# The 2nd field is the pipe-packed timestamp + GMT offsets. Everything after
# it maps 1:1 onto the remaining header names.
RAW_COLUMNS_AFTER_OFFSETS = [
    "name",
    "lat",
    "lon",
    "bikes",
    "spaces",
    "installed",
    "locked",
    "temporary",
    "total_docks",
    "givesbonus_acceptspedelecs_fbbattlevel",
    "pedelecs",
]

EXPECTED_COLUMNS = [
    "station_id",
    "name",
    "timestamp",
    "gmt_local_diff_sec",
    "gmt_servertime_diff_sec",
    "lat",
    "lon",
    "bikes",
    "spaces",
    "installed",
    "locked",
    "temporary",
    "total_docks",
    "givesbonus_acceptspedelecs_fbbattlevel",
    "pedelecs",
]


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


def make_payload(*rows: str, newline: str = "\n") -> str:
    return newline.join([HEADER, *rows])


VALID_SINGLE_ROW = make_row()
VALID_TWO_ROWS = make_payload(
    make_row(station_id="001"),
    make_row(station_id="002", name="Station 2", bikes="0", spaces="12", givesbonus="true"),
)


class FakeLog:
    """Minimal stand-in for a Dagster context logger that records calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def info(self, message: str) -> None:  # noqa: D102 - trivial recorder
        self.calls.append(("info", message))

    def warning(self, message: str) -> None:  # noqa: D102 - trivial recorder
        self.calls.append(("warning", message))

    def error(self, message: str) -> None:  # noqa: D102 - trivial recorder
        self.calls.append(("error", message))

    @property
    def messages(self) -> list[str]:
        return [message for _, message in self.calls]

    def by_level(self, level: str) -> list[str]:
        return [message for lvl, message in self.calls if lvl == level]


# ---------------------------------------------------------------------------
# Legacy reference implementation (verbatim from pre-refactor processed_all.py)
# ---------------------------------------------------------------------------


def _parse_like_legacy(data_text: str):
    """Reproduce the pre-refactor inline parser's observable outcome.

    The old code lived inside a per-file ``try/except Exception: continue`` in
    the asset, so any exception here means "skip this file". We model that by
    returning ``None`` on exception.
    """
    try:
        rows = list(csv.reader(StringIO(data_text)))
        if not rows:
            return None

        header = rows[0]
        new_header = (
            [header[0], "timestamp", "gmt_local_diff_sec", "gmt_servertime_diff_sec"]
            + header[2:]
        )

        data_rows = []
        for row in rows[1:]:
            try:
                timestamp, gmt_local_diff_sec, gmt_servertime_diff_sec = row[1].split("|")
                new_row = (
                    [row[0], timestamp, gmt_local_diff_sec, gmt_servertime_diff_sec]
                    + row[2:]
                )
                data_rows.append(new_row)
            except ValueError:
                continue

        if not data_rows:
            return None

        df = pd.DataFrame(data_rows, columns=new_header)
        df.rename(columns={"#id": "station_id"}, inplace=True)

        df["timestamp"] = df["timestamp"].astype(float)
        df["gmt_local_diff_sec"] = df["gmt_local_diff_sec"].astype(int)
        df["gmt_servertime_diff_sec"] = df["gmt_servertime_diff_sec"].astype(int)
        df["lat"] = df["lat"].astype(float)
        df["lon"] = df["lon"].astype(float)
        df["bikes"] = df["bikes"].astype(int)
        df["spaces"] = df["spaces"].astype(int)
        df["installed"] = df["installed"].map({"true": True, "false": False})
        df["locked"] = df["locked"].map({"true": True, "false": False})
        df["temporary"] = df["temporary"].map({"true": True, "false": False})
        df["total_docks"] = df["total_docks"].astype(int)

        gb = "givesbonus_acceptspedelecs_fbbattlevel"
        df[gb] = df[gb].fillna("false")
        df[gb] = df[gb].map(
            {"true": True, "false": False, "": False, "True": True, "False": False}
        )
        df[gb] = df[gb].fillna(False)
        df["pedelecs"] = df["pedelecs"].astype(int)

        return df[
            ["station_id", "name"]
            + [col for col in df.columns if col not in ["station_id", "name"]]
        ]
    except Exception:
        return None


def _parse_outcome(data_text: str):
    """Outcome of the new helper as the asset sees it (any raise == skip)."""
    try:
        return parse_raw_stations_csv(data_text)
    except Exception:
        return None


PARITY_CORPUS = {
    "valid_single_row": make_payload(VALID_SINGLE_ROW),
    "valid_two_rows": VALID_TWO_ROWS,
    "empty_string": "",
    "bare_newline": "\n",
    "header_only": HEADER,
    "header_only_trailing_newline": HEADER + "\n",
    "single_column_header": "#id",
    "single_column_header_with_rows": "#id\n001",
    "two_column_header": "#id,1705147845.123|3600|-3600\n001,1705147845.123|3600|-3600",
    "row_without_pipes": make_payload("001,1705147845.123,Station 1,51.1,17.0,5,10,true,false,false,15,false,2"),
    "mixed_good_and_pipe_less_row": make_payload(
        make_row(station_id="001"),
        "002,corrupted_row_data",
        make_row(station_id="003"),
    ),
    "too_many_pipe_fields": make_payload(make_row(packed="1|2|3|4")),
    "row_shorter_than_header": make_payload("001,1705147845.123|3600|-3600"),
    "row_longer_than_header": make_payload(VALID_SINGLE_ROW + ",extra"),
    "non_numeric_lat": make_payload(make_row(lat="invalid_lat")),
    "non_numeric_offsets": make_payload(make_row(packed="1705147845.123|not_a_number|-3600")),
    "non_numeric_bikes": make_payload(make_row(bikes="five")),
    "missing_pedelecs_column": (
        HEADER.replace(",pedelecs", "")
        + "\n"
        + VALID_SINGLE_ROW.rsplit(",", 1)[0]
    ),
    # NOTE: ``blank_line_between_rows`` is intentionally excluded here. The
    # legacy parser let ``row[1]`` raise IndexError (which the asset's
    # per-file try/except turned into "skip the file"), whereas the helper now
    # drops the malformed row and keeps the valid ones. That documented,
    # deliberate divergence is pinned by
    # ``TestLegacyParity.test_blank_row_diverges_from_legacy_by_dropping_only_that_row``.
    "crlf_line_endings": make_payload(make_row(station_id="001"), make_row(station_id="002"), newline="\r\n"),
    "quoted_name_with_comma": make_payload(
        make_row(station_id="001", name='"Station, 1"')
    ),
    "bom_prefixed_header": "\ufeff" + HEADER + "\n" + VALID_SINGLE_ROW,
    "capitalised_booleans": make_payload(
        make_row(givesbonus="True", installed="true"),
        make_row(station_id="002", givesbonus="False"),
    ),
    "empty_givesbonus_field": make_payload(make_row(givesbonus="")),
}


# ---------------------------------------------------------------------------
# Valid payload: shape, order, dtypes, values
# ---------------------------------------------------------------------------


class TestValidPayload:
    def test_column_order_matches_processed_contract(self):
        df = parse_raw_stations_csv(make_payload(VALID_SINGLE_ROW))
        assert list(df.columns) == EXPECTED_COLUMNS

    def test_rename_and_name_moved_to_second_position(self):
        df = parse_raw_stations_csv(make_payload(VALID_SINGLE_ROW))
        assert "station_id" in df.columns
        assert "#id" not in df.columns
        assert df.columns[0] == "station_id"
        assert df.columns[1] == "name"

    def test_dtypes_match_helper_contract_before_timestamp_finalisation(self):
        df = parse_raw_stations_csv(make_payload(VALID_SINGLE_ROW))

        # Timestamp stays float epoch seconds; the asset converts it later.
        assert df["timestamp"].dtype == "float64"
        assert df["lat"].dtype == "float64"
        assert df["lon"].dtype == "float64"

        for col in [
            "gmt_local_diff_sec",
            "gmt_servertime_diff_sec",
            "bikes",
            "spaces",
            "total_docks",
            "pedelecs",
        ]:
            assert df[col].dtype == "int64", col

        for col in ["installed", "locked", "temporary", "givesbonus_acceptspedelecs_fbbattlevel"]:
            assert df[col].dtype == "bool", col

        for col in ["station_id", "name"]:
            assert pd.api.types.is_string_dtype(df[col]) or pd.api.types.is_object_dtype(
                df[col]
            ), col

    def test_values_are_preserved_exactly(self):
        df = parse_raw_stations_csv(VALID_TWO_ROWS)

        assert df["station_id"].tolist() == ["001", "002"]
        assert df["name"].tolist() == ["Station 1", "Station 2"]
        assert df["timestamp"].tolist() == [1705147845.123, 1705147845.123]
        assert df["gmt_local_diff_sec"].tolist() == [3600, 3600]
        assert df["gmt_servertime_diff_sec"].tolist() == [-3600, -3600]
        assert df["bikes"].tolist() == [5, 0]
        assert df["spaces"].tolist() == [10, 12]
        assert df["installed"].tolist() == [True, True]
        assert df["locked"].tolist() == [False, False]
        assert df["temporary"].tolist() == [False, False]
        assert df["total_docks"].tolist() == [15, 15]
        assert df["givesbonus_acceptspedelecs_fbbattlevel"].tolist() == [False, True]
        assert df["pedelecs"].tolist() == [2, 2]

    def test_does_not_add_asset_level_columns(self):
        df = parse_raw_stations_csv(make_payload(VALID_SINGLE_ROW))
        assert "s3_source_key" not in df.columns
        assert "file_timestamp" not in df.columns

    def test_asset_style_finalisation_satisfies_processed_schema(self):
        """Helper output + the asset's post-concat steps pass the real schema."""
        df = parse_raw_stations_csv(VALID_TWO_ROWS)

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df["s3_source_key"] = "wrm/stations/raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt"
        df["file_timestamp"] = datetime(2024, 1, 15, 10, 30, 45)

        validated = processed_data_schema.validate(df)

        assert len(validated) == 2
        assert validated["timestamp"].dtype == "datetime64[ns]"
        assert validated["file_timestamp"].dtype == "datetime64[us]"

    def test_negative_counter_is_passed_through_for_schema_to_reject(self):
        """The helper does not validate business rules; pandera does (parity)."""
        df = parse_raw_stations_csv(make_payload(make_row(bikes="-1")))
        assert df["bikes"].tolist() == [-1]

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df["s3_source_key"] = "key"
        df["file_timestamp"] = datetime(2024, 1, 15)
        with pytest.raises(Exception):
            processed_data_schema.validate(df)


# ---------------------------------------------------------------------------
# Malformed / degenerate payloads
# ---------------------------------------------------------------------------


class TestMalformedPayloads:
    def test_empty_string_returns_none_and_warns(self):
        log = FakeLog()
        assert parse_raw_stations_csv("", log=log, source_label="empty.txt") is None
        assert any("Empty data from empty.txt" in m for m in log.by_level("warning"))

    def test_bare_newline_returns_none(self):
        assert parse_raw_stations_csv("\n") is None

    def test_whitespace_only_returns_none(self):
        assert parse_raw_stations_csv("   \n  ") is None

    def test_header_only_returns_none_and_warns_no_valid_rows(self):
        log = FakeLog()
        assert parse_raw_stations_csv(HEADER, log=log, source_label="header.txt") is None
        assert any("No valid data rows from header.txt" in m for m in log.by_level("warning"))

    def test_single_column_header_returns_none_and_warns(self):
        log = FakeLog()
        assert parse_raw_stations_csv("#id", log=log, source_label="short.txt") is None
        assert any("Unusable header from short.txt" in m for m in log.by_level("warning"))

    def test_pipe_less_row_is_dropped_while_valid_rows_survive(self):
        payload = make_payload(
            make_row(station_id="001"),
            "002,corrupted_row_data",
            make_row(station_id="003"),
        )
        log = FakeLog()
        df = parse_raw_stations_csv(payload, log=log)

        assert df["station_id"].tolist() == ["001", "003"]
        assert any("Could not split row" in m for m in log.by_level("warning"))

    def test_row_with_too_many_pipe_fields_is_dropped(self):
        payload = make_payload(make_row(station_id="001"), make_row(station_id="002", packed="1|2|3|4"))
        df = parse_raw_stations_csv(payload)
        assert df["station_id"].tolist() == ["001"]

    def test_all_rows_unparseable_returns_none(self):
        payload = make_payload("001,corrupted_row_data", "002,another_corrupted_row")
        assert parse_raw_stations_csv(payload) is None

    def test_non_numeric_timestamp_offset_returns_none(self):
        payload = make_payload(make_row(packed="1705147845.123|not_a_number|-3600"))
        log = FakeLog()
        assert parse_raw_stations_csv(payload, log=log, source_label="bad-offset.txt") is None
        assert any(
            "Failed to convert data types for bad-offset.txt" in m
            for m in log.by_level("error")
        )

    def test_non_numeric_lat_discards_whole_file(self):
        """A bad numeric cell poisons the column, so no rows survive (parity)."""
        payload = make_payload(make_row(station_id="001"), make_row(station_id="002", lat="invalid_lat"))
        assert parse_raw_stations_csv(payload) is None

    def test_missing_required_column_returns_none(self):
        payload = HEADER.replace(",pedelecs", "") + "\n" + VALID_SINGLE_ROW.rsplit(",", 1)[0]
        assert parse_raw_stations_csv(payload) is None

    def test_row_longer_than_header_returns_none(self):
        assert parse_raw_stations_csv(make_payload(VALID_SINGLE_ROW + ",extra")) is None

    def test_two_column_header_returns_none(self):
        payload = "#id,1705147845.123|3600|-3600\n001,1705147845.123|3600|-3600"
        assert parse_raw_stations_csv(payload) is None

    def test_blank_line_row_is_dropped_and_valid_rows_survive(self):
        """Blank/short rows are skipped like any other unparseable row.

        Regression guard for the old ``row[1]`` IndexError, which escaped the
        helper instead of following the documented "skip and continue" contract.
        """
        payload = make_payload(make_row(station_id="001"), "", make_row(station_id="002"))
        log = FakeLog()
        df = parse_raw_stations_csv(payload, log=log, source_label="blank-row.txt")

        assert df is not None
        assert df["station_id"].tolist() == ["001", "002"]
        assert any("Skipping malformed row" in m for m in log.by_level("warning"))
        assert not log.by_level("error")

    def test_all_rows_blank_or_short_returns_none(self):
        payload = make_payload("", " ", "")
        log = FakeLog()
        assert parse_raw_stations_csv(payload, log=log, source_label="all-blank.txt") is None
        assert any("No valid data rows from all-blank.txt" in m for m in log.by_level("warning"))


# ---------------------------------------------------------------------------
# Boolean / dtype edge cases
# ---------------------------------------------------------------------------


class TestBooleanEdgeCases:
    def test_givesbonus_null_and_empty_map_to_false(self):
        payload = make_payload(make_row(givesbonus=""), make_row(station_id="002"))
        df = parse_raw_stations_csv(payload)
        assert df["givesbonus_acceptspedelecs_fbbattlevel"].tolist() == [False, False]
        assert df["givesbonus_acceptspedelecs_fbbattlevel"].dtype == "bool"

    def test_givesbonus_capitalised_variants_map(self):
        payload = make_payload(
            make_row(givesbonus="True"), make_row(station_id="002", givesbonus="False")
        )
        df = parse_raw_stations_csv(payload)
        assert df["givesbonus_acceptspedelecs_fbbattlevel"].tolist() == [True, False]
        assert df["givesbonus_acceptspedelecs_fbbattlevel"].dtype == "bool"

    def test_givesbonus_unknown_token_becomes_false_but_stays_object(self):
        """Quirk preserved from legacy: unmapped tokens become False (object dtype),
        which then fails the bool schema in the asset. Documented, not fixed."""
        df = parse_raw_stations_csv(make_payload(make_row(givesbonus="yes")))
        assert df["givesbonus_acceptspedelecs_fbbattlevel"].tolist() == [False]
        assert df["givesbonus_acceptspedelecs_fbbattlevel"].dtype == object

    def test_installed_unknown_token_becomes_nan(self):
        """``installed`` is mapped without a fill, so unknown/empty -> NaN (parity)."""
        df = parse_raw_stations_csv(make_payload(make_row(installed="TRUE")))
        assert df["installed"].isna().all()

    def test_standard_lowercase_booleans_map_to_bool_dtype(self):
        df = parse_raw_stations_csv(make_payload(VALID_SINGLE_ROW))
        assert df["installed"].tolist() == [True]
        assert df["locked"].tolist() == [False]
        assert df["temporary"].tolist() == [False]

    def test_convert_columns_is_the_dtype_authority(self):
        """``_convert_columns`` alone performs the rename + dtypes (unit seam)."""
        raw = pd.DataFrame(
            [
                [
                    "001",
                    "1705147845.123",
                    "3600",
                    "-3600",
                    "Station 1",
                    "51.1089",
                    "17.0377",
                    "5",
                    "10",
                    "true",
                    "false",
                    "false",
                    "15",
                    "false",
                    "2",
                ]
            ],
            columns=[
                "#id",
                "timestamp",
                "gmt_local_diff_sec",
                "gmt_servertime_diff_sec",
                *RAW_COLUMNS_AFTER_OFFSETS,
            ],
        )
        converted = _convert_columns(raw)
        assert list(converted.columns) == EXPECTED_COLUMNS
        assert converted["timestamp"].dtype == "float64"
        assert converted["installed"].dtype == "bool"


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------


class TestLogging:
    def test_none_logger_is_a_noop(self):
        _log(None, "warning", "ignored")

    def test_dispatches_to_requested_level_on_delegating_logger(self):
        log = Mock()
        _log(log, "error", "boom")
        log.error.assert_called_once_with("boom")
        log.info.assert_not_called()

    def test_happy_path_emits_info_progress_and_final_columns(self):
        log = FakeLog()
        parse_raw_stations_csv(make_payload(VALID_SINGLE_ROW), log=log, source_label="ok.txt")

        infos = log.by_level("info")
        assert any("Rows in ok.txt: 2" in m for m in infos)
        assert any("Original header: " in m for m in infos)
        assert any("Successfully processed 1 records from ok.txt" in m for m in infos)
        assert any("Final columns: " in m for m in infos)
        assert any("givesbonus_acceptspedelecs_fbbattlevel dtype: bool" in m for m in infos)
        assert not log.by_level("warning")
        assert not log.by_level("error")

    def test_default_source_label_when_omitted(self):
        log = FakeLog()
        parse_raw_stations_csv("", log=log)
        assert any("Empty data from raw data" in m for m in log.messages)


# ---------------------------------------------------------------------------
# Parity with the pre-refactor inline parser
# ---------------------------------------------------------------------------


class TestLegacyParity:
    @pytest.mark.parametrize("case", sorted(PARITY_CORPUS))
    def test_matches_legacy_parser_outcome(self, case):
        payload = PARITY_CORPUS[case]

        legacy = _parse_like_legacy(payload)
        current = _parse_outcome(payload)

        if legacy is None:
            assert current is None, f"{case}: helper produced rows where legacy skipped"
            return

        assert current is not None, f"{case}: helper skipped rows where legacy produced data"
        pd.testing.assert_frame_equal(current, legacy, check_dtype=True)

    def test_parity_corpus_covers_both_outcomes(self):
        """Guard rails: the corpus would be useless if everything were skipped."""
        outcomes = [_parse_like_legacy(payload) for payload in PARITY_CORPUS.values()]
        assert any(o is not None for o in outcomes)
        assert any(o is None for o in outcomes)

    def test_blank_row_diverges_from_legacy_by_dropping_only_that_row(self):
        """Documented divergence: legacy skipped the whole file, helper keeps rows."""
        payload = make_payload(make_row(station_id="001"), "", make_row(station_id="002"))
        assert _parse_like_legacy(payload) is None

        current = _parse_outcome(payload)
        assert current is not None
        assert current["station_id"].tolist() == ["001", "002"]


# ---------------------------------------------------------------------------
# Asset seam: ``df is None -> continue`` (the refactor's riskiest change)
# ---------------------------------------------------------------------------


class TestAssetSeamSkipsNoneResults:
    """``processed_all`` now relies on the helper's ``None`` to skip a file."""

    def _context(self):
        return build_asset_context(
            partition_key="2024-01-15",
            resources={"s3_resource": Mock(name="s3_resource")},
        )

    def _wire(self, context, files):
        """files: list of (key_suffix, payload, last_modified)."""
        contents = [
            {
                "Key": f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/{suffix}",
                "LastModified": last_modified,
            }
            for suffix, _, last_modified in files
        ]
        by_key = {
            f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/{suffix}": payload
            for suffix, payload, _ in files
        }
        context.resources.s3_resource.list_objects_v2.return_value = {"Contents": contents}

        def get_object(Bucket, Key):
            body = Mock()
            body.read.return_value = by_key[Key].encode("utf-8")
            return {"Body": body}

        context.resources.s3_resource.get_object.side_effect = get_object

    def test_unusable_file_is_skipped_and_good_file_still_processed(self):
        context = self._context()
        self._wire(
            context,
            [
                (
                    "wrm_stations_2024-01-15_10-30-45.txt",
                    make_payload(make_row(lat="invalid_lat")),
                    datetime(2024, 1, 15, 10, 30, 45),
                ),
                (
                    "wrm_stations_2024-01-15_11-15-30.txt",
                    VALID_TWO_ROWS,
                    datetime(2024, 1, 15, 11, 15, 30),
                ),
            ],
        )
        context.add_output_metadata = Mock()

        result = wrm_stations_processed_data_all_asset(context)

        assert len(result) == 2
        assert result["station_id"].tolist() == ["001", "002"]

        metadata = context.add_output_metadata.call_args[0][0]
        # Only the usable file is recorded; the skipped one contributes nothing.
        assert metadata["processed_files_count"] == 1
        assert len(metadata["processed_files"]) == 1
        assert metadata["processed_files"][0].endswith("wrm_stations_2024-01-15_11-15-30.txt")
        assert metadata["total_records"] == 2

    def test_all_files_unusable_still_raises_no_valid_data(self):
        context = self._context()
        self._wire(
            context,
            [
                (
                    "wrm_stations_2024-01-15_10-30-45.txt",
                    make_payload(make_row(lat="invalid_lat")),
                    datetime(2024, 1, 15, 10, 30, 45),
                ),
                (
                    "wrm_stations_2024-01-15_11-15-30.txt",
                    make_payload("002,corrupted_row_data"),
                    datetime(2024, 1, 15, 11, 15, 30),
                ),
            ],
        )
        context.add_output_metadata = Mock()

        with pytest.raises(ValueError, match="No valid data found after processing"):
            wrm_stations_processed_data_all_asset(context)

        assert not context.add_output_metadata.called
        assert not context.resources.s3_resource.put_object.called

    def test_blank_line_file_never_yields_partial_rows(self):
        """Invariant that survives a fix for the blank-row contract gap: a payload
        containing an empty line is either skipped wholesale or parsed fully --
        never silently truncated to the rows before the blank line."""
        context = self._context()
        self._wire(
            context,
            [
                (
                    "wrm_stations_2024-01-15_10-30-45.txt",
                    make_payload(make_row(station_id="001"), "", make_row(station_id="002")),
                    datetime(2024, 1, 15, 10, 30, 45),
                )
            ],
        )
        context.add_output_metadata = Mock()

        try:
            result = wrm_stations_processed_data_all_asset(context)
        except ValueError:
            return  # file skipped wholesale -> acceptable (unchanged legacy behaviour)

        assert result["station_id"].tolist() == ["001", "002"]
