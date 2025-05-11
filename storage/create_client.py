import os
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

def get_minio_client(dotenv_path=None):
    """
    Creates and returns a MinIO client using environment variables.
    
    Args:
        dotenv_path (str, optional): Path to the .env file. If None, loads from default location.
    
    Returns:
        Minio: Configured MinIO client or None if configuration fails
    """
    # Load environment variables
    if dotenv_path:
        load_dotenv(dotenv_path=dotenv_path)
    else:
        load_dotenv()
        
    # Get configuration from environment
    raw_url = os.environ.get('HETZNER_ENDPOINT_URL')
    access_key = os.environ.get('HETZNER_ACCESS_KEY_ID')
    secret_key = os.environ.get('HETZNER_SECRET_ACCESS_KEY')
    
    if not raw_url:
        print("Error: HETZNER_ENDPOINT_URL is not set in environment variables.")
        return None
        
    # Determine endpoint and security settings from HETZNER_ENDPOINT_URL
    if raw_url.lower().startswith('https://'):
        endpoint = raw_url[len('https://'):]
        use_secure = True
    elif raw_url.lower().startswith('http://'):
        endpoint = raw_url[len('http://'):]
        use_secure = False
    else:
        # No scheme provided, or an unknown scheme.
        # Assume the raw_url is the endpoint itself.
        # Default to a secure connection.
        endpoint = raw_url
        use_secure = True
    
    # Remove any trailing slashes from the endpoint, as Minio expects host or host:port
    endpoint = endpoint.rstrip('/')

    print(f"Initializing MinIO client for endpoint: '{endpoint}', Secure: {use_secure}")

    try:
        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=use_secure
        )
        return client
    except Exception as e:
        print(f"Error creating MinIO client: {e}")
        return None


# Example usage when script is run directly
if __name__ == "__main__":
    client = get_minio_client()
    if client:
        print("MinIO client created successfully")
        # Test connection by listing buckets
        try:
            buckets = client.list_buckets()
            print("Available buckets:")
            for bucket in buckets:
                print(f" - {bucket.name}")
        except S3Error as e:
            print(f"Error listing buckets: {e}")
