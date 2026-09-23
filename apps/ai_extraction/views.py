from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from core.exceptions import success_response, error_response
from core.roles import require
from core.storage_service import upload_contract_file, download_file_bytes
from core.audit_service import log_action, get_client_ip, ACTIONS
from .gemini_service import (
    extract_contract_data_from_pdf,
    extract_contract_data_from_image,
)
import logging

logger = logging.getLogger(__name__)

SUPPORTED_MIME_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/png": "image",
}


class ExtractContractView(APIView):
    """
    POST /api/ai/extract/
    Accepts a file upload, stores it in Firebase Storage,
    runs AI extraction, returns structured JSON for the confirmation screen.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        file = request.FILES.get("file")

        if not file:
            return error_response("No file provided.")

        content_type = file.content_type or ""
        file_category = SUPPORTED_MIME_TYPES.get(content_type)
        if not file_category:
            return error_response(
                f"Unsupported file type: {content_type}. Allowed: PDF, JPG, PNG."
            )

        # Step 1: Upload to Firebase Storage
        try:
            upload_result = upload_contract_file(file, uid)
        except ValueError as exc:
            return error_response(str(exc), 400)
        except Exception as exc:
            logger.error(f"Upload error during extraction: {exc}")
            return error_response("File upload failed.", 500)

        storage_path = upload_result["storage_path"]

        # Step 2: Download bytes for AI processing
        try:
            file_bytes = download_file_bytes(storage_path)
        except Exception as exc:
            logger.error(f"Download error for extraction: {exc}")
            return error_response("Could not read uploaded file.", 500)

        # Step 3: Run extraction
        try:
            if file_category == "pdf":
                extracted = extract_contract_data_from_pdf(file_bytes)
            else:
                extracted = extract_contract_data_from_image(file_bytes, content_type)
        except Exception as exc:
            logger.error(f"AI extraction error: {exc}")
            return error_response("AI extraction failed. You can still fill in the details manually.", 500)

        log_action(
            user_uid=uid,
            action=ACTIONS["UPLOAD"],
            resource_type="contract_extraction",
            resource_id=storage_path,
            description=f"AI extracted: {upload_result['file_name']}",
            metadata={
                "confidence_score": extracted.get("confidence_score"),
                "file_name": upload_result["file_name"],
            },
            ip_address=get_client_ip(request),
        )

        return success_response(
            data={
                "extracted": extracted,
                "storage_path": storage_path,
                "file_name": upload_result["file_name"],
                "file_size_bytes": upload_result["size_bytes"],
                "signed_url": upload_result["signed_url"],
            },
            message="Extraction complete. Please review and confirm the details.",
        )


class ReExtractView(APIView):
    """
    POST /api/ai/re-extract/
    Re-run extraction on an already-uploaded file (by storage_path).
    Used if user wants to retry AI extraction.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        storage_path = request.data.get("storage_path", "").strip()
        if not storage_path:
            return error_response("storage_path is required.")

        # Security: ensure path belongs to this user
        if f"contracts/{uid}/" not in storage_path:
            return error_response("Unauthorized.", 403)

        try:
            file_bytes = download_file_bytes(storage_path)
        except FileNotFoundError:
            return error_response("File not found.", 404)
        except Exception as exc:
            logger.error(f"Re-extract download error: {exc}")
            return error_response("Could not read file.", 500)

        # Determine type from path extension
        ext = storage_path.rsplit(".", 1)[-1].lower() if "." in storage_path else ""
        try:
            if ext == "pdf":
                extracted = extract_contract_data_from_pdf(file_bytes)
            elif ext in ("jpg", "jpeg"):
                extracted = extract_contract_data_from_image(file_bytes, "image/jpeg")
            elif ext == "png":
                extracted = extract_contract_data_from_image(file_bytes, "image/png")
            else:
                return error_response("Cannot determine file type from storage path.")
        except Exception as exc:
            logger.error(f"Re-extract AI error: {exc}")
            return error_response("AI extraction failed.", 500)

        return success_response(
            data={"extracted": extracted},
            message="Re-extraction complete.",
        )
