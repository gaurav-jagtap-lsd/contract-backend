import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from django.conf import settings
from django.template.loader import render_to_string
import logging

logger = logging.getLogger(__name__)


def _get_api_instance():
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = settings.BREVO_API_KEY
    return sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )


def send_email(
    to_emails: list[str],
    subject: str,
    html_content: str,
    cc_emails: list[str] = None,
    reply_to: str = None,
) -> bool:
    """Send a transactional email via Brevo. Returns True on success."""
    api = _get_api_instance()

    to = [{"email": email} for email in to_emails if email]
    if not to:
        logger.warning("send_email called with no valid recipients.")
        return False

    params = {
        "to": to,
        "subject": subject,
        "html_content": html_content,
        "sender": {
            "email": settings.BREVO_SENDER_EMAIL,
            "name": settings.BREVO_SENDER_NAME,
        },
    }

    if cc_emails:
        params["cc"] = [{"email": e} for e in cc_emails if e]

    if reply_to:
        params["reply_to"] = {"email": reply_to}

    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(**params)

    try:
        api.send_transac_email(send_smtp_email)
        logger.info(f"Email sent to {to_emails}: {subject}")
        return True
    except ApiException as e:
        logger.error(f"Brevo API error sending to {to_emails}: {e}")
        return False


def send_reminder_email(contract: dict, client: dict, days_remaining: int) -> bool:
    """Render and send a contract reminder email."""
    context = {
        "contract_name": contract.get("contract_name", "Unnamed Contract"),
        "client_name": client.get("client_name", ""),
        "vendor_name": contract.get("vendor_name", ""),
        "end_date": contract.get("end_date", ""),
        "days_remaining": days_remaining,
        "renewal_clause": contract.get("renewal_clause", ""),
        "notice_period": contract.get("notice_period", ""),
        "frontend_url": settings.FRONTEND_URL,
        "contract_id": contract.get("id", ""),
    }

    if days_remaining <= 0:
        subject = f"⚠️ CONTRACT EXPIRED: {context['contract_name']}"
        template = "emails/reminder_expired.html"
    elif days_remaining <= 30:
        subject = f"🔴 Urgent: {context['contract_name']} expires in {days_remaining} days"
        template = "emails/reminder_30.html"
    else:
        subject = f"🟡 Reminder: {context['contract_name']} expires in {days_remaining} days"
        template = "emails/reminder_60.html"

    html_content = render_to_string(template, context)

    recipients = []
    if client.get("primary_reminder_email"):
        recipients.append(client["primary_reminder_email"])
    if client.get("secondary_reminder_email"):
        recipients.append(client["secondary_reminder_email"])

    cc_list = client.get("cc_emails", [])

    if not recipients:
        logger.warning(f"No recipients for contract {contract.get('id')}")
        return False

    return send_email(
        to_emails=recipients,
        subject=subject,
        html_content=html_content,
        cc_emails=cc_list,
    )
