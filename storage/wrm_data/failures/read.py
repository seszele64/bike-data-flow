import pandas as pd
from minio.error import S3Error
import io

# Assuming this script is in the 'storage' directory, and 'create_client.py' is also in 'storage'.
from storage.create_client import get_minio_client

def read_csv_from_s3_object_to_pandas(minio_client, bucket_name: str, object_name: str):
    """
    Reads a single CSV object from S3 into a Pandas DataFrame using an existing client.

    Args:
        minio_client: An initialized MinIO client.
        bucket_name (str): The name of the S3 bucket.
        object_name (str): The full path to the CSV file within the bucket.

    Returns:
        pandas.DataFrame: The DataFrame containing the CSV data, or None on error.
    """
    s3_data_stream = None
    try:
        print(f"Attempting to read CSV: s3://{bucket_name}/{object_name}")
        s3_data_stream = minio_client.get_object(bucket_name, object_name)
        # It's good practice to read into BytesIO first if the stream might be used multiple times
        # or if pandas has issues directly with the MinIO stream object in some cases.
        # For pd.read_csv, directly passing the stream usually works.
        df = pd.read_csv(s3_data_stream)
        print(f"Successfully read '{object_name}' into a Pandas DataFrame.")
        return df
    except S3Error as e:
        print(f"S3 Error reading object '{object_name}' from bucket '{bucket_name}': {e}")
        return None
    except pd.errors.EmptyDataError:
        print(f"Error: The file '{object_name}' is empty.")
        return None
    except pd.errors.ParserError as e:
        print(f"Error parsing CSV file '{object_name}': {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred while reading '{object_name}': {e}")
        return None
    finally:
        if s3_data_stream:
            s3_data_stream.close()
            s3_data_stream.release_conn()

def read_all_csvs_from_s3_folder(bucket_name: str, folder_prefix: str):
    """
    Lists all objects in a given S3 folder (prefix), reads each CSV file
    into a Pandas DataFrame, and returns a list of DataFrames.

    Args:
        bucket_name (str): The S3 bucket name.
        folder_prefix (str): The S3 folder prefix (e.g., "bike-data/failures/").
                             Ensure it ends with a '/' if it's a folder.

    Returns:
        list[pd.DataFrame]: A list of Pandas DataFrames, one for each successfully read CSV.
                            Returns an empty list if no CSVs are found or errors occur.
    """
    minio_client = get_minio_client()
    if not minio_client:
        print("Failed to create MinIO client. Exiting.")
        return []

    all_dataframes = []
    try:
        print(f"Listing objects in bucket '{bucket_name}' with prefix '{folder_prefix}'...")
        objects = minio_client.list_objects(bucket_name, prefix=folder_prefix, recursive=True)
        
        found_csv_files = False
        for obj in objects:
            if obj.object_name.lower().endswith(".csv") and not obj.is_dir:
                found_csv_files = True
                print(f"\nFound CSV file: {obj.object_name}")
                df = read_csv_from_s3_object_to_pandas(minio_client, bucket_name, obj.object_name)
                if df is not None:
                    all_dataframes.append({"filename": obj.object_name, "dataframe": df})
        
        if not found_csv_files:
            print(f"No CSV files found in s3://{bucket_name}/{folder_prefix}")

    except S3Error as e:
        print(f"S3 Error listing objects in bucket '{bucket_name}' with prefix '{folder_prefix}': {e}")
    except Exception as e:
        print(f"An unexpected error occurred while listing or processing files: {e}")
        
    return all_dataframes

if __name__ == "__main__":
    # --- Configuration ---
    bucket = "disband-yodel-botanical"
    # Folder (prefix) in S3 containing the CSV files
    s3_folder_prefix = "bike-data/failures/"
    # --- End Configuration ---

    print(f"Reading all CSV files from: s3://{bucket}/{s3_folder_prefix}")
    list_of_failure_data = read_all_csvs_from_s3_folder(bucket_name=bucket, folder_prefix=s3_folder_prefix)

    if list_of_failure_data:
        print(f"\nSuccessfully read {len(list_of_failure_data)} CSV file(s).")
        for item in list_of_failure_data:
            print(f"\n--- DataFrame Head for: {item['filename']} ---")
            print(item['dataframe'].head())
            
            print(f"\n--- DataFrame Info for: {item['filename']} ---")
            item['dataframe'].info()
            
        # If you want to combine them into a single DataFrame (assuming they have compatible columns):
        # all_dfs = [item['dataframe'] for item in list_of_failure_data]
        # if all_dfs:
        #     combined_df = pd.concat(all_dfs, ignore_index=True)
        #     print("\n--- Combined DataFrame Head ---")
        #     print(combined_df.head())
        #     print("\n--- Combined DataFrame Info ---")
        #     combined_df.info()
    else:
        print(f"No CSV dataframes were read from s3://{bucket}/{s3_folder_prefix}")
