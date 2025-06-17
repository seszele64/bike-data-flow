from pydantic import BaseModel, Field, ValidationError, field_validator
from typing import List
from datetime import datetime
import pandera as pa
from pandera import Column, DataFrameSchema, Check

# Define Pandera schema for WRM stations data validation
wrm_stations_schema = DataFrameSchema({
    "station_id": Column(str, nullable=False),
    "timestamp": Column("datetime64[ns]", nullable=False),
    "name": Column(str, nullable=False),
    "lat": Column(float, nullable=True),  # May not be present in all data
    "lon": Column(float, nullable=True),  # May not be present in all data
    "bikes": Column(int, Check.greater_than_or_equal_to(0), nullable=False),
    "spaces": Column(int, Check.greater_than_or_equal_to(0), nullable=False),
    "installed": Column(bool, Check.isin([True, False]), nullable=False),
    "locked": Column(bool, Check.isin([True, False]), nullable=False),
    "temporary": Column(bool, Check.isin([True, False]), nullable=False),
    "total_docks": Column(int, Check.greater_than_or_equal_to(1), nullable=False),
    "givesbonus_acceptspedelecs_fbbattlevel": Column(
        int, 
        Check(lambda x: (x == -1) | (x == 0) | (x >= 0)), 
        nullable=True
    ),
    "pedelecs": Column(int, Check.greater_than_or_equal_to(0), nullable=True),
    "timezone_1": Column(str, nullable=True),
    "timezone_2": Column(str, nullable=True),
    "date": Column("datetime64[ns]", nullable=False),  # Changed from str to datetime64[ns]
    "processed_at": Column("datetime64[ns]", nullable=False),
    "s3_source_key": Column(str, nullable=False)
}, strict=False)  # Allow additional columns


# --- 