import os
from dagster import ConfigurableResource, EnvVar, io_manager
from pydantic import Field
from minio import Minio
from minio.error import S3Error
from contextlib import contextmanager
import psycopg2
from dagster_aws.s3.resources import S3Resource
from typing import Optional


from .config import (
    get_database_config, get_storage_config,
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
    S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
    S3_REGION_NAME, BUCKET_NAME, WRM_STATIONS_S3_PREFIX,
    HETZNER_ENDPOINT_URL, HETZNER_ACCESS_KEY_ID, HETZNER_SECRET_ACCESS_KEY
)

class MinIOResource(ConfigurableResource):
    """MinIO resource for MinIO-specific functionality"""
    endpoint_url: str = Field(
        default=EnvVar("MINIO_ENDPOINT_URL"),
        description="MinIO endpoint URL (e.g., 'localhost:9000')."
    )
    access_key: str = Field(
        default=EnvVar("MINIO_ACCESS_KEY"),
        description="MinIO access key."
    )
    secret_key: str = Field(
        default=EnvVar("MINIO_SECRET_KEY"),
        description="MinIO secret key."
    )
    secure: bool = Field(
        default=False,
        description="Default to False for local MinIO, set to True for HTTPS."
    )

    _client: Minio = None  # type: ignore

    def get_client(self) -> Minio:
        if self._client is None:
            self._client = Minio(
                self.endpoint_url,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure
            )
        return self._client

    def ensure_bucket_exists(self, bucket_name: str, logger):
        client = self.get_client()
        try:
            if not client.bucket_exists(bucket_name):
                client.make_bucket(bucket_name)
                logger.info(f"Bucket '{bucket_name}' created.")
            else:
                logger.info(f"Bucket '{bucket_name}' already exists.")
        except S3Error as e:
            logger.error(f"Error checking or creating bucket '{bucket_name}': {e}")
            raise

class PostgreSQLResource(ConfigurableResource):
    """PostgreSQL resource that can use Vault for credentials."""
    host: str = Field(default=EnvVar("POSTGRES_HOST"))
    port: int = Field(default=EnvVar("POSTGRES_PORT"))
    database: str = Field(default=EnvVar("POSTGRES_DB"))
    user: str = Field(default=EnvVar("POSTGRES_USER"))
    password: str = Field(default=EnvVar("POSTGRES_PASSWORD"))
    use_vault: bool = Field(default=False, description="Use Vault for credentials")
    vault_path: str = Field(default="bike-data-flow/production/database")
    
    @contextmanager
    def get_connection(self):
        """Get a database connection."""
        # If Vault is enabled, fetch credentials from Vault
        if self.use_vault:
            try:
                from .vault.client import VaultClient
                from .vault.models import VaultConnectionConfig
                
                config = VaultConnectionConfig(
                    vault_addr=os.environ.get("VAULT_ADDR", "https://vault.internal.bike-data-flow.com:8200"),
                    auth_method="approle",
                    role_id=os.environ.get("VAULT_ROLE_ID"),
                    secret_id=os.environ.get("VAULT_SECRET_ID"),
                )
                
                client = VaultClient(config)
                secret = client.get_secret(self.vault_path)
                client.close()
                
                conn = psycopg2.connect(
                    host=secret.data.get("host", self.host),
                    port=secret.data.get("port", self.port),
                    database=secret.data.get("database", self.database),
                    user=secret.data.get("username", self.user),
                    password=secret.data.get("password", self.password)
                )
            except Exception as e:
                # Fall back to configured values
                conn = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password
                )
        else:
            conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password
            )
        try:
            yield conn
        finally:
            conn.close()


postgres_resource = PostgreSQLResource(
    host=POSTGRES_HOST,
    port=POSTGRES_PORT,
    database=POSTGRES_DB,
    user=POSTGRES_USER,
    password=POSTGRES_PASSWORD,
    use_vault=False,  # Set to True and configure vault_path to use Vault
)

# Determine if a region name is configured and should be passed
s3_resource_config = {
    "endpoint_url": S3_ENDPOINT_URL,
    "aws_access_key_id": S3_ACCESS_KEY_ID,
    "aws_secret_access_key": S3_SECRET_ACCESS_KEY,
    "region_name": S3_REGION_NAME,
    "s3_path_style_access": True,  # Use path-style access for MinIO compatibility
    "bucket_name": BUCKET_NAME,
}

s3_resource = S3Resource(**s3_resource_config)


# --- DuckDB and S3 IO Managers ---

import os
from dagster import Definitions, EnvVar
from dagster_duckdb_pandas import DuckDBPandasIOManager
from dagster_aws.s3.io_manager import s3_pickle_io_manager

# Ensure the data directory exists
data_dir = os.path.join(os.path.expanduser("~"), "data")
os.makedirs(data_dir, exist_ok=True)

# DuckDB I/O Manager with S3 integration
duckdb_io_manager = DuckDBPandasIOManager(
    database=os.path.join(data_dir, "analytics.duckdb"),
    schema="wrm_analytics"
)

# DuckDB I/O Manager with S3 secret for cloud storage integration
duckdb_s3_io_manager = DuckDBPandasIOManager(
    database=f"s3://{BUCKET_NAME}/{WRM_STATIONS_S3_PREFIX}duckdb/analytics.duckdb",
    connection_config={
        "s3_region": "auto",
        "s3_access_key_id": HETZNER_ACCESS_KEY_ID,
        "s3_secret_access_key": HETZNER_SECRET_ACCESS_KEY,
        "s3_endpoint": HETZNER_ENDPOINT_URL,
        "s3_use_ssl": "true",
        "s3_url_style": "path"
    }
)

# Local DuckDB with S3 extension for hybrid operations
duckdb_hybrid_io_manager = DuckDBPandasIOManager(
    database=os.path.join(data_dir, "analytics.duckdb"),
    schema="wrm_analytics",
    connection_config={
        "s3_region": "auto",
        "s3_access_key_id": HETZNER_ACCESS_KEY_ID,
        "s3_secret_access_key": HETZNER_SECRET_ACCESS_KEY,
        "s3_endpoint": HETZNER_ENDPOINT_URL,
        "s3_use_ssl": "true",
        "s3_url_style": "path"
    }
)

# S3 I/O Manager for raw data storage with proper prefix
s3_io_manager = s3_pickle_io_manager.configured({
    "s3_bucket": BUCKET_NAME,
    "s3_prefix": f"{WRM_STATIONS_S3_PREFIX}",  # Use your WRM prefix: "bike-data/gen_info/"
})

# --- Hive Partitioned S3 IO Manager ---

from dagster import ConfigurableIOManager, InputContext, OutputContext
import pandas as pd
import pickle
import io
from datetime import datetime

class HivePartitionedS3IOManager(ConfigurableIOManager):
    """Custom S3 I/O Manager that supports Hive-style date partitioning."""
    
    s3_resource: S3Resource = Field(description="S3 resource for cloud storage operations")
    
    def _get_partition_path(self, context: OutputContext) -> str:
        """Generate Hive-style partition path with current date."""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
        
        # Use the asset name to determine the filename
        asset_name = context.asset_key.path[-1]
        if asset_name == "raw_stations_data":
            filename = f"wrm_stations_{timestamp}.txt"  # Changed to .txt for raw text data
            return f"{WRM_STATIONS_S3_PREFIX}raw/dt={date_str}/{filename}"
        elif "processed" in asset_name or "transformed" in asset_name:
            filename = f"{asset_name}_{timestamp}.parquet"
            return f"{WRM_STATIONS_S3_PREFIX}raw/dt={date_str}/{filename}"
        else:
            filename = f"{asset_name}_{timestamp}.csv"  # Default to CSV
            return f"{WRM_STATIONS_S3_PREFIX}raw/dt={date_str}/{filename}"
    
    def handle_output(self, context: OutputContext, obj: pd.DataFrame):
        s3_path = self._get_partition_path(context)
        
        # Determine format based on file extension
        if s3_path.endswith('.json'):
            # For raw JSON data, store as JSON Lines
            buffer = io.BytesIO()
            obj.to_json(buffer, orient='records', lines=True)
            buffer.seek(0)
        elif s3_path.endswith('.txt'):
            buffer = io.BytesIO()
            # For raw text data, store as-is without any processing
            if isinstance(obj, str):
                raw_text = obj
            else:
                raw_text = str(obj)
            buffer.write(raw_text.encode('utf-8'))
            buffer.seek(0)
        elif s3_path.endswith('.parquet'):
            # For processed data, store as Parquet
            buffer = io.BytesIO()
            obj.to_parquet(buffer, index=False, engine='pyarrow')
            buffer.seek(0)
        elif s3_path.endswith('.csv'):
            # For tabular data, store as CSV
            buffer = io.BytesIO()
            obj.to_csv(buffer, index=False)
            buffer.seek(0)
        else:
            # Default to CSV for other cases
            buffer = io.BytesIO()
            obj.to_csv(buffer, index=False)
            buffer.seek(0)
        
        # Upload to S3 using the S3Resource
        if buffer:
            self.s3_resource.get_client().put_object(
                Bucket=BUCKET_NAME,
                Key=s3_path,
                Body=buffer.getvalue()
            )
        
        full_s3_uri = f"s3://{BUCKET_NAME}/{s3_path}"
        context.log.info(f"Stored asset at {full_s3_uri}")
        
        # Add metadata about the actual storage location
        context.add_output_metadata({
            "s3_uri": full_s3_uri,
            "s3_path": s3_path,
            "partition_date": datetime.now().strftime("%Y-%m-%d"),
            "ingestion_timestamp": datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
            "format": s3_path.split('.')[-1],
            "rows": len(obj)
        })
    
    def load_input(self, context: InputContext) -> pd.DataFrame:
        # For loading, you'd need to implement logic to find the most recent partition
        # This is a simplified version - you could extend this to:
        # 1. List objects in the partition directory
        # 2. Find the most recent file based on timestamp
        # 3. Load based on file format
        
        s3_client = self.s3_resource.get_client()
        
        # Get the asset name to construct search prefix
        asset_name = context.upstream_output.asset_key.path[-1]
        
        # Search for the most recent file
        prefix = f"{WRM_STATIONS_S3_PREFIX}raw/"
        
        try:
            # List objects with the prefix
            response = s3_client.list_objects_v2(
                Bucket=BUCKET_NAME,
                Prefix=prefix
            )
            
            if 'Contents' not in response:
                raise FileNotFoundError(f"No files found with prefix {prefix}")
            
            # Find the most recent file for this asset
            asset_files = [
                obj for obj in response['Contents'] 
                if asset_name in obj['Key']
            ]
            
            if not asset_files:
                raise FileNotFoundError(f"No files found for asset {asset_name}")
            
            # Sort by last modified and get the most recent
            most_recent = sorted(asset_files, key=lambda x: x['LastModified'])[-1]
            s3_path = most_recent['Key']
            
            # Download and load based on file format
            obj = s3_client.get_object(Bucket=BUCKET_NAME, Key=s3_path)
            
            if s3_path.endswith('.json'):
                return pd.read_json(io.BytesIO(obj['Body'].read()), lines=True)
            elif s3_path.endswith('.txt'):
                # Load raw text file and convert to DataFrame
                text_content = obj['Body'].read().decode('utf-8')
                lines = text_content.strip().split('\n')
                return pd.DataFrame({'text': lines})
            elif s3_path.endswith('.parquet'):
                return pd.read_parquet(io.BytesIO(obj['Body'].read()))
            elif s3_path.endswith('.csv'):
                return pd.read_csv(io.BytesIO(obj['Body'].read()))
            else:
                # Default to CSV
                return pd.read_csv(io.BytesIO(obj['Body'].read()))
                
        except Exception as e:
            context.log.error(f"Error loading from S3: {e}")
            raise

# Create the custom partitioned S3 I/O manager with the S3Resource
hive_partitioned_s3_io_manager = HivePartitionedS3IOManager(s3_resource=s3_resource)