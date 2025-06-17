from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import List
from datetime import datetime
import pandera as pa
from pandera import Column, DataFrameSchema, Check

# ========================= sChema For prOcesSED DAta ======================== #
# ~~ This schema is used for validating processed data from the WRM API 

processed_data_schema = pa.DataFrameSchema({
    "station_id": pa.Column(str, nullable=False),
    "name": pa.Column(str, nullable=False),
    "timestamp": pa.Column("datetime64[ns]", nullable=False),  # Convert from float to datetime
    "gmt_local_diff_sec": pa.Column(int, nullable=False),
    "gmt_servertime_diff_sec": pa.Column(int, nullable=False),
    "lat": pa.Column(float, nullable=False),
    "lon": pa.Column(float, nullable=False),
    "bikes": pa.Column(int, pa.Check.ge(0), nullable=False),
    "spaces": pa.Column(int, pa.Check.ge(0), nullable=False),
    "installed": pa.Column(bool, pa.Check.isin([True, False]), nullable=False),
    "locked": pa.Column(bool, pa.Check.isin([True, False]), nullable=False),
    "temporary": pa.Column(bool, pa.Check.isin([True, False]), nullable=False),
    "total_docks": pa.Column(int, pa.Check.ge(1), nullable=False),
    "givesbonus_acceptspedelecs_fbbattlevel": pa.Column(
        bool,  # Changed from your original int to match current data
        nullable=False
    ),
    "pedelecs": pa.Column(int, pa.Check.ge(0), nullable=False),
    "s3_source_key": pa.Column(str, nullable=False),
    "file_timestamp": pa.Column("datetime64[us]", nullable=False)
}, strict=False, ordered=True)


# =============== ScheMa FOR EnhaNCED daILY DAta iN .cSV foRmat ============== #
# ~ This schema is used for validating after processing daily data into CSV format. (and before storing in S3 // enhanced)

# =============================== All ENhAncED =============================== #

enhanced_daily_schema = pa.DataFrameSchema({
    "station_id": pa.Column(str, nullable=False),
    "name": pa.Column(str, nullable=False),
    "timestamp": pa.Column("datetime64[ns]", nullable=False),
    "gmt_local_diff_sec": pa.Column(int, nullable=False),
    "gmt_servertime_diff_sec": pa.Column(int, nullable=False),
    "lat": pa.Column(float, nullable=False),
    "lon": pa.Column(float, nullable=False),
    "bikes": pa.Column(int, pa.Check.ge(0), nullable=False),
    "spaces": pa.Column(int, pa.Check.ge(0), nullable=False),
    "installed": pa.Column(bool, nullable=False),
    "locked": pa.Column(bool, nullable=False),
    "temporary": pa.Column(bool, nullable=False),
    "total_docks": pa.Column(int, pa.Check.ge(1), nullable=False),
    "givesbonus_acceptspedelecs_fbbattlevel": pa.Column(bool, nullable=True),
    "pedelecs": pa.Column(int, pa.Check.ge(0), nullable=False),
    "record_type": pa.Column(str, pa.Check.isin(['station', 'bike', 'unknown']), nullable=False),
    "s3_source_key": pa.Column(str, nullable=False),
    "file_timestamp": pa.Column("datetime64[us]", nullable=False),
    "date": pa.Column("datetime64[ns]", nullable=False),
    "processed_at": pa.Column("datetime64[us]", nullable=False)
}, strict=False, ordered=True)

# =============================== station data =============================== #

# idk if this is needed, but keeping it for now

# ================================= bike data ================================ #

