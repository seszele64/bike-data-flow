import os
from dagster import ConfigurableResource, EnvVar
from dagster_aws.s3.resources import S3Resource
from pydantic import Field
from minio import Minio
from minio.error import S3Error
import boto3

class MinIOResource(ConfigurableResource):
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

class HetznerS3Resource(ConfigurableResource):
    """S3Resource for Hetzner Object Storage"""
    endpoint_url: str = Field(
        default=EnvVar("HETZNER_ENDPOINT_URL"),
        description="Hetzner S3 endpoint URL"
    )
    access_key: str = Field(
        default=EnvVar("HETZNER_ACCESS_KEY_ID"),
        description="Hetzner access key"
    )
    secret_key: str = Field(
        default=EnvVar("HETZNER_SECRET_ACCESS_KEY"),
        description="Hetzner secret key"
    )
    
    def get_client(self):
        """Return boto3 S3 client configured for Hetzner"""
        return boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key
        )

# Create the s3_resource instance
s3_resource = HetznerS3Resource()