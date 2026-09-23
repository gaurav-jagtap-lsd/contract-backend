from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from core.exceptions import success_response, error_response
from core.roles import require
from core.audit_service import log_action, get_client_ip, ACTIONS
from core.firestore_utils import (
    create_doc, get_doc, update_doc, delete_doc,
    query_collection, serialize_firestore_doc,
    CONTRACTS_COL, CONTRACT_VERSIONS_COL, CLIENTS_COL, SNOOZES_COL,
    now_utc,
)
from core.firebase import get_firestore_client
from core.storage_service import upload_contract_file, get_signed_url, delete_file

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

CONTRACT_STATUSES = ("active", "paused", "expiring_soon", "expired", "renewed", "archived")
PIPELINE_STEPS = (
    "Initiated",
    "Commercial Shared",
    "Negotiation",
    "Approval",
    "SOW/Draft shared",
    "Signed",
)
DEFAULT_PIPELINE_STEP = "Initiated"


import re

def _is_iso_date(val: str) -> bool:
    if not val or not isinstance(val, str):
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}", val.strip()))


def _calculate_contract_dates(services: list, fallback_start: str = "", fallback_end: str = "") -> tuple[str, str]:
    start_dates = []
    end_dates = []
    for s in services:
        if isinstance(s, dict):
            s_start = str(s.get("start_date") or "").strip()
            s_end = str(s.get("end_date") or "").strip()
            if _is_iso_date(s_start):
                start_dates.append(s_start[:10])
            if _is_iso_date(s_end):
                end_dates.append(s_end[:10])
    calc_start = min(start_dates) if start_dates else (fallback_start or "")
    calc_end = max(end_dates) if end_dates else (fallback_end or "")
    return calc_start, calc_end


def _calculate_days_remaining(end_date_str: str) -> int | None:
    if not end_date_str:
        return None
    try:
        end = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
        delta = end - datetime.now(timezone.utc)
        return delta.days
    except Exception:
        return None


def _resolve_status(contract: dict) -> str:
    if contract.get("status") in ("archived", "renewed"):
        return contract["status"]
    if contract.get("is_paused") or contract.get("client_paused"):
        return "paused"
    days = _calculate_days_remaining(contract.get("end_date"))
    if days is None:
        return contract.get("status", "active")
    if days < 0:
        return "expired"
    if days <= 30:
        return "expiring_soon"
    return "active"


def _search_haystack(contract: dict, client: dict | None = None) -> str:
    """Flatten searchable contract (and optional client) fields into one string."""
    parts: list[str] = []

    for key in (
        "contract_name",
        "vendor_name",
        "service_name",
        "service_type",
        "client_name",
        "notes",
        "file_name",
        "status",
        "pipeline_step",
    ):
        val = contract.get(key)
        if val:
            parts.append(str(val))

    computed = contract.get("computed_status") or ""
    if computed:
        parts.append(str(computed))
        parts.append(str(computed).replace("_", " "))

    emails = contract.get("email_ids") or []
    if isinstance(emails, list):
        parts.extend(str(e) for e in emails if e)
    elif emails:
        parts.append(str(emails))

    for service in contract.get("services") or []:
        if not isinstance(service, dict):
            continue
        for skey in ("service_name", "service_type", "description"):
            sval = service.get(skey)
            if sval:
                parts.append(str(sval))

    if client:
        for key in (
            "client_name",
            "account_manager",
            "primary_reminder_email",
            "secondary_reminder_email",
        ):
            val = client.get(key)
            if val:
                parts.append(str(val))
        cc_emails = client.get("cc_emails") or []
        if isinstance(cc_emails, list):
            parts.extend(str(e) for e in cc_emails if e)

    return " ".join(parts).lower()


def _contract_matches_search(contract: dict, term: str, client: dict | None = None) -> bool:
    """Return True if every whitespace-separated search token appears in the haystack."""
    tokens = [t for t in (term or "").strip().lower().split() if t]
    if not tokens:
        return True
    haystack = _search_haystack(contract, client)
    return all(token in haystack for token in tokens)


class ContractListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        uid = request.user.uid
        status_filter = request.query_params.get("status")
        client_filter = request.query_params.get("client_id")
        search_term = (
            request.query_params.get("search")
            or request.query_params.get("q")
            or ""
        ).strip()

        filters = [("is_deleted", "==", False)]
        if client_filter:
            filters.append(("client_id", "==", client_filter))

        contracts = query_collection(
            CONTRACTS_COL, filters=filters,
            order_by="created_at", direction="DESCENDING",
        )

        client_by_id: dict = {}
        if search_term:
            clients = query_collection(
                CLIENTS_COL,
                filters=[("is_deleted", "==", False)],
            )
            client_by_id = {cl["id"]: cl for cl in clients}

        # Enrich with live status and days remaining
        result = []
        for c in contracts:
            c["days_remaining"] = _calculate_days_remaining(c.get("end_date"))
            c["computed_status"] = _resolve_status(c)
            if status_filter and c["computed_status"] != status_filter:
                continue
            if search_term and not _contract_matches_search(
                c, search_term, client_by_id.get(c.get("client_id"))
            ):
                continue
            if not c.get("pipeline_step"):
                c["pipeline_step"] = DEFAULT_PIPELINE_STEP
            result.append(serialize_firestore_doc(c))

        return success_response(data={"contracts": result, "total": len(result)})

    def post(self, request):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        data = request.data

        services = data.get("services", [])
        if isinstance(services, str):
            import json
            try:
                services = json.loads(services)
            except Exception:
                services = []

        calc_start, calc_end = _calculate_contract_dates(
            services,
            fallback_start=data.get("start_date", ""),
            fallback_end=data.get("end_date", ""),
        )

        required = ["contract_name", "client_id"]
        for field in required:
            if not data.get(field):
                return error_response(f"{field} is required.")

        if not calc_end:
            return error_response("end_date is required (either at contract level or on services).")

        # Verify client belongs to this user
        client = get_doc(CLIENTS_COL, data["client_id"])
        if not client or client.get("is_deleted"):
            return error_response("Client not found.", 404)

        service_name = data.get("service_name", "").strip()
        service_type = data.get("service_type", "").strip()
        if not service_name and services and isinstance(services[0], dict):
            service_name = services[0].get("service_name", "").strip()
        if not service_type and services and isinstance(services[0], dict):
            service_type = services[0].get("service_type", "").strip()

        payload = {
            "owner_uid": uid,
            "client_id": data["client_id"],
            "client_name": client.get("client_name", ""),
            "contract_name": data.get("contract_name", "").strip(),
            "vendor_name": data.get("vendor_name", "").strip(),
            "service_name": service_name,
            "service_type": service_type,
            "start_date": calc_start,
            "end_date": calc_end,
            "effective_date": data.get("effective_date", ""),
            "agreement_date": data.get("agreement_date", ""),
            "scope_date": data.get("scope_date", ""),
            "schedule_date": data.get("schedule_date", ""),
            "annexure_date": data.get("annexure_date", ""),
            "execution_date": data.get("execution_date", ""),
            "lock_in_period": data.get("lock_in_period", ""),
            "renewal_clause": data.get("renewal_clause", ""),
            "notice_period": data.get("notice_period", ""),
            "email_ids": data.get("email_ids", []),
            "services": services,
            "status": "active",
            "is_paused": False,
            "pause_reason": "",
            "paused_by": "",
            "paused_at": None,
            "client_paused": False,
            "is_snoozed": False,
            "snooze_until": None,
            "storage_path": data.get("storage_path", ""),
            "file_name": data.get("file_name", ""),
            "version": 1,
            "parent_contract_id": None,
            "is_deleted": False,
            "notes": data.get("notes", ""),
            "pipeline_step": (
                data.get("pipeline_step")
                if data.get("pipeline_step") in PIPELINE_STEPS
                else DEFAULT_PIPELINE_STEP
            ),
        }

        contract = create_doc(CONTRACTS_COL, payload)
        log_action(
            user_uid=uid,
            action=ACTIONS["UPLOAD"],
            resource_type="contract",
            resource_id=contract["id"],
            description=f"Created contract: {payload['contract_name']}",
            ip_address=get_client_ip(request),
        )
        return success_response(
            data={"contract": serialize_firestore_doc(contract)},
            message="Contract created.",
            status_code=201,
        )


class ContractDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_or_404(self, contract_id, uid):
        c = get_doc(CONTRACTS_COL, contract_id)
        if not c or c.get("is_deleted"):
            return None
        return c

    def get(self, request, contract_id):
        uid = request.user.uid
        contract = self._get_or_404(contract_id, uid)
        if not contract:
            return error_response("Contract not found.", 404)
        contract["days_remaining"] = _calculate_days_remaining(contract.get("end_date"))
        contract["computed_status"] = _resolve_status(contract)
        if not contract.get("pipeline_step"):
            contract["pipeline_step"] = DEFAULT_PIPELINE_STEP

        # Get signed URL if file exists
        if contract.get("storage_path"):
            try:
                contract["file_url"] = get_signed_url(contract["storage_path"])
            except Exception:
                contract["file_url"] = None

        return success_response(data={"contract": serialize_firestore_doc(contract)})

    def patch(self, request, contract_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        contract = self._get_or_404(contract_id, uid)
        if not contract:
            return error_response("Contract not found.", 404)

        editable_fields = [
            "contract_name", "client_id", "vendor_name", "service_name", "service_type",
            "start_date", "end_date", "effective_date", "agreement_date",
            "scope_date", "schedule_date", "annexure_date", "execution_date",
            "lock_in_period", "renewal_clause", "notice_period",
            "email_ids", "services", "notes", "pipeline_step",
        ]
        updates = {k: v for k, v in request.data.items() if k in editable_fields}
        if not updates:
            return error_response("No valid fields to update.")

        if "pipeline_step" in updates and updates["pipeline_step"] not in PIPELINE_STEPS:
            return error_response("Invalid pipeline_step.")

        if "client_id" in updates and updates["client_id"] != contract.get("client_id"):
            client = get_doc(CLIENTS_COL, updates["client_id"])
            if not client or client.get("is_deleted"):
                return error_response("Client not found.", 404)
            updates["client_name"] = client.get("client_name", "")

        if "services" in updates:
            services = updates["services"]
            if isinstance(services, str):
                import json
                try:
                    services = json.loads(services)
                    updates["services"] = services
                except Exception:
                    services = []

            calc_start, calc_end = _calculate_contract_dates(
                services,
                fallback_start=updates.get("start_date") or contract.get("start_date", ""),
                fallback_end=updates.get("end_date") or contract.get("end_date", ""),
            )
            if calc_start:
                updates["start_date"] = calc_start
            if calc_end:
                updates["end_date"] = calc_end
            if services and isinstance(services[0], dict):
                if not updates.get("service_name"):
                    updates["service_name"] = services[0].get("service_name", "")
                if not updates.get("service_type"):
                    updates["service_type"] = services[0].get("service_type", "")

        update_doc(CONTRACTS_COL, contract_id, updates)
        log_action(
            user_uid=uid,
            action=ACTIONS["EDIT"],
            resource_type="contract",
            resource_id=contract_id,
            description=f"Edited contract: {contract.get('contract_name')}",
            metadata={"fields": list(updates.keys())},
            ip_address=get_client_ip(request),
        )
        updated = get_doc(CONTRACTS_COL, contract_id)
        return success_response(data={"contract": serialize_firestore_doc(updated)})

    def delete(self, request, contract_id):
        denied = require(request, "delete")
        if denied:
            return denied
        uid = request.user.uid
        contract = self._get_or_404(contract_id, uid)
        if not contract:
            return error_response("Contract not found.", 404)

        update_doc(CONTRACTS_COL, contract_id, {"is_deleted": True, "status": "archived"})
        log_action(
            user_uid=uid,
            action=ACTIONS["DELETE"],
            resource_type="contract",
            resource_id=contract_id,
            description=f"Deleted contract: {contract.get('contract_name')}",
            ip_address=get_client_ip(request),
        )
        return success_response(message="Contract deleted.")


class ContractUploadFileView(APIView):
    """Upload a contract file to Firebase Storage — returns storage_path for use in create/edit."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        file = request.FILES.get("file")
        if not file:
            return error_response("No file provided.")

        try:
            result = upload_contract_file(file, uid)
        except ValueError as exc:
            return error_response(str(exc), 400)
        except Exception as exc:
            logger.error(f"File upload error: {exc}")
            return error_response("File upload failed.", 500)

        log_action(
            user_uid=uid,
            action=ACTIONS["UPLOAD"],
            resource_type="file",
            resource_id=result["storage_path"],
            description=f"Uploaded file: {result['file_name']}",
            ip_address=get_client_ip(request),
        )
        return success_response(data=result, message="File uploaded.", status_code=201)


class ContractPauseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, contract_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract or contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        pause_reason = request.data.get("pause_reason", "").strip()
        if not pause_reason:
            return error_response("pause_reason is required.")

        update_doc(CONTRACTS_COL, contract_id, {
            "is_paused": True,
            "status": "paused",
            "pause_reason": pause_reason,
            "paused_by": uid,
            "paused_at": now_utc().isoformat(),
        })
        log_action(
            user_uid=uid,
            action=ACTIONS["PAUSE"],
            resource_type="contract",
            resource_id=contract_id,
            description=f"Paused contract: {contract.get('contract_name')}. Reason: {pause_reason}",
            ip_address=get_client_ip(request),
        )
        return success_response(message="Contract paused. Reminders stopped.")


class ContractResumeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, contract_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract or contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        update_doc(CONTRACTS_COL, contract_id, {
            "is_paused": False,
            "status": "active",
            "pause_reason": "",
            "paused_by": "",
            "paused_at": None,
        })
        log_action(
            user_uid=uid,
            action=ACTIONS["RESUME"],
            resource_type="contract",
            resource_id=contract_id,
            description=f"Resumed contract: {contract.get('contract_name')}",
            ip_address=get_client_ip(request),
        )
        return success_response(message="Contract resumed.")


class ContractSnoozeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, contract_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract or contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        snooze_days = request.data.get("snooze_days")
        custom_date = request.data.get("custom_date")

        if custom_date:
            try:
                snooze_until = datetime.fromisoformat(custom_date).replace(tzinfo=timezone.utc)
            except ValueError:
                return error_response("Invalid custom_date format. Use ISO 8601.")
        elif snooze_days:
            valid_days = [7, 15, 30]
            try:
                snooze_days = int(snooze_days)
            except ValueError:
                return error_response("snooze_days must be a number.")
            snooze_until = datetime.now(timezone.utc) + timedelta(days=snooze_days)
        else:
            return error_response("Provide snooze_days (7, 15, 30) or custom_date.")

        if snooze_until <= datetime.now(timezone.utc):
            return error_response("Snooze date must be in the future.")

        update_doc(CONTRACTS_COL, contract_id, {
            "is_snoozed": True,
            "snooze_until": snooze_until.isoformat(),
        })
        log_action(
            user_uid=uid,
            action=ACTIONS["SNOOZE"],
            resource_type="contract",
            resource_id=contract_id,
            description=f"Snoozed contract until {snooze_until.date()}",
            ip_address=get_client_ip(request),
        )
        return success_response(
            data={"snooze_until": snooze_until.isoformat()},
            message=f"Reminders snoozed until {snooze_until.strftime('%d %b %Y')}.",
        )


class ContractUnsnoozeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, contract_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract or contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        update_doc(CONTRACTS_COL, contract_id, {
            "is_snoozed": False,
            "snooze_until": None,
        })
        log_action(
            user_uid=uid,
            action=ACTIONS["UNSNOOZE"],
            resource_type="contract",
            resource_id=contract_id,
            description="Unsnoozed contract",
            ip_address=get_client_ip(request),
        )
        return success_response(message="Snooze removed. Reminders active.")


class ContractRenewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, contract_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        old_contract = get_doc(CONTRACTS_COL, contract_id)
        if not old_contract or old_contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        new_end_date = request.data.get("new_end_date")
        if not new_end_date:
            return error_response("new_end_date is required.")

        # Archive old version
        update_doc(CONTRACTS_COL, contract_id, {
            "status": "renewed",
            "is_paused": False,
            "is_snoozed": False,
        })

        # Save old contract as a version snapshot
        version_snapshot = {**old_contract, "original_contract_id": contract_id}
        version_snapshot.pop("id", None)
        create_doc(CONTRACT_VERSIONS_COL, version_snapshot)

        # Create new version
        new_payload = {
            **old_contract,
            "end_date": new_end_date,
            "start_date": request.data.get("new_start_date", old_contract.get("end_date", "")),
            "status": "active",
            "is_paused": False,
            "pause_reason": "",
            "paused_by": "",
            "paused_at": None,
            "client_paused": False,
            "is_snoozed": False,
            "snooze_until": None,
            "version": old_contract.get("version", 1) + 1,
            "parent_contract_id": contract_id,
            "notes": request.data.get("notes", old_contract.get("notes", "")),
            "storage_path": request.data.get("storage_path", old_contract.get("storage_path", "")),
        }
        new_payload.pop("id", None)
        new_payload.pop("created_at", None)
        new_payload.pop("updated_at", None)

        new_contract = create_doc(CONTRACTS_COL, new_payload)

        # Link new contract ID back to old one
        update_doc(CONTRACTS_COL, contract_id, {
            "renewed_contract_id": new_contract["id"]
        })

        log_action(
            user_uid=uid,
            action=ACTIONS["RENEW"],
            resource_type="contract",
            resource_id=new_contract["id"],
            description=f"Renewed contract: {old_contract.get('contract_name')} → v{new_payload['version']}",
            metadata={"old_contract_id": contract_id, "new_end_date": new_end_date},
            ip_address=get_client_ip(request),
        )
        return success_response(
            data={"new_contract": serialize_firestore_doc(new_contract)},
            message="Contract renewed. New version created.",
            status_code=201,
        )


class ContractVersionHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, contract_id):
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract:
            return error_response("Contract not found.", 404)

        versions = query_collection(
            CONTRACT_VERSIONS_COL,
            filters=[("original_contract_id", "==", contract_id)],
            order_by="created_at",
            direction="DESCENDING",
        )
        return success_response(data={
            "versions": [serialize_firestore_doc(v) for v in versions],
            "total": len(versions),
        })


class ContractFileUrlView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, contract_id):
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract or contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        storage_path = contract.get("storage_path")
        if not storage_path:
            return error_response("No file attached to this contract.", 404)

        try:
            url = get_signed_url(storage_path, expiry_hours=2)
        except FileNotFoundError:
            return error_response("File not found in storage.", 404)

        return success_response(data={"url": url, "expires_in_seconds": 7200})


# ─── Universal Contract Comments ─────────────────────────────────────────────

CONTRACT_COMMENTS_COL = "contract_comments"


class ContractCommentsView(APIView):
    """
    GET  /api/contracts/<id>/comments/  — list all comments for a contract
    POST /api/contracts/<id>/comments/  — add a new comment
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, contract_id):
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract or contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        # Single field filter on contract_id to avoid Firestore composite index requirements
        comments = query_collection(
            CONTRACT_COMMENTS_COL,
            filters=[("contract_id", "==", contract_id)],
        )

        serialized = [
            serialize_firestore_doc(c)
            for c in comments
        ]
        serialized.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)

        return success_response(data={
            "comments": serialized,
            "total": len(serialized),
        })


    def post(self, request, contract_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract or contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        text = (request.data.get("text") or "").strip()
        if not text:
            return error_response("Comment text is required.")
        if len(text) > 2000:
            return error_response("Comment must be 2000 characters or fewer.")

        # Use explicit UTC ISO strings instead of SERVER_TIMESTAMP so the
        # returned comment dict is immediately JSON-serializable.
        ts = now_utc().isoformat()
        db = get_firestore_client()
        data = {
            "contract_id": contract_id,
            "owner_uid": uid,
            "contract_name": contract.get("contract_name", ""),
            "text": text,
            "author": uid,
            "created_at": ts,
            "updated_at": ts,
        }
        _, doc_ref = db.collection(CONTRACT_COMMENTS_COL).add(data)
        comment = {**data, "id": doc_ref.id}

        log_action(
            user_uid=uid,
            action="COMMENT",
            resource_type="contract",
            resource_id=contract_id,
            description=f"Comment added to: {contract.get('contract_name')}",
            ip_address=get_client_ip(request),
        )

        return success_response(
            data={"comment": comment},
            message="Comment added.",
            status_code=201,
        )

    def delete(self, request, contract_id):
        """Delete a specific comment by comment_id passed in request body."""
        denied = require(request, "delete")
        if denied:
            return denied
        uid = request.user.uid
        comment_id = request.data.get("comment_id")
        if not comment_id:
            return error_response("comment_id is required.")

        comment = get_doc(CONTRACT_COMMENTS_COL, comment_id)
        if not comment or comment.get("contract_id") != contract_id:
            return error_response("Comment not found.", 404)

        delete_doc(CONTRACT_COMMENTS_COL, comment_id)
        return success_response(message="Comment deleted.")

