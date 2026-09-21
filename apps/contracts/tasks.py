from celery import shared_task
from core.firestore_utils import (
    query_collection, update_doc, CONTRACTS_COL, now_utc
)
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def _days_remaining(end_date_str):
    if not end_date_str:
        return None
    try:
        end = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
        return (end - datetime.now(timezone.utc)).days
    except Exception:
        return None


@shared_task(name="apps.contracts.tasks.update_contract_statuses")
def update_contract_statuses():
    """
    Runs every hour via Celery Beat.
    Updates each contract's stored status field based on end_date.
    Does NOT update paused or archived contracts.
    Clears expired snoozes.
    """
    contracts = query_collection(
        CONTRACTS_COL,
        filters=[("is_deleted", "==", False)],
    )

    updated_count = 0
    now = now_utc()

    for contract in contracts:
        cid = contract["id"]
        current_status = contract.get("status", "active")

        # Never auto-update these terminal / manually set states
        if current_status in ("archived", "renewed", "paused"):
            continue
        if contract.get("is_paused") or contract.get("client_paused"):
            continue

        # Clear expired snooze
        snooze_until = contract.get("snooze_until")
        if contract.get("is_snoozed") and snooze_until:
            try:
                snooze_dt = datetime.fromisoformat(snooze_until).replace(tzinfo=timezone.utc)
                if now > snooze_dt:
                    update_doc(CONTRACTS_COL, cid, {
                        "is_snoozed": False,
                        "snooze_until": None,
                    })
            except Exception:
                pass

        days = _days_remaining(contract.get("end_date"))
        if days is None:
            continue

        if days < 0:
            new_status = "expired"
        elif days <= 30:
            new_status = "expiring_soon"
        else:
            new_status = "active"

        if new_status != current_status:
            update_doc(CONTRACTS_COL, cid, {"status": new_status})
            updated_count += 1

    logger.info(f"update_contract_statuses: updated {updated_count} contracts.")
    return {"updated": updated_count}
