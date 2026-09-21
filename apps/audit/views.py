from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from core.exceptions import success_response
from core.firestore_utils import query_collection, serialize_firestore_doc, AUDIT_LOGS_COL


class AuditLogListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        uid = request.user.uid
        action_filter = request.query_params.get("action")
        resource_filter = request.query_params.get("resource_type")

        filters = [("user_uid", "==", uid)]
        if action_filter:
            filters.append(("action", "==", action_filter))
        if resource_filter:
            filters.append(("resource_type", "==", resource_filter))

        logs = query_collection(
            AUDIT_LOGS_COL,
            filters=filters,
            order_by="created_at",
            direction="DESCENDING",
            limit=200,
        )
        return success_response(data={
            "logs": [serialize_firestore_doc(l) for l in logs],
            "total": len(logs),
        })
