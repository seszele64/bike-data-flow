"""Tests for S3 operation helpers."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError

from wrm_pipeline.retry import RetryConfiguration
from wrm_pipeline.retry.operations import (
    retry_s3_upload,
    retry_s3_download,
    retry_s3_delete,
    retry_s3_list_objects,
    S3OperationError,
    S3UploadError,
    S3DownloadError,
    S3DeleteError,
    _extract_s3_error_code,
)


class TestRetryS3Upload:
    """Test retry_s3_upload function."""
    
    def test_successful_upload(self):
        """Test successful upload without retry."""
        mock_client = Mock()
        mock_client.put_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        
        result = retry_s3_upload(
            mock_client,
            bucket="test-bucket",
            key="test/key.parquet",
            body=b"test data",
        )
        
        assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
        mock_client.put_object.assert_called_once()
    
    def test_retry_on_throttling_exception(self):
        """Test retry on ThrottlingException."""
        mock_client = Mock()
        
        # First call raises ThrottlingException
        error_response = {
            "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}
        }
        mock_client.put_object.side_effect = [
            ClientError(error_response, "PutObject"),
            {"ResponseMetadata": {"HTTPStatusCode": 200}},
        ]
        
        result = retry_s3_upload(
            mock_client,
            bucket="test-bucket",
            key="test/key.parquet",
            body=b"test data",
            config=RetryConfiguration(
                max_attempts=3,
                base_delay=0.01,
                max_delay=1.0,
                jitter=0.0,
            ),
        )
        
        assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
        assert mock_client.put_object.call_count == 2
    
    def test_no_retry_on_access_denied(self):
        """Test that AccessDenied raises immediately."""
        mock_client = Mock()
        
        error_response = {
            "Error": {"Code": "AccessDenied", "Message": "Access Denied"}
        }
        mock_client.put_object.side_effect = ClientError(error_response, "PutObject")
        
        with pytest.raises(S3UploadError):
            retry_s3_upload(
                mock_client,
                bucket="test-bucket",
                key="test/key.parquet",
                body=b"test data",
            )
        
        assert mock_client.put_object.call_count == 1
    
    def test_upload_with_metadata(self):
        """Test upload with metadata."""
        mock_client = Mock()
        mock_client.put_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        
        retry_s3_upload(
            mock_client,
            bucket="test-bucket",
            key="test/key.parquet",
            body=b"test data",
            metadata={"source": "pipeline", "version": "1"},
        )
        
        call_kwargs = mock_client.put_object.call_args[1]
        assert call_kwargs["Metadata"] == {"source": "pipeline", "version": "1"}
    
    def test_retry_exhausted_raises_retry_exhausted_exception(self):
        """Test that RetryExhaustedException is raised after max attempts."""
        mock_client = Mock()
        
        error_response = {
            "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}
        }
        mock_client.put_object.side_effect = ClientError(error_response, "PutObject")
        
        from wrm_pipeline.retry.exceptions import RetryExhaustedException
        
        with pytest.raises(RetryExhaustedException):
            retry_s3_upload(
                mock_client,
                bucket="test-bucket",
                key="test/key.parquet",
                body=b"test data",
                config=RetryConfiguration(
                    max_attempts=3,
                    base_delay=0.01,
                    max_delay=1.0,
                    jitter=0.0,
                ),
            )
        
        assert mock_client.put_object.call_count == 3


class TestRetryS3Download:
    """Test retry_s3_download function."""
    
    def test_successful_download_to_memory(self):
        """Test successful download to memory."""
        mock_client = Mock()
        mock_response = {
            "Body": Mock(read=Mock(return_value=b"test data")),
        }
        mock_client.get_object.return_value = mock_response
        
        result = retry_s3_download(
            mock_client,
            bucket="test-bucket",
            key="test/key.parquet",
        )
        
        assert result == b"test data"
        mock_client.get_object.assert_called_once()
    
    def test_retry_on_connection_error(self):
        """Test retry on connection error."""
        from botocore.exceptions import EndpointConnectionError
        
        mock_client = Mock()
        mock_client.get_object.side_effect = [
            EndpointConnectionError(endpoint_url="http://localhost"),
            {"Body": Mock(read=Mock(return_value=b"test data"))},
        ]
        
        result = retry_s3_download(
            mock_client,
            bucket="test-bucket",
            key="test/key.parquet",
            config=RetryConfiguration(
                max_attempts=3,
                base_delay=0.01,
                max_delay=1.0,
                jitter=0.0,
            ),
        )
        
        assert result == b"test data"
        assert mock_client.get_object.call_count == 2
    
    def test_no_retry_on_no_such_key(self):
        """Test that NoSuchKey raises immediately."""
        mock_client = Mock()
        
        error_response = {
            "Error": {"Code": "NoSuchKey", "Message": "Key not found"}
        }
        mock_client.get_object.side_effect = ClientError(error_response, "GetObject")
        
        with pytest.raises(S3DownloadError):
            retry_s3_download(
                mock_client,
                bucket="test-bucket",
                key="nonexistent/key.parquet",
            )
    
    def test_download_to_file(self):
        """Test download to file path."""
        mock_client = Mock()
        
        result = retry_s3_download(
            mock_client,
            bucket="test-bucket",
            key="test/key.parquet",
            filepath="/tmp/downloaded.parquet",
        )
        
        assert result == "/tmp/downloaded.parquet"
        mock_client.download_file.assert_called_once_with(
            "test-bucket", "test/key.parquet", "/tmp/downloaded.parquet"
        )


class TestRetryS3Delete:
    """Test retry_s3_delete function."""
    
    def test_successful_delete(self):
        """Test successful delete."""
        mock_client = Mock()
        mock_client.delete_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 204}}
        
        result = retry_s3_delete(
            mock_client,
            bucket="test-bucket",
            key="test/key.parquet",
        )
        
        assert result is True
        mock_client.delete_object.assert_called_once()
    
    def test_no_such_key_not_error(self):
        """Test that NoSuchKey returns True (already deleted)."""
        mock_client = Mock()
        
        error_response = {
            "Error": {"Code": "NoSuchKey", "Message": "Key not found"}
        }
        mock_client.delete_object.side_effect = ClientError(error_response, "DeleteObject")
        
        result = retry_s3_delete(
            mock_client,
            bucket="test-bucket",
            key="deleted/key.parquet",
        )
        
        assert result is True
    
    def test_retry_on_throttling(self):
        """Test retry on ThrottlingException."""
        mock_client = Mock()
        
        error_response = {
            "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}
        }
        mock_client.delete_object.side_effect = [
            ClientError(error_response, "DeleteObject"),
            {"ResponseMetadata": {"HTTPStatusCode": 204}},
        ]
        
        result = retry_s3_delete(
            mock_client,
            bucket="test-bucket",
            key="test/key.parquet",
            config=RetryConfiguration(
                max_attempts=3,
                base_delay=0.01,
                max_delay=1.0,
                jitter=0.0,
            ),
        )
        
        assert result is True
        assert mock_client.delete_object.call_count == 2


class TestRetryS3ListObjects:
    """Test retry_s3_list_objects function."""
    
    def test_successful_list(self):
        """Test successful list."""
        mock_client = Mock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "key1.parquet"},
                {"Key": "key2.parquet"},
            ],
            "IsTruncated": False,
        }
        
        result = retry_s3_list_objects(
            mock_client,
            bucket="test-bucket",
            prefix="data/",
        )
        
        assert result == ["key1.parquet", "key2.parquet"]
        mock_client.list_objects_v2.assert_called_once()
    
    def test_pagination(self):
        """Test pagination with continuation token."""
        mock_client = Mock()
        mock_client.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": "key1.parquet"}],
                "IsTruncated": True,
                "NextContinuationToken": "token123",
            },
            {
                "Contents": [{"Key": "key2.parquet"}],
                "IsTruncated": False,
            },
        ]
        
        result = retry_s3_list_objects(
            mock_client,
            bucket="test-bucket",
        )
        
        assert result == ["key1.parquet", "key2.parquet"]
        assert mock_client.list_objects_v2.call_count == 2


class TestS3ExceptionMapping:
    """Test S3 exception mapping."""
    
    def test_extract_error_code(self):
        """Test error code extraction."""
        error_response = {
            "Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}
        }
        error = ClientError(error_response, "PutObject")
        
        assert _extract_s3_error_code(error) == "ThrottlingException"
    
    def test_custom_exceptions(self):
        """Test custom exception hierarchy."""
        base_error = S3OperationError("Test error")
        assert isinstance(base_error, S3OperationError)
        
        upload_error = S3UploadError("Upload failed")
        assert isinstance(upload_error, S3UploadError)
        assert isinstance(upload_error, S3OperationError)
        
        download_error = S3DownloadError("Download failed")
        assert isinstance(download_error, S3DownloadError)
        assert isinstance(download_error, S3OperationError)
        
        delete_error = S3DeleteError("Delete failed")
        assert isinstance(delete_error, S3DeleteError)
        assert isinstance(delete_error, S3OperationError)
    
    def test_error_code_unknown_for_malformed_error(self):
        """Test that 'Unknown' is returned for malformed errors."""
        error = ClientError({"Error": {}}, "PutObject")
        assert _extract_s3_error_code(error) == "Unknown"


class TestS3OperationHelper:
    """Test S3OperationHelper class."""
    
    def test_helper_upload(self):
        """Test helper upload method."""
        from wrm_pipeline.retry.operations import S3OperationHelper
        
        mock_client = Mock()
        mock_client.put_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
        
        helper = S3OperationHelper(mock_client, "test-bucket")
        result = helper.upload("key.parquet", b"data")
        
        assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
        mock_client.put_object.assert_called_once()
    
    def test_helper_download(self):
        """Test helper download method."""
        from wrm_pipeline.retry.operations import S3OperationHelper
        
        mock_client = Mock()
        mock_response = {
            "Body": Mock(read=Mock(return_value=b"test data")),
        }
        mock_client.get_object.return_value = mock_response
        
        helper = S3OperationHelper(mock_client, "test-bucket")
        result = helper.download("key.parquet")
        
        assert result == b"test data"
    
    def test_helper_delete(self):
        """Test helper delete method."""
        from wrm_pipeline.retry.operations import S3OperationHelper
        
        mock_client = Mock()
        mock_client.delete_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 204}}
        
        helper = S3OperationHelper(mock_client, "test-bucket")
        result = helper.delete("key.parquet")
        
        assert result is True
    
    def test_helper_list(self):
        """Test helper list method."""
        from wrm_pipeline.retry.operations import S3OperationHelper
        
        mock_client = Mock()
        mock_client.list_objects_v2.return_value = {
            "Contents": [{"Key": "key.parquet"}],
            "IsTruncated": False,
        }
        
        helper = S3OperationHelper(mock_client, "test-bucket")
        result = helper.list(prefix="data/")
        
        assert result == ["key.parquet"]
