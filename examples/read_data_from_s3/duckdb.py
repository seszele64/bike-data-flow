import duckdb
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.fs as fs

# Connect to DuckDB
con = duckdb.connect()

# Load httpfs extension for S3 support
con.execute("INSTALL httpfs")
con.execute("LOAD httpfs")

# load env variables
import os
from dotenv import load_dotenv
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
load_dotenv(dotenv_path=dotenv_path, override=True)  # .env is in root directory
from storage.wrm_data.config import HETZNER_ACCESS_KEY_ID, HETZNER_SECRET_ACCESS_KEY, HETZNER_ENDPOINT

# Create a secret with the Hetzner credentials
con.execute(f"""
CREATE OR REPLACE SECRET hetzner_secret (
    TYPE s3,
    PROVIDER config,
    KEY_ID '{HETZNER_ACCESS_KEY_ID}',
    SECRET '{HETZNER_SECRET_ACCESS_KEY}',
    ENDPOINT '{HETZNER_ENDPOINT}',
    REGION 'auto'
);
""")

# Now you can query directly from S3 with the updated path
# For example, if you have Parquet files:
query_result = con.execute("""
SELECT * FROM read_parquet('s3://disband-yodel-botanical/bike-data/gen_info/processed/2025/05/12/*.parquet')
""").fetchdf()

# Print the result
print(query_result)