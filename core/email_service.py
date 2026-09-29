from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
import logging

logger = logging.getLogger(__name__)


def send_email(
    to_emails: list[str],
    subject: str,
    html_content: str,
    cc_emails: list[str] = None,
    reply_to: str = None,
) -> bool:
    """Send an email via configured SMTP server. Returns True on success."""

    to_emails = [email for email in to_emails if email]

    if not to_emails:
        logger.warning("send_email called with no valid recipients.")
        return False

    cc_emails = [email for email in (cc_emails or []) if email]

    try:
        email = EmailMultiAlternatives(
            subject=subject,
            body="Please view this email in an HTML-compatible email client.",
            from_email=None,
            to=to_emails,
            cc=cc_emails,
            reply_to=[reply_to] if reply_to else None,
        )

        email.attach_alternative(html_content, "text/html")

        email.send()

        logger.info(
            f"Email sent to {to_emails}: {subject}"
        )

        return True

    except Exception as e:
        logger.error(
            f"SMTP error sending email to {to_emails}: {e}"
        )
        return False


def send_reminder_email(
    contract: dict,
    client: dict,
    days_remaining: int,
) -> bool:
    """Render and send a contract reminder email."""

    context = {
        "contract_name": contract.get(
            "contract_name",
            "Unnamed Contract",
        ),
        "client_name": client.get(
            "client_name",
            "",
        ),
        "vendor_name": contract.get(
            "vendor_name",
            "",
        ),
        "end_date": contract.get(
            "end_date",
            "",
        ),
        "days_remaining": days_remaining,
        "renewal_clause": contract.get(
            "renewal_clause",
            "",
        ),
        "notice_period": contract.get(
            "notice_period",
            "",
        ),
        "frontend_url": settings.FRONTEND_URL,
        "contract_id": contract.get(
            "id",
            "",
        ),
    }

    if days_remaining <= 0:
        subject = (
            f"⚠️ CONTRACT EXPIRED: "
            f"{context['contract_name']}"
        )
        template = "emails/reminder_expired.html"

    elif days_remaining <= 30:
        subject = (
            f"🔴 Urgent: "
            f"{context['contract_name']} "
            f"expires in {days_remaining} days"
        )
        template = "emails/reminder_30.html"

    else:
        subject = (
            f"🟡 Reminder: "
            f"{context['contract_name']} "
            f"expires in {days_remaining} days"
        )
        template = "emails/reminder_60.html"

    html_content = render_to_string(
        template,
        context,
    )

    recipients = []

    if client.get("primary_reminder_email"):
        recipients.append(
            client["primary_reminder_email"]
        )

    if client.get("secondary_reminder_email"):
        recipients.append(
            client["secondary_reminder_email"]
        )

    cc_list = client.get(
        "cc_emails",
        [],
    )

    if not recipients:
        logger.warning(
            f"No recipients for contract "
            f"{contract.get('id')}"
        )
        return False

    return send_email(
        to_emails=recipients,
        subject=subject,
        html_content=html_content,
        cc_emails=cc_list,
    )