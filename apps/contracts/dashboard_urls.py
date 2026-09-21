from django.urls import path
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from core.exceptions import success_response
from core.firestore_utils import query_collection, CONTRACTS_COL, CLIENTS_COL
from core.firestore_utils import serialize_firestore_doc
from datetime import datetime, timezone
from collections import defaultdict


def _days_remaining(end_date_str):
    if not end_date_str:
        return None
    try:
        end = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
        return (end - datetime.now(timezone.utc)).days
    except Exception:
        return None


def _ph_days_elapsed(ph_approved_at_str):
    """Returns how many days have elapsed since PH approval."""
    if not ph_approved_at_str:
        return None
    try:
        approved = datetime.fromisoformat(ph_approved_at_str).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - approved).days
    except Exception:
        return None


class DashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        uid = request.user.uid

        clients = query_collection(CLIENTS_COL, filters=[
            ("owner_uid", "==", uid), ("is_deleted", "==", False)
        ])
        contracts = query_collection(CONTRACTS_COL, filters=[
            ("owner_uid", "==", uid), ("is_deleted", "==", False)
        ])

        total_clients = len(clients)
        total_contracts = len(contracts)
        active = paused = expiring_60 = expiring_30 = expired = 0
        ph_pending = 0  # PH approved but main contract not yet uploaded

        for c in contracts:
            # PH pending count — contracts with PH approval but no main contract yet
            if c.get("ph_approved") and not c.get("main_contract_uploaded"):
                ph_pending += 1

            if c.get("is_paused") or c.get("client_paused"):
                paused += 1
                continue
            days = _days_remaining(c.get("end_date"))
            if days is None:
                active += 1
            elif days < 0:
                expired += 1
            elif days <= 30:
                expiring_30 += 1
                expiring_60 += 1
            elif days <= 60:
                expiring_60 += 1
                active += 1
            else:
                active += 1

        return success_response(data={
            "total_clients": total_clients,
            "total_contracts": total_contracts,
            "active_contracts": active,
            "paused_contracts": paused,
            "expiring_in_60_days": expiring_60,
            "expiring_in_30_days": expiring_30,
            "expired_contracts": expired,
            "ph_approved_pending_main_contract": ph_pending,
        })


class DashboardChartsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        uid = request.user.uid
        contracts = query_collection(CONTRACTS_COL, filters=[
            ("owner_uid", "==", uid), ("is_deleted", "==", False)
        ])

        # Monthly expiry trend (next 12 months)
        monthly_expiry = defaultdict(int)
        service_type_dist = defaultdict(int)
        upcoming_renewals = []
        ph_pending_contracts = []

        for c in contracts:
            end_date = c.get("end_date", "")
            if end_date:
                try:
                    dt = datetime.fromisoformat(end_date)
                    key = dt.strftime("%Y-%m")
                    monthly_expiry[key] += 1
                except Exception:
                    pass

            stype = c.get("service_type") or "Other"
            service_type_dist[stype] += 1

            days = _days_remaining(end_date)
            if days is not None and 0 <= days <= 90:
                upcoming_renewals.append({
                    "id": c.get("id"),
                    "contract_name": c.get("contract_name"),
                    "client_name": c.get("client_name"),
                    "end_date": end_date,
                    "days_remaining": days,
                })

            # PH pending: approved but main contract not yet uploaded
            if c.get("ph_approved") and not c.get("main_contract_uploaded"):
                elapsed = _ph_days_elapsed(c.get("ph_approved_at"))
                days_left_in_30 = (30 - elapsed) if elapsed is not None else None
                ph_pending_contracts.append({
                    "id": c.get("id"),
                    "contract_name": c.get("contract_name"),
                    "client_name": c.get("client_name"),
                    "ph_approved_at": c.get("ph_approved_at"),
                    "ph_approval_type": c.get("ph_approval_type", ""),
                    "ph_approval_image_path": c.get("ph_approval_image_path", ""),
                    "days_elapsed": elapsed,
                    "days_remaining_in_30": days_left_in_30,
                    "is_overdue": elapsed is not None and elapsed > 30,
                })

        upcoming_renewals.sort(key=lambda x: x["days_remaining"])
        # Sort: overdue first, then by least days remaining
        ph_pending_contracts.sort(
            key=lambda x: (not x["is_overdue"], x["days_remaining_in_30"] if x["days_remaining_in_30"] is not None else 999)
        )

        return success_response(data={
            "monthly_expiry_trend": [
                {"month": k, "count": v}
                for k, v in sorted(monthly_expiry.items())
            ],
            "service_type_distribution": [
                {"type": k, "count": v}
                for k, v in service_type_dist.items()
            ],
            "upcoming_renewals": upcoming_renewals[:10],
            "ph_pending_contracts": ph_pending_contracts,
        })


class CalendarView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        uid = request.user.uid
        year = request.query_params.get("year", str(datetime.now().year))
        month = request.query_params.get("month", str(datetime.now().month))

        contracts = query_collection(CONTRACTS_COL, filters=[
            ("owner_uid", "==", uid), ("is_deleted", "==", False)
        ])

        events = []
        for c in contracts:
            end_date = c.get("end_date", "")
            if not end_date:
                continue
            try:
                dt = datetime.fromisoformat(end_date)
                if str(dt.year) != year or str(dt.month).zfill(2) != str(month).zfill(2):
                    continue
            except Exception:
                continue

            days = _days_remaining(end_date)
            if c.get("status") == "archived":
                color = "black"
            elif c.get("status") == "renewed":
                color = "blue"
            elif c.get("is_paused") or c.get("client_paused"):
                color = "gray"
            elif days is not None and days <= 30:
                color = "red"
            elif days is not None and days <= 60:
                color = "yellow"
            else:
                color = "green"

            events.append({
                "id": c.get("id"),
                "contract_name": c.get("contract_name"),
                "client_name": c.get("client_name"),
                "end_date": end_date,
                "days_remaining": days,
                "color": color,
                "status": c.get("status"),
            })

        return success_response(data={"events": events})


urlpatterns = [
    path("summary/", DashboardSummaryView.as_view(), name="dashboard-summary"),
    path("charts/", DashboardChartsView.as_view(), name="dashboard-charts"),
    path("calendar/", CalendarView.as_view(), name="dashboard-calendar"),
]
