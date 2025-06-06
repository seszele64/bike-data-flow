from dagster import Definitions
from .assets.stations import wrm_stations_raw_data_asset, wrm_stations_processed_asset
from .sensors.stations_sensor import s3_raw_stations_sensor
from .resources import s3_resource

print(f"Loading sensors: {s3_raw_stations_sensor.name}")
print(f"Sensor type: {type(s3_raw_stations_sensor)}")

defs = Definitions(
    assets=[wrm_stations_raw_data_asset, wrm_stations_processed_asset],
    sensors=[s3_raw_stations_sensor],
    resources={"s3_resource": s3_resource}
)

__all__ = ["defs"]

