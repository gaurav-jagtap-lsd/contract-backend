from core.firestore_utils import create_doc, AUDIT_LOGS_COL
from typing import Optional


ACTIONS = {
    "UPLOAD": "upload",
    "EDIT": "edit",
    "DELETE": "delete",
    "PAUSE": "pause",
    "RESUME": "resume",
    "RENEW": "renew",
    "LOGIN": "login",
    "LOGOUT": "logout",
    "REMINDER_SENT": "reminder_sent",
    "SNOOZE": "snooze",
    "UNSNOOZE": "unsnooze",
    "ARCHIVE": "archive",
    "CLIENT_CREATE": "client_create",
    "CLIENT_UPDATE": "client_update",
    "CLIENT_DELETE": "client_delete",
    "CLIENT_PAUSE": "client_pause",
    "CLIENT_RESUME": "client_resume",
    "VIEW": "view",
}


def log_action(
    user_uid: str,
    action: str,
    resource_type: str,
    resource_id: str,
    description: str = "",
    metadata: Optional[dict] = None,
    ip_address: str = "",
) -> str:
    """
    Write an audit log entry to Firestore.
    Returns the created document ID.
    """
    entry = {
        "user_uid": user_uid,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "description": description,
        "metadata": metadata or {},
        "ip_address": ip_address,
    }
    doc = create_doc(AUDIT_LOGS_COL, entry)
    return doc["id"]


def get_client_ip(request) -> str:
    x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded:
        return x_forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")
