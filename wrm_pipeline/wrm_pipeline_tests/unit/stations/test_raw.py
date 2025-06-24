import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
import hashlib
import json
from dagster import build_asset_context
from wrm_pipeline.assets.stations.raw_all import wrm_stations_raw_data_asset
from wrm_pipeline.config import BUCKET_NAME, WRM_STATIONS_S3_PREFIX

class TestWRMStationsRawDataAsset:
    
    @pytest.fixture
    def mock_s3_resource(self):
        """Create a mock S3 resource"""
        mock_s3 = Mock()
        return mock_s3
    
    @pytest.fixture
    def asset_context(self, mock_s3_resource):
        """Create a proper AssetExecutionContext using build_asset_context"""
        return build_asset_context(
            resources={"s3_resource": mock_s3_resource}
        )
    
    @pytest.fixture
    def sample_api_response(self):
        """Sample API response data"""
        return '{"stations": [{"id": 1, "name": "Station 1", "lat": 51.1, "lng": 17.0}]}'
    
    @pytest.fixture
    def corrupted_api_response(self):
        """Corrupted API response with encoding issues"""
        return 'StaÄÄion naÅÄe with encoding issues'
    
    @pytest.fixture
    def real_wrm_api_response(self):
        """Realistic WRM API response structure"""
        return json.dumps({
            "success": True,
            "data": {
                "stations": [
                    {
                        "id": "001",
                        "name": "Dworzec Główny",
                        "lat": 51.0989,
                        "lng": 17.0377,
                        "bikes": 5,
                        "empty_slots": 10,
                        "total_slots": 15,
                        "status": "online"
                    },
                    {
                        "id": "002", 
                        "name": "Rynek",
                        "lat": 51.1097,
                        "lng": 17.0314,
                        "bikes": 0,
                        "empty_slots": 12,
                        "total_slots": 12,
                        "status": "online"
                    }
                ]
            },
            "timestamp": "2024-01-15T10:30:45Z"
        })

    @patch('wrm_pipeline.assets.stations.raw_all.requests.get')
    @patch('wrm_pipeline.assets.stations.raw_all.datetime')
    def test_successful_upload_new_data(self, mock_datetime, mock_requests, asset_context, sample_api_response):
        """Test successful upload when no existing data"""
        # Setup mocks
        mock_response = Mock()
        mock_response.text = sample_api_response
        mock_response.raise_for_status = Mock()
        mock_requests.return_value = mock_response
        
        mock_now = datetime(2024, 1, 15, 10, 30, 45)
        mock_datetime.now.return_value = mock_now
        
        asset_context.resources.s3_resource.list_objects_v2.return_value = {}
        asset_context.resources.s3_resource.put_object = Mock()
        
        # Execute
        result = wrm_stations_raw_data_asset(asset_context)
        
        # Assertions
        expected_s3_key = f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt"
        assert result == expected_s3_key
        
        asset_context.resources.s3_resource.put_object.assert_called_once()
        call_args = asset_context.resources.s3_resource.put_object.call_args
        assert call_args[1]['Bucket'] == BUCKET_NAME
        assert call_args[1]['Key'] == expected_s3_key
        assert call_args[1]['Body'] == sample_api_response.encode('utf-8')
        assert call_args[1]['ContentType'] == 'text/plain'

    @patch('wrm_pipeline.assets.stations.raw_all.requests.get')
    @patch('wrm_pipeline.assets.stations.raw_all.datetime')
    @patch('wrm_pipeline.assets.stations.raw_all.ftfy.fix_text')
    def test_encoding_fix_applied(self, mock_ftfy, mock_datetime, mock_requests, asset_context, corrupted_api_response):
        """Test that encoding issues are fixed using ftfy"""
        # Setup mocks
        mock_response = Mock()
        mock_response.text = corrupted_api_response
        mock_response.raise_for_status = Mock()
        mock_requests.return_value = mock_response
        
        fixed_text = 'Station name with encoding issues'
        mock_ftfy.return_value = fixed_text
        
        mock_now = datetime(2024, 1, 15, 10, 30, 45)
        mock_datetime.now.return_value = mock_now
        
        asset_context.resources.s3_resource.list_objects_v2.return_value = {}
        asset_context.resources.s3_resource.put_object = Mock()
        
        # Execute
        wrm_stations_raw_data_asset(asset_context)
        
        # Assertions
        mock_ftfy.assert_called_once_with(corrupted_api_response)
        call_args = asset_context.resources.s3_resource.put_object.call_args
        assert call_args[1]['Body'] == fixed_text.encode('utf-8')

    @patch('wrm_pipeline.assets.stations.raw_all.requests.get')
    @patch('wrm_pipeline.assets.stations.raw_all.datetime')
    def test_duplicate_detection_skips_upload(self, mock_datetime, mock_requests, asset_context, sample_api_response):
        """Test that duplicate data detection prevents redundant uploads"""
        # Setup mocks
        mock_response = Mock()
        mock_response.text = sample_api_response
        mock_response.raise_for_status = Mock()
        mock_requests.return_value = mock_response
        
        mock_now = datetime(2024, 1, 15, 10, 30, 45)
        mock_datetime.now.return_value = mock_now
        
        # Mock existing file with same content
        existing_key = f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-14/wrm_stations_2024-01-14_09-15-30.txt"
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {'Key': existing_key, 'LastModified': datetime(2024, 1, 14, 9, 15, 30)}
            ]
        }
        
        # Mock get_object to return same content
        mock_body = Mock()
        mock_body.read.return_value = sample_api_response.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        
        # Execute
        result = wrm_stations_raw_data_asset(asset_context)
        
        # Assertions
        assert result == existing_key
        asset_context.resources.s3_resource.put_object.assert_not_called()

    @patch('wrm_pipeline.assets.stations.raw_all.requests.get')
    @patch('wrm_pipeline.assets.stations.raw_all.datetime')
    def test_different_data_proceeds_with_upload(self, mock_datetime, mock_requests, asset_context, sample_api_response):
        """Test that different data proceeds with upload"""
        # Setup mocks
        mock_response = Mock()
        mock_response.text = sample_api_response
        mock_response.raise_for_status = Mock()
        mock_requests.return_value = mock_response
        
        mock_now = datetime(2024, 1, 15, 10, 30, 45)
        mock_datetime.now.return_value = mock_now
        
        # Mock existing file with different content
        existing_key = f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-14/wrm_stations_2024-01-14_09-15-30.txt"
        different_content = '{"stations": [{"id": 2, "name": "Different Station"}]}'
        
        asset_context.resources.s3_resource.list_objects_v2.return_value = {
            'Contents': [
                {'Key': existing_key, 'LastModified': datetime(2024, 1, 14, 9, 15, 30)}
            ]
        }
        
        mock_body = Mock()
        mock_body.read.return_value = different_content.encode('utf-8')
        asset_context.resources.s3_resource.get_object.return_value = {'Body': mock_body}
        asset_context.resources.s3_resource.put_object = Mock()
        
        # Execute
        result = wrm_stations_raw_data_asset(asset_context)
        
        # Assertions
        expected_s3_key = f"{WRM_STATIONS_S3_PREFIX}raw/dt=2024-01-15/wrm_stations_2024-01-15_10-30-45.txt"
        assert result == expected_s3_key
        asset_context.resources.s3_resource.put_object.assert_called_once()

    @patch('wrm_pipeline.assets.stations.raw_all.requests.get')
    @patch('wrm_pipeline.assets.stations.raw_all.datetime')
    def test_hash_calculation_consistency(self, mock_datetime, mock_requests, asset_context, sample_api_response):
        """Test that hash calculation is consistent"""
        # Setup mocks
        mock_response = Mock()
        mock_response.text = sample_api_response
        mock_response.raise_for_status = Mock()
        mock_requests.return_value = mock_response
        
        mock_now = datetime(2024, 1, 15, 10, 30, 45)
        mock_datetime.now.return_value = mock_now
        
        asset_context.resources.s3_resource.list_objects_v2.return_value = {}
        asset_context.resources.s3_resource.put_object = Mock()
        asset_context.add_output_metadata = Mock()
        
        # Execute
        wrm_stations_raw_data_asset(asset_context)
        
        # Calculate expected hash
        expected_hash = hashlib.sha256(sample_api_response.encode('utf-8')).hexdigest()
        
        # Assertions
        metadata = asset_context.add_output_metadata.call_args[0][0]
        assert metadata['data_hash'] == expected_hash

    @patch('wrm_pipeline.assets.stations.raw_all.requests.get')
    def test_api_request_failure(self, mock_requests, asset_context):
        """Test handling of API request failures"""
        # Setup mock to raise exception
        mock_requests.side_effect = Exception("API request failed")
        
        # Execute and assert exception is raised
        with pytest.raises(Exception, match="API request failed"):
            wrm_stations_raw_data_asset(asset_context)

    @patch('wrm_pipeline.assets.stations.raw_all.requests.get')
    @patch('wrm_pipeline.assets.stations.raw_all.datetime')
    def test_api_url_called_correctly(self, mock_datetime, mock_requests, asset_context, sample_api_response):
        """Test that the correct API URL is called with proper parameters"""
        # Setup mocks
        mock_response = Mock()
        mock_response.text = sample_api_response
        mock_response.raise_for_status = Mock()
        mock_requests.return_value = mock_response
        
        mock_now = datetime(2024, 1, 15, 10, 30, 45)
        mock_datetime.now.return_value = mock_now
        
        asset_context.resources.s3_resource.list_objects_v2.return_value = {}
        asset_context.resources.s3_resource.put_object = Mock()
        
        # Execute
        wrm_stations_raw_data_asset(asset_context)
        
        # Assertions
        expected_url = "https://gladys.geog.ucl.ac.uk/bikesapi/load.php?scheme=wroclaw"
        mock_requests.assert_called_once_with(expected_url, timeout=30)
        mock_response.raise_for_status.assert_called_once()