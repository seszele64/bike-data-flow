-- Initial setup for Dagster PostgreSQL database
-- This file will be executed when the container starts for the first time

-- Create additional schemas if needed
CREATE SCHEMA IF NOT EXISTS dagster_schema;

-- Grant permissions to the dagster user
GRANT ALL PRIVILEGES ON DATABASE dagster_db TO dagster_user;
GRANT ALL PRIVILEGES ON SCHEMA public TO dagster_user;
GRANT ALL PRIVILEGES ON SCHEMA dagster_schema TO dagster_user;

-- Drop existing table if we need to recreate it
DROP TABLE IF EXISTS wrm_stations;

-- WRM Stations table for processed bike station data
-- Updated to allow NULL values in all columns
CREATE TABLE IF NOT EXISTS wrm_stations (
    db_id SERIAL PRIMARY KEY,                -- Internal DB ID
    station_id VARCHAR(50),                  -- Maps to 'id' in data (allows NULL)
    station_name VARCHAR(255),               -- Maps to 'name' in data (allows NULL)
    timestamp TIMESTAMP,                     -- Original timestamp from data (allows NULL)
    latitude DECIMAL(10, 8),                 -- Maps to 'lat' in data (allows NULL)
    longitude DECIMAL(11, 8),                -- Maps to 'lon' in data (allows NULL)
    total_docks INTEGER,                     -- Direct mapping (allows NULL)
    available_bikes INTEGER,                 -- Maps to 'bikes' in data (allows NULL)
    available_docks INTEGER,                 -- Maps to 'spaces' in data (allows NULL)
    installed BOOLEAN,                       -- From data (allows NULL)
    locked BOOLEAN,                          -- From data (allows NULL)
    temporary BOOLEAN,                       -- From data (allows NULL)
    pedelecs INTEGER,                        -- From data (allows NULL)
    gmt_local_diff_sec INTEGER,              -- From data (allows NULL)
    gmt_servertime_diff_sec INTEGER,         -- From data (allows NULL)
    givesbonus_acceptspedelecs_fbbattlevel VARCHAR(10), -- From data (allows NULL)
    date DATE,                               -- Derived date (allows NULL)
    last_reported TIMESTAMP,                 -- Can be derived from timestamp (allows NULL)
    processed_at TIMESTAMP,                  -- When our pipeline processed this (allows NULL)
    s3_source_key VARCHAR(500),              -- Direct mapping (allows NULL)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    -- Removed unique constraint since all fields can now be null
);

-- Grant permissions on the table
GRANT ALL PRIVILEGES ON TABLE wrm_stations TO dagster_user;
GRANT ALL PRIVILEGES ON SEQUENCE wrm_stations_db_id_seq TO dagster_user;