from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from core.exceptions import success_response, error_response
from core.roles import require
from core.firestore_utils import query_collection, get_doc, serialize_firestore_doc, REMINDERS_COL, CONTRACTS_COL


class ReminderLogListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = require(request, "write")
        if denied:
            return denied
        logs = query_collection(
            REMINDERS_COL,
            order_by="last_sent_at",
            direction="DESCENDING",
            limit=100,
        )
        return success_response(data={
            "logs": [serialize_firestore_doc(l) for l in logs],
            "total": len(logs),
        })


class ManualReminderView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, contract_id):
        denied = require(request, "write")
        if denied:
            return denied
        uid = request.user.uid
        contract = get_doc(CONTRACTS_COL, contract_id)
        if not contract or contract.get("is_deleted"):
            return error_response("Contract not found.", 404)

        from apps.reminders.tasks import send_single_reminder
        try:
            # Try Celery queue first (requires Redis)
            task = send_single_reminder.delay(contract_id)
            return success_response(
                data={"task_id": task.id},
                message="Reminder queued.",
            )
        except Exception:
            # Redis not available — run task directly (synchronous fallback)
            try:
                send_single_reminder(contract_id)
                return success_response(
                    data={"task_id": None},
                    message="Reminder sent.",
                )
            except Exception as e:
                return error_response(f"Failed to send reminder: {str(e)}", 500)