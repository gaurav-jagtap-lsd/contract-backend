from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from core.exceptions import success_response, error_response
from core.roles import require
from core.firestore_utils import query_collection, serialize_firestore_doc, AUDIT_LOGS_COL


class AuditLogListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        denied = require(request, "write")
        if denied:
            return denied
        action_filter = request.query_params.get("action")
        resource_filter = request.query_params.get("resource_type")

        logs = query_collection(
            AUDIT_LOGS_COL,
            order_by="created_at",
            direction="DESCENDING",
            limit=200,
        )
        if action_filter:
            logs = [item for item in logs if item.get("action") == action_filter]
        if resource_filter:
            logs = [item for item in logs if item.get("resource_type") == resource_filter]
        return success_response(data={
            "logs": [serialize_firestore_doc(l) for l in logs],
            "total": len(logs),
        })
