import os
from dagster import ConfigurableResource, EnvVar
from pydantic import Field
from minio import Minio
from minio.error import S3Error

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