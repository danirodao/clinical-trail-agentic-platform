# shared/storage.py
"""
MinIO S3-compatible object storage client.
Used by Generator to upload PDFs and Processor to download them.
"""
from minio import Minio
from minio.error import S3Error
import io
import os
import tempfile
import logging
from .config import MinIOConfig

logger = logging.getLogger(__name__)


class ObjectStorage:
    """Wrapper around MinIO for PDF storage."""

    def __init__(self, config: MinIOConfig):
        self.config = config
        self.client = Minio(
            endpoint=config.endpoint,
            access_key=config.access_key,
            secret_key=config.secret_key,
            secure=config.secure
        )
        self._ensure_bucket()

    def _ensure_bucket(self):
        if not self.client.bucket_exists(self.config.bucket):
            self.client.make_bucket(self.config.bucket)
            logger.info(f"Created bucket: {self.config.bucket}")

    def upload_pdf(
        self,
        local_path: str,
        object_key: str
    ) -> int:
        """
        Upload a PDF file to MinIO.
        Returns file size in bytes.
        """
        file_size = os.path.getsize(local_path)
        self.client.fput_object(
            bucket_name=self.config.bucket,
            object_name=object_key,
            file_path=local_path,
            content_type="application/pdf"
        )
        logger.info(
            f"Uploaded {object_key} ({file_size:,} bytes) "
            f"to {self.config.bucket}"
        )
        return file_size

    def download_pdf(self, object_key: str) -> str:
        """
        Download a PDF from MinIO to a temporary file.
        Returns the local temp file path.
        Caller is responsible for cleanup.
        """
        temp_dir = tempfile.mkdtemp(prefix="ct_pdf_")
        filename = os.path.basename(object_key)
        local_path = os.path.join(temp_dir, filename)

        self.client.fget_object(
            bucket_name=self.config.bucket,
            object_name=object_key,
            file_path=local_path
        )
        logger.info(f"Downloaded {object_key} → {local_path}")
        return local_path

    def delete_pdf(self, object_key: str):
        """Delete a PDF from MinIO (for cleanup after processing)."""
        self.client.remove_object(
            bucket_name=self.config.bucket,
            object_name=object_key
        )

    def list_pdfs(self, prefix: str = "") -> list[str]:
        """List all PDF objects with a given prefix."""
        objects = self.client.list_objects(
            self.config.bucket, prefix=prefix, recursive=True
        )
        return [
            obj.object_name for obj in objects
            if obj.object_name.endswith(".pdf")
        ]