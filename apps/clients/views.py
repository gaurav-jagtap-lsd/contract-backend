from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from core.exceptions import success_response, error_response
from core.roles import require
from core.audit_service import log_action, get_client_ip, ACTIONS
from core.firestore_utils import (
    create_doc, get_doc, update_doc, delete_doc,
    query_collection, CLIENTS_COL, CONTRACTS_COL
)
import logging

logger = logging.getLogger(__name__)


class ClientListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        uid = request.user.uid
        clients = query_collection(
            CLIENTS_COL,
            filters=[("is_deleted", "==", False)],
            order_by="created_at",
            direction="DESCENDING",
        )
        from core.firestore_utils import serialize_firestore_doc
        clients = [serialize_firestore_doc(c) for c in clients]
        return success_response(data={"clients": clients, "total": len(clients)})

    def post(self, request):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        data = request.data

        client_name = data.get("client_name", "").strip()
        if not client_name:
            return error_response("client_name is required.")

        cc_emails = data.get("cc_emails", [])
        if isinstance(cc_emails, str):
            cc_emails = [e.strip() for e in cc_emails.split(",") if e.strip()]

        payload = {
            "owner_uid": uid,
            "client_name": client_name,
            "account_manager": data.get("account_manager", "").strip(),
            "primary_reminder_email": data.get("primary_reminder_email", "").strip(),
            "secondary_reminder_email": data.get("secondary_reminder_email", "").strip(),
            "cc_emails": cc_emails,
            "notes": data.get("notes", "").strip(),
            "is_paused": False,
            "pause_reason": "",
            "paused_by": "",
            "paused_at": None,
            "is_deleted": False,
        }

        client = create_doc(CLIENTS_COL, payload)
        log_action(
            user_uid=uid,
            action=ACTIONS["CLIENT_CREATE"],
            resource_type="client",
            resource_id=client["id"],
            description=f"Created client: {client_name}",
            ip_address=get_client_ip(request),
        )
        from core.firestore_utils import serialize_firestore_doc
        return success_response(
            data={"client": serialize_firestore_doc(client)},
            message="Client created.",
            status_code=201,
        )


class ClientDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_client_or_404(self, client_id, uid):
        client = get_doc(CLIENTS_COL, client_id)
        if not client or client.get("is_deleted"):
            return None
        return client

    def get(self, request, client_id):
        uid = request.user.uid
        client = self._get_client_or_404(client_id, uid)
        if not client:
            return error_response("Client not found.", 404)
        from core.firestore_utils import serialize_firestore_doc
        return success_response(data={"client": serialize_firestore_doc(client)})

    def patch(self, request, client_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        client = self._get_client_or_404(client_id, uid)
        if not client:
            return error_response("Client not found.", 404)

        allowed_fields = [
            "client_name", "account_manager", "primary_reminder_email",
            "secondary_reminder_email", "cc_emails", "notes",
        ]
        updates = {k: v for k, v in request.data.items() if k in allowed_fields}

        if "cc_emails" in updates and isinstance(updates["cc_emails"], str):
            updates["cc_emails"] = [e.strip() for e in updates["cc_emails"].split(",") if e.strip()]

        if not updates:
            return error_response("No valid fields to update.")

        update_doc(CLIENTS_COL, client_id, updates)
        log_action(
            user_uid=uid,
            action=ACTIONS["CLIENT_UPDATE"],
            resource_type="client",
            resource_id=client_id,
            description=f"Updated client: {client.get('client_name')}",
            metadata={"fields": list(updates.keys())},
            ip_address=get_client_ip(request),
        )
        updated = get_doc(CLIENTS_COL, client_id)
        from core.firestore_utils import serialize_firestore_doc
        return success_response(data={"client": serialize_firestore_doc(updated)})

    def delete(self, request, client_id):
        denied = require(request, "delete")
        if denied:
            return denied
        uid = request.user.uid
        client = self._get_client_or_404(client_id, uid)
        if not client:
            return error_response("Client not found.", 404)

        # Soft delete
        update_doc(CLIENTS_COL, client_id, {"is_deleted": True})
        log_action(
            user_uid=uid,
            action=ACTIONS["CLIENT_DELETE"],
            resource_type="client",
            resource_id=client_id,
            description=f"Deleted client: {client.get('client_name')}",
            ip_address=get_client_ip(request),
        )
        return success_response(message="Client deleted.")


class ClientPauseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, client_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        client = get_doc(CLIENTS_COL, client_id)
        if not client or client.get("is_deleted"):
            return error_response("Client not found.", 404)

        pause_reason = request.data.get("pause_reason", "").strip()
        if not pause_reason:
            return error_response("pause_reason is required.")

        from core.firestore_utils import now_utc
        update_doc(CLIENTS_COL, client_id, {
            "is_paused": True,
            "pause_reason": pause_reason,
            "paused_by": uid,
            "paused_at": now_utc().isoformat(),
        })

        # Pause all active contracts for this client
        contracts = query_collection(
            CONTRACTS_COL,
            filters=[("client_id", "==", client_id), ("is_deleted", "==", False)],
        )
        for contract in contracts:
            if contract.get("status") not in ("archived", "expired"):
                update_doc(CONTRACTS_COL, contract["id"], {
                    "client_paused": True,
                })

        log_action(
            user_uid=uid,
            action=ACTIONS["CLIENT_PAUSE"],
            resource_type="client",
            resource_id=client_id,
            description=f"Paused client: {client.get('client_name')}. Reason: {pause_reason}",
            ip_address=get_client_ip(request),
        )
        return success_response(message="Client and all associated contracts paused.")


class ClientResumeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, client_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        client = get_doc(CLIENTS_COL, client_id)
        if not client or client.get("is_deleted"):
            return error_response("Client not found.", 404)

        update_doc(CLIENTS_COL, client_id, {
            "is_paused": False,
            "pause_reason": "",
            "paused_by": "",
            "paused_at": None,
        })

        # Resume contracts that were paused due to client pause
        contracts = query_collection(
            CONTRACTS_COL,
            filters=[("client_id", "==", client_id), ("client_paused", "==", True)],
        )
        for contract in contracts:
            update_doc(CONTRACTS_COL, contract["id"], {"client_paused": False})

        log_action(
            user_uid=uid,
            action=ACTIONS["CLIENT_RESUME"],
            resource_type="client",
            resource_id=client_id,
            description=f"Resumed client: {client.get('client_name')}",
            ip_address=get_client_ip(request),
        )
        return success_response(message="Client and all associated contracts resumed.")


class ClientEmailsView(APIView):
    """Returns saved email addresses for a client — used to pre-fill contract forms."""
    permission_classes = [IsAuthenticated]

    def get(self, request, client_id):
        uid = request.user.uid
        client = get_doc(CLIENTS_COL, client_id)
        if not client or client.get("is_deleted"):
            return error_response("Client not found.", 404)

        return success_response(data={
            "primary_reminder_email": client.get("primary_reminder_email", ""),
            "secondary_reminder_email": client.get("secondary_reminder_email", ""),
            "cc_emails": client.get("cc_emails", []),
        })
