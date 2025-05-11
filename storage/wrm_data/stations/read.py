from minio.error import S3Error
import os
from storage.create_client import get_minio_client # Import the function

def read_data_from_s3():
    # Get MinIO client using the centralized function
    client = get_minio_client()

    if not client:
        print("Failed to create MinIO client. Exiting.")
        return

    bucket_name = "disband-yodel-botanical"
    prefix_to_list = "bike-data/gen_info/"

    try:
        print(f"Listing objects in bucket '{bucket_name}' with prefix '{prefix_to_list}'...")
        objects = client.list_objects(bucket_name, prefix=prefix_to_list, recursive=True)
        
        found_files = False
        for obj in objects:
            found_files = True
            print(f"Found object: {obj.object_name}")
            
            # To read the content of the file:
            try:
                print(f"Reading content of '{obj.object_name}'...")
                data = client.get_object(bucket_name, obj.object_name)
                file_content = data.read().decode('utf-8')
                print(f"--- Content of {obj.object_name} ---")
                print(file_content[:500] + "..." if len(file_content) > 500 else file_content) # Print first 500 chars
                print("--- End of content ---")
                # Process the file_content as needed
            except S3Error as e:
                print(f"Error reading object {obj.object_name}: {e}")
            finally:
                if 'data' in locals() and hasattr(data, 'close'):
                    data.close()
                if 'data' in locals() and hasattr(data, 'release_conn'):
                    data.release_conn()
        
        if not found_files:
            print(f"No files found in bucket '{bucket_name}' with prefix '{prefix_to_list}'.")
            print("Please check:")
            print(f"1. Bucket name: '{bucket_name}' is correct.")
            print(f"2. Prefix: '{prefix_to_list}' is correct (ensure it ends with a '/' if it's a folder).")
            print(f"3. Credentials and endpoint are correct and have permissions for this bucket/prefix.")
            print(f"4. Files actually exist at the specified path.")


    except S3Error as exc:
        print(f"Error occurred: {exc}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    # Ensure your .env file is in the root of your project or specify its path
    # in get_minio_client if it's located elsewhere.
    # Example: client = get_minio_client(dotenv_path='/path/to/your/.env')
    
    # The .env file should contain:
    # HETZNER_ENDPOINT_URL='https://your-s3-endpoint.com'
    # HETZNER_ACCESS_KEY_ID='youraccesskey'
    # HETZNER_SECRET_ACCESS_KEY='yoursecretkey'
    
    read_data_from_s3()