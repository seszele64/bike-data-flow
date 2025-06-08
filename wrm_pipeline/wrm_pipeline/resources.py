import os
from dagster import ConfigurableResource, EnvVar
from pydantic import Field
from minio import Minio
from minio.error import S3Error
from contextlib import contextmanager
import psycopg2
from dagster_aws.s3.resources import S3Resource

from .config import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
    S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION_NAME
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
    "region_name": S3_REGION_NAME
}

s3_resource = S3Resource(**s3_resource_config)
