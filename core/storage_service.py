import os
import re
import mimetypes
from datetime import datetime, timedelta, timezone

from core.gcs import get_gcs_bucket


ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/jpg",
}

MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


def validate_upload(file) -> None:
    """Raises ValueError if the file fails validation."""

    if file.size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File exceeds maximum size of 25 MB. "
            f"Got {file.size / 1024 / 1024:.1f} MB."
        )

    content_type = (
        file.content_type
        or mimetypes.guess_type(file.name)[0]
        or ""
    )

    if content_type not in ALLOWED_MIME_TYPES:
        raise ValueError(
            f"File type '{content_type}' is not allowed. "
            f"Allowed: PDF, JPG, PNG."
        )


def upload_contract_file(
    file,
    user_uid: str,
    contract_id: str = None,
) -> dict:
    """
    Upload a contract file to Google Cloud Storage.

    The GCS filename keeps the original filename and adds
    the upload date/time.

    Example:
        Original:
            Master Agreement.pdf

        GCS:
            contracts/user123/Master Agreement_2026-09-29_17-05-32.pdf
    """

    validate_upload(file)

    # Get the original filename only
    original_name = os.path.basename(file.name)

    # Remove characters that can cause issues in storage paths
    safe_name = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        original_name,
    )

    # Add upload date and time in UTC
    upload_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Keep the original extension
    if "." in safe_name:
        name, ext = safe_name.rsplit(".", 1)
        file_name = f"{name}_{upload_date}.{ext}"
    else:
        file_name = f"{safe_name}_{upload_date}"

    # Store files under the user's folder
    storage_path = f"contracts/{user_uid}/{file_name}"

    # Get Google Cloud Storage bucket
    bucket = get_gcs_bucket()

    # Create GCS blob
    blob = bucket.blob(storage_path)

    # Set content type
    blob.content_type = file.content_type

    # Make sure we're reading the file from the beginning
    file.seek(0)

    # Upload file
    blob.upload_from_file(
        file,
        content_type=file.content_type,
    )

    # Generate temporary signed URL
    signed_url = blob.generate_signed_url(
        expiration=timedelta(hours=2),
        method="GET",
    )

    return {
        # GCS path
        "storage_path": storage_path,

        # Original filename shown to the application/UI
        "file_name": file.name,

        # MIME type
        "content_type": file.content_type,

        # File size
        "size_bytes": file.size,

        # Temporary download/view URL
        "signed_url": signed_url,
    }


def get_signed_url(
    storage_path: str,
    expiry_hours: int = 2,
) -> str:
    """
    Generate a temporary signed URL for an existing GCS file.
    """

    bucket = get_gcs_bucket()

    blob = bucket.blob(storage_path)

    if not blob.exists():
        raise FileNotFoundError(
            f"File not found in storage: {storage_path}"
        )

    return blob.generate_signed_url(
        expiration=timedelta(hours=expiry_hours),
        method="GET",
    )


def delete_file(storage_path: str) -> None:
    """
    Delete a file from Google Cloud Storage.
    """

    bucket = get_gcs_bucket()

    blob = bucket.blob(storage_path)

    if blob.exists():
        blob.delete()


def download_file_bytes(storage_path: str) -> bytes:
    """
    Download a file from Google Cloud Storage as bytes.
    """

    bucket = get_gcs_bucket()

    blob = bucket.blob(storage_path)

    if not blob.exists():
        raise FileNotFoundError(
            f"File not found: {storage_path}"
        )

    return blob.download_as_bytes()