-- Initial setup for Dagster PostgreSQL database
-- This file will be executed when the container starts for the first time

-- Create additional schemas if needed
CREATE SCHEMA IF NOT EXISTS dagster_schema;

-- Grant permissions to the dagster user
GRANT ALL PRIVILEGES ON DATABASE dagster_db TO dagster_user;
GRANT ALL PRIVILEGES ON SCHEMA public TO dagster_user;
GRANT ALL PRIVILEGES ON SCHEMA dagster_schema TO dagster_user;

-- WRM Stations table for processed bike station data
CREATE TABLE IF NOT EXISTS wrm_stations (
    id SERIAL PRIMARY KEY,
    station_id VARCHAR(50) NOT NULL,
    station_name VARCHAR(255),
    latitude DECIMAL(10, 8),
    longitude DECIMAL(11, 8),
    total_docks INTEGER,
    available_bikes INTEGER,
    available_docks INTEGER,
    status VARCHAR(50),
    date DATE NOT NULL,                   -- Date of the actual data
    last_reported TIMESTAMP,              -- When station last reported (from API)
    processed_at TIMESTAMP NOT NULL,      -- When our pipeline processed this
    s3_source_key VARCHAR(500),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(station_id, date, processed_at)  -- Updated unique constraint
);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_wrm_stations_station_id ON wrm_stations(station_id);
CREATE INDEX IF NOT EXISTS idx_wrm_stations_date ON wrm_stations(date);
CREATE INDEX IF NOT EXISTS idx_wrm_stations_processed_at ON wrm_stations(processed_at);
CREATE INDEX IF NOT EXISTS idx_wrm_stations_last_reported ON wrm_stations(last_reported);

-- Grant permissions on the table
GRANT ALL PRIVILEGES ON TABLE wrm_stations TO dagster_user;
GRANT ALL PRIVILEGES ON SEQUENCE wrm_stations_id_seq TO dagster_user;