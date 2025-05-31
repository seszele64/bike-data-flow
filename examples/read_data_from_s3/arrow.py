import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.fs as fs

# load env variables
from storage.wrm_data.config import (
    HETZNER_ENDPOINT,
    HETZNER_ACCESS_KEY_ID,
    HETZNER_SECRET_ACCESS_KEY,
    BUCKET_NAME
)

# Create S3 filesystem with your credentials
s3_fs = fs.S3FileSystem(
    access_key=HETZNER_ACCESS_KEY_ID,
    secret_key=HETZNER_SECRET_ACCESS_KEY,
    endpoint_override=HETZNER_ENDPOINT,
    region='auto'
)

# Example: Read data from your S3 bucket
bucket_name = BUCKET_NAME
path = 'bike-data/gen_info/processed/'

# Read as a dataset (for multiple files or partitioned data)
dataset = ds.dataset(
    f"{bucket_name}/{path}", 
    format="parquet",
    filesystem=s3_fs
)

# Convert to table
table = dataset.to_table()

# Print table schema
print(table.schema)

# Print first few rows
print(table.slice(0, 100).to_pandas())

# Alternatively, read a specific file
# file_path = f"{bucket_name}/path/to/specific/file.parquet"
# with s3_fs.open_input_file(file_path) as f:
#     table = pa.parquet.read_table(f)