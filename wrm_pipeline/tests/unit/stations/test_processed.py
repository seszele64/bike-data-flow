import pytest
import pandas as pd
import pandera as pa
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from io import StringIO
from dagster import build_asset_context
from wrm_pipeline.assets.stations.processed_all import wrm_stations_processed_data_all_asset
from wrm_pipeline.config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX


class TestWRMStationsProcessedDataAllAsset:
    
    @pytest.fixture
    def mock_s3_resource(self):
        """Create a mock S3 resource"""
        mock_s3 = Mock()
        return mock_s3
    
    @pytest.fixture
    def asset_context(self, mock_s3_resource):
        """Create a proper AssetExecutionContext using build_asset_context"""
        return build_asset_context(
            partition_key="2024-01-15",
            resources={"s3_resource": mock_s3_resource}
        )
    
    @pytest.fixture
    def sample_raw_data(self):
        """Sample raw CSV data from S3"""
        return """#id,1705147845.123|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs
001,1705147845.123|3600|-3600,Station 1,51.1089,17.0377,5,10,true,false,false,15,false,2
002,1705147845.456|3600|-3600,Station 2,51.1097,17.0314,0,12,true,false,false,12,true,3
003,1705147845.789|3600|-3600,Station 3,51.1105,17.0251,8,7,true,false,false,15,false,1"""
    
    @pytest.fixture
    def multiple_raw_files_data(self):
        """Multiple raw files with different timestamps"""
        return [
            {
                'key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                'data': """#id,1705147845.123|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs
001,1705147845.123|3600|-3600,Station 1,51.1089,17.0377,5,10,true,false,false,15,false,2
002,1705147845.456|3600|-3600,Station 2,51.1097,17.0314,0,12,true,false,false,12,true,3""",
                'last_modified': datetime(2024, 1, 15, 10, 30, 45)
            },
            {
                'key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_11-15-30.txt",
                'data': """#id,1705151730.456|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs
001,1705151730.456|3600|-3600,Station 1,51.1089,17.0377,3,12,true,false,false,15,false,1
003,1705151730.789|3600|-3600,Station 3,51.1105,17.0251,8,7,true,false,false,15,false,2""",
                'last_modified': datetime(2024, 1, 15, 11, 15, 30)
            }
        ]
    
    @pytest.fixture
    def partially_corrupted_raw_data(self):
        """Raw data with some corrupted rows but still some valid ones that pass type conversion"""
        return """#id,1705147845.123|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs
001,1705147845.123|3600|-3600,Station 1,51.1089,17.0377,5,10,true,false,false,15,false,2
002,invalid_timestamp,Station 2,51.1097,17.0314,0,12,true,false,false,12,true,3
003,1705147845.789|3600|-3600,Station 3,51.1105,17.0251,8,7,true,false,false,15,false,1"""
    
    @pytest.fixture
    def corrupted_raw_data(self):
        """Raw data with some corrupted rows mixed with valid rows"""
        return """#id,1705147845.123|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs
001,1705147845.123|3600|-3600,Station 1,51.1089,17.0377,5,10,true,false,false,15,false,2
002,invalid_timestamp,Station 2,51.1097,17.0314,0,12,true,false,false,12,true,3
003,1705147845.789|3600|-3600,Station 3,51.1105,17.0251,8,7,true,false,false,15,false,1
004,1705147845.999|not_a_number|-3600,Station 4,51.1110,17.0290,2,13,true,false,false,15,false,0
005,1705147845.999|3600|-3600,Station 5,invalid_lat,17.0251,2,13,true,false,false,15,false,0
006,1705147846.123|3600|-3600,Station 6,51.1120,17.0300,3,12,true,false,false,15,false,1"""
    
    @pytest.fixture
    def empty_raw_data(self):
        """Empty raw data file"""
        return """#id,1705147845.123|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs"""
    
    @pytest.fixture
    def mixed_quality_raw_data(self):
        """Raw data with some rows that will be filtered during CSV parsing but others that succeed"""
        return """#id,1705147845.123|3600|-3600,name,lat,lon,bikes,spaces,installed,locked,temporary,total_docks,givesbonus_acceptspedelecs_fbbattlevel,pedelecs
001,1705147845.123|3600|-3600,Station 1,51.1089,17.0377,5,10,true,false,false,15,false,2
002,1705147845.456|3600|-3600,Station 2,51.1097,17.0314,0,12,true,false,false,12,true,3
003,corrupted_row_data
004,1705147845.789|3600|-3600,Station 4,51.1105,17.0251,8,7,true,false,false,15,false,1"""

    def test_successful_processing_single_file(self, asset_context, sample_raw_data):
        """Test successful processing of a single raw data file"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = sample_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        # Mock schema validation to pass
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            mock_schema.validate.side_effect = lambda df: df  # Return the DataFrame unchanged
            
            # Execute
            result = wrm_stations_processed_data_all_asset(asset_context)
            
            # Assertions
            assert isinstance(result, pd.DataFrame)
            assert len(result) == 3  # Should have 3 records
            assert 'station_id' in result.columns
            assert 'timestamp' in result.columns
            assert 's3_source_key' in result.columns
            assert 'file_timestamp' in result.columns

    def test_multiple_files_processing(self, asset_context, multiple_raw_files_data):
        """Test processing multiple raw data files for same partition"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': file_data['key'],
                    'LastModified': file_data['last_modified']
                }
                for file_data in multiple_raw_files_data
            ]
        }
        
        # Mock get_object to return different data for different keys
        def mock_get_object(Bucket, Key):
            for file_data in multiple_raw_files_data:
                if file_data['key'] == Key:
                    mock_body = Mock()
                    mock_body.read.return_value = file_data['data'].encode('utf-8')
                    return {'Body': mock_body}
            raise KeyError(f"Key {Key} not found")
        
        asset_context.resources.s3_resource.get_object.side_effect = mock_get_object
        
        # Mock the add_output_metadata method
        mock_add_metadata = Mock()
        asset_context.add_output_metadata = mock_add_metadata
        
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            mock_schema.validate.side_effect = lambda df: df
            
            # Execute
            result = wrm_stations_processed_data_all_asset(asset_context)
            
            # Assertions
            assert isinstance(result, pd.DataFrame)
            assert len(result) == 4  # Should have 4 total records from both files
            
            # Verify data from both files is included
            source_keys = result['s3_source_key'].unique()
            assert len(source_keys) == 2
            
            # Check metadata was added
            assert mock_add_metadata.called

    def test_data_transformation_correctness(self, asset_context, sample_raw_data):
        """Test that data transformation works correctly"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = sample_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        # Don't mock the schema, let it run to test transformation
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            # Create a real DataFrame to validate transformation
            def validate_and_return(df):
                # Verify the transformation worked correctly
                assert 'station_id' in df.columns
                assert 'timestamp' in df.columns
                assert 'gmt_local_diff_sec' in df.columns
                assert 'gmt_servertime_diff_sec' in df.columns
                assert 'name' in df.columns
                
                # Check data types
                assert df['timestamp'].dtype == 'datetime64[ns]'
                assert df['gmt_local_diff_sec'].dtype == 'int64'
                assert df['gmt_servertime_diff_sec'].dtype == 'int64'
                assert df['lat'].dtype == 'float64'
                assert df['lon'].dtype == 'float64'
                assert df['bikes'].dtype == 'int64'
                assert df['spaces'].dtype == 'int64'
                
                # Check boolean conversions
                assert df['installed'].dtype == 'bool'
                assert df['locked'].dtype == 'bool'
                assert df['temporary'].dtype == 'bool'
                assert df['givesbonus_acceptspedelecs_fbbattlevel'].dtype == 'bool'
                
                return df
            
            mock_schema.validate.side_effect = validate_and_return
            
            # Execute
            result = wrm_stations_processed_data_all_asset(asset_context)
            
            # Additional assertions
            assert len(result) == 3
            assert result['station_id'].tolist() == ['001', '002', '003']
            assert result['name'].tolist() == ['Station 1', 'Station 2', 'Station 3']

    def test_corrupted_data_handling(self, asset_context, corrupted_raw_data):
        """Test handling of corrupted data rows - expects ValueError when all data is corrupted"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = corrupted_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        # Execute and expect ValueError when corrupted data can't be processed
        # The asset function should fail gracefully when data type conversion fails
        with pytest.raises(ValueError, match="No valid data found after processing"):
            wrm_stations_processed_data_all_asset(asset_context)

    def test_partially_corrupted_data_handling(self, asset_context, partially_corrupted_raw_data):
        """Test handling of partially corrupted data - some rows are invalid but valid rows can still be processed"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = partially_corrupted_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            # Mock schema validation to pass through the DataFrame unchanged
            mock_schema.validate.side_effect = lambda df: df
            
            # Execute - should process the valid rows and skip the corrupted ones
            result = wrm_stations_processed_data_all_asset(asset_context)
            
            # Should return a DataFrame with only the successfully processed rows
            assert isinstance(result, pd.DataFrame)
            # Should have processed 2 valid rows (001, 003), skipping the corrupted row 002
            assert len(result) == 2
            assert 'station_id' in result.columns
            # Check that only valid station IDs are present
            station_ids = result['station_id'].tolist()
            assert '001' in station_ids
            assert '003' in station_ids
            assert '002' not in station_ids  # This row should be skipped due to invalid timestamp

    def test_empty_data_file(self, asset_context, empty_raw_data):
        """Test handling of empty data files"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = empty_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        # Execute and expect exception due to no valid data
        with pytest.raises(ValueError, match="No valid data found after processing"):
            wrm_stations_processed_data_all_asset(asset_context)

    def test_no_raw_files_found(self, asset_context):
        """Test handling when no raw files are found for partition"""
        # Setup S3 mock to return empty contents
        asset_context.resources.s3_resource.list_objects_v2.return_value = {}
        
        # Execute and expect FileNotFoundError
        with pytest.raises(FileNotFoundError, match="No raw data found for partition date"):
            wrm_stations_processed_data_all_asset(asset_context)

    def test_schema_validation_failure(self, asset_context, sample_raw_data):
        """Test handling of schema validation failures"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = sample_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        # Mock schema validation to fail
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            # Create a proper SchemaError with required arguments
            schema_error = pa.errors.SchemaError(
                schema=mock_schema,
                data=pd.DataFrame(),
                message="Schema validation failed",
                failure_cases=None,
                check=None
            )
            mock_schema.validate.side_effect = schema_error
            
            # Execute and expect ValueError
            with pytest.raises(ValueError, match="DataFrame does not match processed_data_schema"):
                wrm_stations_processed_data_all_asset(asset_context)

    def test_timestamp_extraction_from_filename(self, asset_context, sample_raw_data):
        """Test timestamp extraction from filename"""
        # Setup S3 mocks with specific filename
        filename = f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt"
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': filename,
                    'LastModified': datetime(2024, 1, 15, 12, 0, 0)  # Different from filename timestamp
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = sample_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            def validate_and_check_timestamp(df):
                # Check that file_timestamp matches filename, not LastModified
                expected_timestamp = datetime(2024, 1, 15, 10, 30, 45)
                assert all(df['file_timestamp'] == expected_timestamp)
                return df
            
            mock_schema.validate.side_effect = validate_and_check_timestamp
            
            # Execute
            result = wrm_stations_processed_data_all_asset(asset_context)
            assert isinstance(result, pd.DataFrame)

    def test_timestamp_fallback_to_lastmodified(self, asset_context, sample_raw_data):
        """Test timestamp fallback when filename doesn't match pattern"""
        # Setup S3 mocks with non-standard filename
        filename = f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/invalid_filename.txt"
        last_modified = datetime(2024, 1, 15, 12, 0, 0)
        
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': filename,
                    'LastModified': last_modified
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = sample_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            def validate_and_check_timestamp(df):
                # Check that file_timestamp uses LastModified when filename doesn't match
                assert all(df['file_timestamp'] == last_modified.replace(tzinfo=None))
                return df
            
            mock_schema.validate.side_effect = validate_and_check_timestamp
            
            # Execute
            result = wrm_stations_processed_data_all_asset(asset_context)
            assert isinstance(result, pd.DataFrame)

    def test_files_processed_in_chronological_order(self, asset_context, multiple_raw_files_data):
        """Test that files are processed in chronological order by LastModified"""
        # Reverse the order to ensure sorting works
        reversed_files = list(reversed(multiple_raw_files_data))
        
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': file_data['key'],
                    'LastModified': file_data['last_modified']
                }
                for file_data in reversed_files
            ]
        }
        
        # Track the order of get_object calls
        get_object_calls = []
        
        def mock_get_object(Bucket, Key):
            get_object_calls.append(Key)
            for file_data in multiple_raw_files_data:
                if file_data['key'] == Key:
                    mock_body = Mock()
                    mock_body.read.return_value = file_data['data'].encode('utf-8')
                    return {'Body': mock_body}
            raise KeyError(f"Key {Key} not found")
        
        asset_context.resources.s3_resource.get_object.side_effect = mock_get_object
        
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            mock_schema.validate.return_value = pd.DataFrame()
            
            # Execute
            wrm_stations_processed_data_all_asset(asset_context)
            
            # Verify files were processed in chronological order (earliest first)
            expected_order = [
                multiple_raw_files_data[0]['key'],  # 10:30:45
                multiple_raw_files_data[1]['key']   # 11:15:30
            ]
            assert get_object_calls == expected_order

    def test_s3_get_object_failure(self, asset_context):
        """Test handling of S3 get_object failures"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        # Mock get_object to raise exception
        asset_context.resources.s3_resource.get_object.side_effect = Exception("S3 access denied")
        
        # Execute and expect the exception to be raised
        with pytest.raises(Exception):
            wrm_stations_processed_data_all_asset(asset_context)

    def test_metadata_output(self, asset_context, sample_raw_data):
        """Test that correct metadata is output"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = sample_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        # Mock the add_output_metadata method
        mock_add_metadata = Mock()
        asset_context.add_output_metadata = mock_add_metadata
        
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            mock_schema.validate.return_value = pd.DataFrame()
            
            # Execute
            result = wrm_stations_processed_data_all_asset(asset_context)
            
            # Verify metadata was added
            assert mock_add_metadata.called
            metadata = mock_add_metadata.call_args[0][0]
            
            # Check expected metadata keys
            expected_keys = [
                'columns', 'column_types', 'total_records', 'partition_date',
                'processed_files_count', 'processed_files', 'data_preview', 'schema_validation'
            ]
            for key in expected_keys:
                assert key in metadata
            
            assert metadata['partition_date'] == '2024-01-15'
            assert metadata['processed_files_count'] == 1
            assert metadata['schema_validation'] == 'PASSED'

    def test_mixed_quality_data_handling(self, asset_context, mixed_quality_raw_data):
        """Test handling of mixed quality data where some rows are completely malformed but others process successfully"""
        # Setup S3 mocks
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {
                    'Key': f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt",
                    'LastModified': datetime(2024, 1, 15, 10, 30, 45)
                }
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = mixed_quality_raw_data.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        with patch('wrm_pipeline.assets.stations.processed_all.processed_data_schema') as mock_schema:
            # Mock schema validation to pass through the DataFrame unchanged
            mock_schema.validate.side_effect = lambda df: df
            
            # Execute - should process the valid rows and skip the malformed ones
            result = wrm_stations_processed_data_all_asset(asset_context)
            
            # Should return a DataFrame with only the successfully processed rows
            assert isinstance(result, pd.DataFrame)
            # Should have processed 3 valid rows (001, 002, 004), skipping the malformed row 003
            assert len(result) == 3
            assert 'station_id' in result.columns
            assert result['station_id'].tolist() == ['001', '002', '004']