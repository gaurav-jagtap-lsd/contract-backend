from datetime import datetime, timezone
from typing import Optional, Any
from core.firebase import get_firestore_client
from google.cloud.firestore_v1 import SERVER_TIMESTAMP


# ── Collection names ──────────────────────────────────────────────────────────
USERS_COL = "users"
CLIENTS_COL = "clients"
CONTRACTS_COL = "contracts"
CONTRACT_VERSIONS_COL = "contract_versions"
REMINDERS_COL = "reminders"
AUDIT_LOGS_COL = "audit_logs"
SNOOZES_COL = "snoozes"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def server_timestamp():
    return SERVER_TIMESTAMP


# ── Generic helpers ───────────────────────────────────────────────────────────

def get_doc(collection: str, doc_id: str) -> Optional[dict]:
    db = get_firestore_client()
    doc = db.collection(collection).document(doc_id).get()
    if doc.exists:
        data = doc.to_dict()
        data["id"] = doc.id
        return data
    return None


def set_doc(collection: str, doc_id: str, data: dict) -> dict:
    db = get_firestore_client()
    data["updated_at"] = server_timestamp()
    db.collection(collection).document(doc_id).set(data)
    return {**data, "id": doc_id}


def create_doc(collection: str, data: dict) -> dict:
    db = get_firestore_client()
    data["created_at"] = server_timestamp()
    data["updated_at"] = server_timestamp()
    _, doc_ref = db.collection(collection).add(data)
    return {**data, "id": doc_ref.id}


def update_doc(collection: str, doc_id: str, data: dict) -> None:
    db = get_firestore_client()
    data["updated_at"] = server_timestamp()
    db.collection(collection).document(doc_id).update(data)


def delete_doc(collection: str, doc_id: str) -> None:
    db = get_firestore_client()
    db.collection(collection).document(doc_id).delete()


def query_collection(
    collection: str,
    filters: list[tuple] = None,
    order_by: str = None,
    direction: str = "ASCENDING",
    limit: int = None,
) -> list[dict]:
    db = get_firestore_client()
    ref = db.collection(collection)

    if filters:
        for field, op, value in filters:
            ref = ref.where(field, op, value)

    if order_by:
        from google.cloud.firestore_v1 import Query
        dir_enum = Query.ASCENDING if direction == "ASCENDING" else Query.DESCENDING
        ref = ref.order_by(order_by, direction=dir_enum)

    if limit:
        ref = ref.limit(limit)

    docs = ref.stream()
    result = []
    for doc in docs:
        d = doc.to_dict()
        d["id"] = doc.id
        result.append(d)
    return result


def doc_exists(collection: str, doc_id: str) -> bool:
    db = get_firestore_client()
    return db.collection(collection).document(doc_id).get().exists


def serialize_firestore_doc(data: dict) -> dict:
    """Convert Firestore timestamps and other non-JSON-serializable types."""
    from google.cloud.firestore_v1.transforms import Sentinel

    result = {}
    for key, value in data.items():
        # Handle Sentinel (SERVER_TIMESTAMP placeholder) — not yet written to Firestore
        if isinstance(value, Sentinel):
            result[key] = now_utc().isoformat()
        # Handle Firestore Timestamp object
        elif hasattr(value, "seconds"):
            ts = datetime.fromtimestamp(value.seconds, tz=timezone.utc)
            result[key] = ts.isoformat()
        # Handle Python datetime
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        # Recurse into dicts
        elif isinstance(value, dict):
            result[key] = serialize_firestore_doc(value)
        # Recurse into lists
        elif isinstance(value, list):
            result[key] = [
                serialize_firestore_doc(v) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            result[key] = value
    return result