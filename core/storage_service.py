import uuid
import mimetypes
from datetime import timedelta
from django.conf import settings
from core.firebase import get_storage_bucket


ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/jpg"}
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


def validate_upload(file) -> None:
    """Raises ValueError if the file fails validation."""
    if file.size > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"File exceeds maximum size of 25 MB. Got {file.size / 1024 / 1024:.1f} MB.")

    content_type = file.content_type or mimetypes.guess_type(file.name)[0] or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise ValueError(f"File type '{content_type}' is not allowed. Allowed: PDF, JPG, PNG.")


def upload_contract_file(file, user_uid: str, contract_id: str = None) -> dict:
    """
    Upload a contract file to Firebase Storage.
    Returns dict with storage_path, public_url, signed_url, content_type, size_bytes.
    """
    validate_upload(file)

    ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else "bin"
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    storage_path = f"contracts/{user_uid}/{unique_name}"

    bucket = get_storage_bucket()
    blob = bucket.blob(storage_path)
    blob.content_type = file.content_type

    file.seek(0)
    blob.upload_from_file(file, content_type=file.content_type)

    signed_url = blob.generate_signed_url(
        expiration=timedelta(hours=2),
        method="GET",
    )

    return {
        "storage_path": storage_path,
        "file_name": file.name,
        "content_type": file.content_type,
        "size_bytes": file.size,
        "signed_url": signed_url,
    }


def get_signed_url(storage_path: str, expiry_hours: int = 2) -> str:
    bucket = get_storage_bucket()
    blob = bucket.blob(storage_path)
    if not blob.exists():
        raise FileNotFoundError(f"File not found in storage: {storage_path}")
    return blob.generate_signed_url(
        expiration=timedelta(hours=expiry_hours),
        method="GET",
    )


def delete_file(storage_path: str) -> None:
    bucket = get_storage_bucket()
    blob = bucket.blob(storage_path)
    if blob.exists():
        blob.delete()


def download_file_bytes(storage_path: str) -> bytes:
    bucket = get_storage_bucket()
    blob = bucket.blob(storage_path)
    if not blob.exists():
        raise FileNotFoundError(f"File not found: {storage_path}")
    return blob.download_as_bytes()
