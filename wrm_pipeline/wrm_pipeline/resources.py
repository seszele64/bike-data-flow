import os
from dagster import ConfigurableResource, EnvVar, io_manager
from pydantic import Field
from minio import Minio
from minio.error import S3Error
from contextlib import contextmanager
import psycopg2
from dagster_aws.s3.resources import S3Resource
from dagster_iceberg.config import IcebergCatalogConfig
from dagster_iceberg.io_manager.pandas import PandasIcebergIOManager  # <- Changed


from .config import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
    S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
    S3_REGION_NAME, BUCKET_NAME, WRM_STATIONS_S3_PREFIX
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
    host: str
    port: int
    database: str
    user: str
    password: str
    
    @contextmanager
    def get_connection(self):
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
    password=POSTGRES_PASSWORD
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


# --- ICEBERG ---

from pyiceberg.catalog import load_catalog
from dagster import ConfigurableIOManager


# create the Iceberg catalog if it does not exist
catalog = load_catalog(
    "default",
    type="sql",
    uri=f"sqlite:///{os.path.expanduser('~')}/iceberg_catalog.db", 
    warehouse=f"s3://{BUCKET_NAME}/{WRM_STATIONS_S3_PREFIX}iceberg/",
    s3_endpoint=S3_ENDPOINT_URL,
    s3_access_key_id=S3_ACCESS_KEY_ID,
    s3_secret_access_key=S3_SECRET_ACCESS_KEY,
    s3_path_style_access=True,
)

catalog.create_namespace_if_not_exists("default")

# Create the IO manager as a resource
@io_manager
def create_iceberg_io_manager():
    return PandasIcebergIOManager(
        name="default",
        config=IcebergCatalogConfig(
            properties={
                "type": "sql",
                "uri": f"sqlite:///{os.path.expanduser('~')}/iceberg_catalog.db", 
                "warehouse": f"s3://{BUCKET_NAME}/{WRM_STATIONS_S3_PREFIX}iceberg/",
                "s3.endpoint": S3_ENDPOINT_URL,
                "s3.access-key-id": S3_ACCESS_KEY_ID,
                "s3.secret-access-key": S3_SECRET_ACCESS_KEY,
                "s3.path-style-access": "true",
                "write.spark.accept-any-schema": "true",  # Critical Iceberg property
                "commit.retry.num-retries": "5",  # Increased from default [3]
                "commit.retry.min-wait-ms": "1000",
                "commit.retry.max-wait-ms": "5000",
                "write.metadata.delete-after-commit.enabled": "true"  # Cleanup old metadata [3]

            },
        ),
        namespace="default",
        schema_update_mode="merge",
    )

iceberg_io_manager = create_iceberg_io_manager()