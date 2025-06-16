from dagster import asset, AssetExecutionContext, DailyPartitionsDefinition
from dagster_aws.s3.resources import S3Resource
import duckdb
import pandas as pd
from datetime import datetime
from io import BytesIO
import tempfile
import os
from typing import Dict, Any

from ..config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX

# Define daily partitions to match other assets
daily_partitions = DailyPartitionsDefinition(
    start_date="2025-05-01"
)

@asset(
    name="wrm_stations_deduplicated",
    deps=["wrm_stations_data"],
    partitions_def=daily_partitions,
    compute_kind="duckdb",
    group_name="wrm_data_deduplication",
    required_resource_keys={"s3_resource"}
)
def wrm_stations_deduplicated_asset(context: AssetExecutionContext) -> str:
    """
    Deduplicate station data using DuckDB to remove duplicate records
    based on station_id and timestamp, keeping the most recent processed_at record.
    """
    
    # Get S3 client and partition information
    s3_client = context.resources.s3_resource
    partition_date = context.partition_key
    
    try:
        # Find stations data for this partition
        stations_s3_prefix = f"{WRM_STATIONS_S3_PREFIX}processed/stations/dt={partition_date}/"
        
        # List objects in the stations partition
        response = s3_client.list_objects_v2(
            Bucket=BUCKET_NAME,
            Prefix=stations_s3_prefix
        )
        
        if 'Contents' not in response or not response['Contents']:
            context.log.info(f"No stations data found for partition {partition_date}")
            context.add_output_metadata({
                "partition_date": partition_date,
                "status": "no_data",
                "input_records": 0,
                "deduplicated_records": 0
            })
            return ""
        
        # Get all station files for this partition (in case there are multiple)
        station_files = [obj['Key'] for obj in response['Contents'] if obj['Key'].endswith('.parquet')]
        
        if not station_files:
            context.log.info(f"No parquet files found for partition {partition_date}")
            return ""
        
        context.log.info(f"Found {len(station_files)} station files for partition {partition_date}")
        
        # Initialize DuckDB connection
        conn = duckdb.connect()
        
        # Create temporary directory for downloaded files
        with tempfile.TemporaryDirectory() as temp_dir:
            local_files = []
            
            # Download all station files for this partition
            for i, s3_key in enumerate(station_files):
                local_file = os.path.join(temp_dir, f"stations_{i}.parquet")
                
                # Download file from S3
                response = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_key)
                with open(local_file, 'wb') as f:
                    f.write(response['Body'].read())
                
                local_files.append(local_file)
                context.log.info(f"Downloaded {s3_key} to {local_file}")
            
            # Create a view that combines all files
            if len(local_files) == 1:
                # Single file case
                conn.execute(f"CREATE VIEW stations_raw AS SELECT * FROM read_parquet('{local_files[0]}')")
            else:
                # Multiple files case - union all
                file_selects = [f"SELECT * FROM read_parquet('{f}')" for f in local_files]
                union_query = " UNION ALL ".join(file_selects)
                conn.execute(f"CREATE VIEW stations_raw AS {union_query}")
            
            # Get initial record count
            initial_count_result = conn.execute("SELECT COUNT(*) FROM stations_raw").fetchone()
            initial_count = initial_count_result[0] if initial_count_result else 0
            
            context.log.info(f"Total input records: {initial_count}")
            
            if initial_count == 0:
                context.log.info("No records found in input data")
                return ""
            
            # Perform deduplication using DuckDB SQL
            # Keep the record with the latest processed_at timestamp for each station_id + timestamp combination
            deduplication_query = """
            CREATE VIEW stations_deduplicated AS
            SELECT *
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY station_id, timestamp 
                           ORDER BY processed_at DESC, s3_source_key DESC
                       ) as rn
                FROM stations_raw
            ) ranked
            WHERE rn = 1
            """
            
            conn.execute(deduplication_query)
            
            # Get deduplicated count
            dedup_count_result = conn.execute("SELECT COUNT(*) FROM stations_deduplicated").fetchone()
            dedup_count = dedup_count_result[0] if dedup_count_result else 0
            
            # Calculate deduplication statistics
            duplicates_removed = initial_count - dedup_count
            dedup_percentage = (duplicates_removed / initial_count * 100) if initial_count > 0 else 0
            
            context.log.info(f"Deduplication completed: {initial_count} -> {dedup_count} records")
            context.log.info(f"Removed {duplicates_removed} duplicates ({dedup_percentage:.2f}%)")
            
            # Get some statistics about the deduplicated data
            stats_query = """
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT station_id) as unique_stations,
                MIN(timestamp) as earliest_timestamp,
                MAX(timestamp) as latest_timestamp,
                AVG(bikes) as avg_bikes,
                AVG(spaces) as avg_spaces,
                AVG(total_docks) as avg_total_docks
            FROM stations_deduplicated
            """
            
            stats_result = conn.execute(stats_query).fetchone()
            
            # Get top stations by record count
            top_stations_query = """
            SELECT 
                station_id,
                name,
                COUNT(*) as record_count,
                AVG(bikes) as avg_bikes,
                AVG(spaces) as avg_spaces
            FROM stations_deduplicated
            GROUP BY station_id, name
            ORDER BY record_count DESC
            LIMIT 5
            """
            
            top_stations_result = conn.execute(top_stations_query).fetchall()
            
            # Export deduplicated data to pandas DataFrame
            df = conn.execute("SELECT * FROM stations_deduplicated ORDER BY station_id, timestamp").df()
            
            # Remove the ranking column
            if 'rn' in df.columns:
                df = df.drop(columns=['rn'])
        
        # Close DuckDB connection
        conn.close()
        
        if df.empty:
            context.log.warning(f"Deduplication resulted in empty dataset for partition {partition_date}")
            return ""
        
        # Generate S3 key for deduplicated data
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dedup_s3_key = f"{WRM_STATIONS_S3_PREFIX}processed/deduplicated/dt={partition_date}/deduplicated_{timestamp}.parquet"
        
        # Upload deduplicated data to S3
        buffer = BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)
        
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=dedup_s3_key,
            Body=buffer.getvalue(),
            ContentType='application/octet-stream'
        )
        
        context.log.info(f"Deduplicated data saved to S3: {dedup_s3_key}")
        
        # Prepare metadata
        metadata = {
            "partition_date": partition_date,
            "input_records": initial_count,
            "deduplicated_records": dedup_count,
            "duplicates_removed": duplicates_removed,
            "deduplication_percentage": round(dedup_percentage, 2),
            "unique_stations": int(stats_result[1]) if stats_result else 0,
            "earliest_timestamp": str(stats_result[2]) if stats_result and stats_result[2] else None,
            "latest_timestamp": str(stats_result[3]) if stats_result and stats_result[3] else None,
            "avg_bikes": round(float(stats_result[4]), 2) if stats_result and stats_result[4] else 0,
            "avg_spaces": round(float(stats_result[5]), 2) if stats_result and stats_result[5] else 0,
            "avg_total_docks": round(float(stats_result[6]), 2) if stats_result and stats_result[6] else 0,
            "s3_key": dedup_s3_key,
            "input_files": len(station_files),
            "top_stations": [
                {
                    "station_id": row[0],
                    "name": row[1],
                    "record_count": int(row[2]),
                    "avg_bikes": round(float(row[3]), 2) if row[3] else 0,
                    "avg_spaces": round(float(row[4]), 2) if row[4] else 0
                }
                for row in top_stations_result
            ]
        }
        
        # Add output metadata
        context.add_output_metadata(metadata)
        
        return dedup_s3_key
        
    except Exception as e:
        context.log.error(f"Failed to deduplicate stations data for partition {partition_date}: {e}")
        raise

@asset(
    name="deduplication_quality_report",
    deps=[wrm_stations_deduplicated_asset],
    partitions_def=daily_partitions,
    compute_kind="duckdb",
    group_name="wrm_data_quality",
    required_resource_keys={"s3_resource"}
)
def deduplication_quality_report_asset(context: AssetExecutionContext, wrm_stations_deduplicated: str) -> Dict[str, Any]:
    """
    Generate a detailed quality report for the deduplication process,
    analyzing patterns in the deduplicated data.
    """
    
    if not wrm_stations_deduplicated:
        context.log.info("No deduplicated data available for quality report")
        return {"status": "no_data", "partition_date": context.partition_key}
    
    # Get S3 client and partition information
    s3_client = context.resources.s3_resource
    partition_date = context.partition_key
    
    try:
        # Download deduplicated data from S3
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=wrm_stations_deduplicated)
        df = pd.read_parquet(BytesIO(response['Body'].read()))
        
        # Initialize DuckDB and load data
        conn = duckdb.connect()
        conn.register('stations', df)
        
        # Quality checks using DuckDB
        quality_checks = {}
        
        # 1. Data completeness check
        completeness_query = """
        SELECT 
            COUNT(*) as total_records,
            COUNT(CASE WHEN station_id IS NOT NULL THEN 1 END) as station_id_complete,
            COUNT(CASE WHEN name IS NOT NULL THEN 1 END) as name_complete,
            COUNT(CASE WHEN timestamp IS NOT NULL THEN 1 END) as timestamp_complete,
            COUNT(CASE WHEN bikes >= 0 THEN 1 END) as bikes_valid,
            COUNT(CASE WHEN spaces >= 0 THEN 1 END) as spaces_valid,
            COUNT(CASE WHEN total_docks > 0 THEN 1 END) as total_docks_valid
        FROM stations
        """
        
        completeness_result = conn.execute(completeness_query).fetchone()
        total_records = completeness_result[0]
        
        quality_checks['completeness'] = {
            "total_records": total_records,
            "station_id_completeness": round((completeness_result[1] / total_records * 100), 2) if total_records > 0 else 0,
            "name_completeness": round((completeness_result[2] / total_records * 100), 2) if total_records > 0 else 0,
            "timestamp_completeness": round((completeness_result[3] / total_records * 100), 2) if total_records > 0 else 0,
            "bikes_validity": round((completeness_result[4] / total_records * 100), 2) if total_records > 0 else 0,
            "spaces_validity": round((completeness_result[5] / total_records * 100), 2) if total_records > 0 else 0,
            "total_docks_validity": round((completeness_result[6] / total_records * 100), 2) if total_records > 0 else 0
        }
        
        # 2. Temporal distribution check
        temporal_query = """
        SELECT 
            DATE_TRUNC('hour', timestamp) as hour,
            COUNT(*) as record_count
        FROM stations
        GROUP BY DATE_TRUNC('hour', timestamp)
        ORDER BY hour
        """
        
        temporal_result = conn.execute(temporal_query).fetchall()
        quality_checks['temporal_distribution'] = [
            {"hour": str(row[0]), "record_count": row[1]}
            for row in temporal_result
        ]
        
        # 3. Station capacity consistency check
        capacity_query = """
        SELECT 
            station_id,
            COUNT(DISTINCT total_docks) as distinct_capacities,
            MIN(total_docks) as min_capacity,
            MAX(total_docks) as max_capacity,
            COUNT(CASE WHEN bikes + spaces != total_docks THEN 1 END) as capacity_mismatches
        FROM stations
        GROUP BY station_id
        HAVING COUNT(DISTINCT total_docks) > 1 OR COUNT(CASE WHEN bikes + spaces != total_docks THEN 1 END) > 0
        ORDER BY distinct_capacities DESC, capacity_mismatches DESC
        LIMIT 10
        """
        
        capacity_result = conn.execute(capacity_query).fetchall()
        quality_checks['capacity_inconsistencies'] = [
            {
                "station_id": row[0],
                "distinct_capacities": row[1],
                "min_capacity": row[2],
                "max_capacity": row[3],
                "capacity_mismatches": row[4]
            }
            for row in capacity_result
        ]
        
        # 4. Outlier detection
        outlier_query = """
        SELECT 
            station_id,
            name,
            bikes,
            spaces,
            total_docks,
            timestamp
        FROM stations
        WHERE bikes > 100 OR spaces > 100 OR total_docks > 100
        ORDER BY bikes DESC, spaces DESC
        LIMIT 10
        """
        
        outlier_result = conn.execute(outlier_query).fetchall()
        quality_checks['potential_outliers'] = [
            {
                "station_id": row[0],
                "name": row[1],
                "bikes": row[2],
                "spaces": row[3],
                "total_docks": row[4],
                "timestamp": str(row[5])
            }
            for row in outlier_result
        ]
        
        # 5. Station name consistency
        name_consistency_query = """
        SELECT 
            station_id,
            COUNT(DISTINCT name) as distinct_names,
            array_agg(DISTINCT name) as names
        FROM stations
        GROUP BY station_id
        HAVING COUNT(DISTINCT name) > 1
        ORDER BY distinct_names DESC
        LIMIT 10
        """
        
        name_result = conn.execute(name_consistency_query).fetchall()
        quality_checks['name_inconsistencies'] = [
            {
                "station_id": row[0],
                "distinct_names": row[1],
                "names": row[2]
            }
            for row in name_result
        ]
        
        conn.close()
        
        # Generate overall quality score
        completeness_score = quality_checks['completeness']
        overall_completeness = (
            completeness_score['station_id_completeness'] +
            completeness_score['timestamp_completeness'] +
            completeness_score['bikes_validity'] +
            completeness_score['spaces_validity']
        ) / 4
        
        consistency_penalties = len(quality_checks['capacity_inconsistencies']) + len(quality_checks['name_inconsistencies'])
        consistency_score = max(0, 100 - (consistency_penalties * 2))  # Penalty of 2 points per inconsistency
        
        overall_quality_score = round((overall_completeness + consistency_score) / 2, 2)
        
        quality_report = {
            "partition_date": partition_date,
            "overall_quality_score": overall_quality_score,
            "quality_checks": quality_checks,
            "summary": {
                "total_records": total_records,
                "quality_issues": len(quality_checks['capacity_inconsistencies']) + len(quality_checks['name_inconsistencies']),
                "potential_outliers": len(quality_checks['potential_outliers']),
                "temporal_coverage_hours": len(quality_checks['temporal_distribution'])
            },
            "recommendations": []
        }
        
        # Add recommendations based on findings
        if overall_quality_score < 90:
            quality_report['recommendations'].append("Data quality is below 90%, investigate data sources")
        
        if len(quality_checks['capacity_inconsistencies']) > 0:
            quality_report['recommendations'].append("Station capacity inconsistencies detected, review station configuration")
        
        if len(quality_checks['potential_outliers']) > 0:
            quality_report['recommendations'].append("Potential outliers detected, validate unusual capacity values")
        
        if completeness_score['station_id_completeness'] < 100:
            quality_report['recommendations'].append("Missing station IDs detected, check data extraction process")
        
        context.log.info(f"Quality report generated for partition {partition_date}: Score {overall_quality_score}/100")
        
        # Add output metadata
        context.add_output_metadata({
            "partition_date": partition_date,
            "overall_quality_score": overall_quality_score,
            "total_records": total_records,
            "quality_issues": quality_report['summary']['quality_issues'],
            "recommendations_count": len(quality_report['recommendations'])
        })
        
        return quality_report
        
    except Exception as e:
        context.log.error(f"Failed to generate quality report for partition {partition_date}: {e}")
        raise