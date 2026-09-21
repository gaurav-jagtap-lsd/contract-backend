from celery import shared_task
from core.firestore_utils import (
    query_collection, get_doc, update_doc, create_doc,
    CONTRACTS_COL, CLIENTS_COL, REMINDERS_COL, now_utc,
)
from core.email_service import send_reminder_email
from core.audit_service import log_action, ACTIONS
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger(__name__)


def _days_remaining(end_date_str: str):
    if not end_date_str:
        return None
    try:
        end = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
        return (end - datetime.now(timezone.utc)).days
    except Exception:
        return None


def _should_send_today(contract: dict, days: int, reminder_log: dict) -> bool:
    """
    Reminder frequency rules:
    - 31–60 days: weekly (every 7 days)
    - 1–30 days: every 2 days
    - 0 or negative (expired): daily for 7 days after expiry, then stop
    """
    now = now_utc()
    last_sent_str = reminder_log.get("last_sent_at")

    if last_sent_str:
        try:
            last_sent = datetime.fromisoformat(last_sent_str).replace(tzinfo=timezone.utc)
        except Exception:
            last_sent = None
    else:
        last_sent = None

    # Expired: send daily but only for 7 days post-expiry
    if days <= 0:
        end_date_str = contract.get("end_date", "")
        if end_date_str:
            try:
                end_dt = datetime.fromisoformat(end_date_str).replace(tzinfo=timezone.utc)
                days_since_expiry = (now - end_dt).days
                if days_since_expiry > 7:
                    return False  # Stop after 7 days
            except Exception:
                pass
        if last_sent is None:
            return True
        return (now - last_sent).days >= 1

    # 1–30 days: every 2 days
    if days <= 30:
        if last_sent is None:
            return True
        return (now - last_sent).days >= 2

    # 31–60 days: weekly
    if days <= 60:
        if last_sent is None:
            return True
        return (now - last_sent).days >= 7

    return False


def _get_or_create_reminder_log(contract_id: str) -> dict:
    logs = query_collection(
        REMINDERS_COL,
        filters=[("contract_id", "==", contract_id)],
        limit=1,
    )
    if logs:
        return logs[0]
    return {"contract_id": contract_id, "last_sent_at": None, "total_sent": 0}


@shared_task(name="apps.reminders.tasks.dispatch_reminders")
def dispatch_reminders():
    """
    Main reminder dispatch — runs daily at 08:00 UTC via Celery Beat.
    Iterates all active contracts, applies frequency rules, sends emails.
    """
    now = now_utc()
    contracts = query_collection(
        CONTRACTS_COL,
        filters=[("is_deleted", "==", False)],
    )

    sent_count = 0
    skipped_count = 0

    for contract in contracts:
        cid = contract["id"]

        # Skip paused (contract-level or client-level)
        if contract.get("is_paused") or contract.get("client_paused"):
            skipped_count += 1
            continue

        # Skip archived / renewed
        if contract.get("status") in ("archived", "renewed"):
            skipped_count += 1
            continue

        # Skip snoozed
        if contract.get("is_snoozed"):
            snooze_until = contract.get("snooze_until")
            if snooze_until:
                try:
                    snooze_dt = datetime.fromisoformat(snooze_until).replace(tzinfo=timezone.utc)
                    if now < snooze_dt:
                        skipped_count += 1
                        continue
                except Exception:
                    pass

        days = _days_remaining(contract.get("end_date"))

        # Only send reminders for contracts expiring within 60 days or already expired
        if days is None or days > 60:
            skipped_count += 1
            continue

        reminder_log = _get_or_create_reminder_log(cid)

        if not _should_send_today(contract, days, reminder_log):
            skipped_count += 1
            continue

        # Get client for email addresses
        client_id = contract.get("client_id")
        if not client_id:
            skipped_count += 1
            continue

        client = get_doc(CLIENTS_COL, client_id)
        if not client or client.get("is_deleted") or client.get("is_paused"):
            skipped_count += 1
            continue

        # Send email
        success = send_reminder_email(contract, client, days)

        if success:
            # Update or create reminder log
            log_entry = {
                "contract_id": cid,
                "contract_name": contract.get("contract_name"),
                "client_id": client_id,
                "owner_uid": contract.get("owner_uid"),
                "last_sent_at": now.isoformat(),
                "days_remaining_when_sent": days,
                "total_sent": reminder_log.get("total_sent", 0) + 1,
            }

            if "id" in reminder_log and reminder_log["id"]:
                update_doc(REMINDERS_COL, reminder_log["id"], log_entry)
            else:
                create_doc(REMINDERS_COL, log_entry)

            log_action(
                user_uid=contract.get("owner_uid", "system"),
                action=ACTIONS["REMINDER_SENT"],
                resource_type="contract",
                resource_id=cid,
                description=f"Reminder sent for '{contract.get('contract_name')}' — {days} days remaining.",
                metadata={"days_remaining": days, "recipients": client.get("primary_reminder_email")},
            )
            sent_count += 1
        else:
            logger.warning(f"Failed to send reminder for contract {cid}")

    logger.info(f"dispatch_reminders complete: sent={sent_count}, skipped={skipped_count}")
    return {"sent": sent_count, "skipped": skipped_count}


@shared_task(name="apps.reminders.tasks.send_single_reminder")
def send_single_reminder(contract_id: str):
    """Manually trigger a reminder for a single contract (called from API)."""
    contract = get_doc(CONTRACTS_COL, contract_id)
    if not contract:
        return {"error": "Contract not found."}

    client = get_doc(CLIENTS_COL, contract.get("client_id", ""))
    if not client:
        return {"error": "Client not found."}

    days = _days_remaining(contract.get("end_date"))
    success = send_reminder_email(contract, client, days or 0)
    return {"sent": success, "days_remaining": days}
