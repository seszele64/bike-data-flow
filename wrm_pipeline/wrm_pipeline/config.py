import os
from dotenv import load_dotenv
import os.path
from typing import Optional
from dataclasses import dataclass

# Load environment variables from .env file (one level up)
dotenv_path = os.path.join(os.path.dirname(__file__), 
                            '..', '..', '.env')
load_dotenv(dotenv_path=dotenv_path)  # .env is in root directory


@dataclass
class VaultConfig:
    """Configuration for Vault integration."""
    vault_addr: str = "https://vault.internal.bike-data-flow.com:8200"
    auth_method: str = "approle"
    role_id: Optional[str] = None
    secret_id: Optional[str] = None
    namespace: Optional[str] = None
    verify: bool = True
    enabled: bool = False  # Set to True to enable Vault integration


# Vault configuration - set VAULT_ENABLED=true to enable
VAULT_CONFIG = VaultConfig(
    vault_addr=os.environ.get("VAULT_ADDR", "https://vault.internal.bike-data-flow.com:8200"),
    auth_method=os.environ.get("VAULT_AUTH_METHOD", "approle"),
    role_id=os.environ.get("VAULT_ROLE_ID"),
    secret_id=os.environ.get("VAULT_SECRET_ID"),
    namespace=os.environ.get("VAULT_NAMESPACE"),
    verify=os.environ.get("VAULT_VERIFY", "true").lower() == "true",
    enabled=os.environ.get("VAULT_ENABLED", "false").lower() == "true",
)


def get_secret_from_vault(path: str) -> Optional[dict]:
    """Get a secret from Vault if Vault is enabled.
    
    Args:
        path: Vault secret path (e.g., 'bike-data-flow/production/database')
        
    Returns:
        Secret data dictionary or None if Vault is not enabled or secret not found.
    """
    if not VAULT_CONFIG.enabled:
        return None
    
    try:
        from wrm_pipeline.wrm_pipeline.vault.client import VaultClient
        from wrm_pipeline.wrm_pipeline.vault.models import VaultConnectionConfig
        
        config = VaultConnectionConfig(
            vault_addr=VAULT_CONFIG.vault_addr,
            auth_method=VAULT_CONFIG.auth_method,
            role_id=VAULT_CONFIG.role_id,
            secret_id=VAULT_CONFIG.secret_id,
            namespace=VAULT_CONFIG.namespace,
            verify=VAULT_CONFIG.verify,
        )
        
        client = VaultClient(config)
        secret = client.get_secret(path)
        client.close()
        
        return secret.data
        
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to retrieve secret from Vault at {path}: {e}")
        return None


def get_database_config() -> dict:
    """Get database configuration, preferring Vault over environment variables.
    
    Returns:
        Dictionary with database configuration keys.
    """
    # Try Vault first if enabled
    vault_secret = get_secret_from_vault("bike-data-flow/production/database")
    if vault_secret:
        return {
            "host": vault_secret.get("host", os.environ.get('POSTGRES_HOST', 'localhost')),
            "port": int(vault_secret.get("port", os.environ.get('POSTGRES_PORT', 5432))),
            "database": vault_secret.get("database", os.environ.get('POSTGRES_DB')),
            "user": vault_secret.get("username", os.environ.get('POSTGRES_USER')),
            "password": vault_secret.get("password", os.environ.get('POSTGRES_PASSWORD')),
        }
    
    # Fall back to environment variables
    return {
        "host": os.environ.get('POSTGRES_HOST', 'localhost'),
        "port": int(os.environ.get('POSTGRES_PORT', 5432)),
        "database": os.environ.get('POSTGRES_DB'),
        "user": os.environ.get('POSTGRES_USER'),
        "password": os.environ.get('POSTGRES_PASSWORD'),
    }


def get_storage_config() -> dict:
    """Get storage configuration, preferring Vault over environment variables.
    
    Returns:
        Dictionary with storage configuration keys.
    """
    # Try Vault first if enabled
    vault_secret = get_secret_from_vault("bike-data-flow/production/storage")
    if vault_secret:
        return {
            "endpoint_url": vault_secret.get("endpoint_url", os.environ.get('S3_ENDPOINT_URL')),
            "access_key_id": vault_secret.get("access_key_id", os.environ.get('S3_ACCESS_KEY_ID')),
            "secret_access_key": vault_secret.get("secret_access_key", os.environ.get('S3_SECRET_ACCESS_KEY')),
            "region_name": vault_secret.get("region_name", os.environ.get('S3_REGION_NAME')),
        }
    
    # Fall back to environment variables
    return {
        "endpoint_url": os.environ.get('S3_ENDPOINT_URL'),
        "access_key_id": os.environ.get('S3_ACCESS_KEY_ID'),
        "secret_access_key": os.environ.get('S3_SECRET_ACCESS_KEY'),
        "region_name": os.environ.get('S3_REGION_NAME'),
    }


def get_api_keys_config() -> dict:
    """Get API keys configuration, preferring Vault over environment variables.
    
    Returns:
        Dictionary with API key configuration.
    """
    # Try Vault first if enabled
    vault_secret = get_secret_from_vault("bike-data-flow/production/api")
    if vault_secret:
        return {
            "wrm_api_url": vault_secret.get("wrm_api_url", "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"),
            "api_key": vault_secret.get("api_key"),
            "auth_token": vault_secret.get("auth_token"),
        }
    
    # Fall back to environment variables
    return {
        "wrm_api_url": "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw",
        "api_key": os.environ.get('API_KEY'),
        "auth_token": os.environ.get('AUTH_TOKEN'),
    }


# s3 - Sensitive values may come from Vault or environment variables
def _get_s3_config() -> tuple:
    """Get S3 configuration from Vault or environment variables."""
    storage_config = get_storage_config()
    return (
        storage_config["endpoint_url"],
        storage_config["access_key_id"],
        storage_config["secret_access_key"],
        storage_config["region_name"],
    )


S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION_NAME = _get_s3_config()

# Hetzner storage
HETZNER_CONFIG = get_storage_config()
HETZNER_ENDPOINT = os.environ.get('HETZNER_ENDPOINT')
HETZNER_ENDPOINT_URL = HETZNER_CONFIG["endpoint_url"]
HETZNER_ACCESS_KEY_ID = HETZNER_CONFIG["access_key_id"]
HETZNER_SECRET_ACCESS_KEY = HETZNER_CONFIG["secret_access_key"]

# PostgreSQL Configuration - use Vault if available
DB_CONFIG = get_database_config()
POSTGRES_HOST = DB_CONFIG["host"]
POSTGRES_PORT = DB_CONFIG["port"]
POSTGRES_DB = DB_CONFIG["database"]
POSTGRES_USER = DB_CONFIG["user"]
POSTGRES_PASSWORD = DB_CONFIG["password"]

# --- URLs for data sources ---
WRM_FAILURES_DATA_URL = "https://www.wroclaw.pl/open-data/39605eb0-8055-4733-bb02-04b96791d36a/WRM_usterki.csv"
WRM_STATIONS_DATA_URL = "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"

# --- File naming ---
WRM_FAILURES_BASE_FILENAME = "wrm_failures"
WRM_STATION_BASE_FILENAME = "station_data"
CSV_EXTENSION = "csv"

# --- S3/MinIO Configuration ---
BUCKET_NAME = os.environ.get('BUCKET_NAME')

# Storage paths
WRM_FAILURES_TARGET_FOLDER = 'bike-data/failures/'
if not WRM_FAILURES_TARGET_FOLDER.endswith('/'):
    WRM_FAILURES_TARGET_FOLDER += '/'

# S3 storage paths
WRM_STATIONS_S3_PREFIX = "bike-data/gen_info/"

# Path to .env file for configuration
ENV_PATH = dotenv_path

# ================================== duckdb ================================== #

db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'db', 'analytics.duckdb')