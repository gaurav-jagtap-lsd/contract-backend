"""Workspace roles. Authorization is decided here, never from the client alone."""

import logging

from django.conf import settings
from firebase_admin import auth as firebase_auth

from core.firebase import get_firebase_app
from core.firestore_utils import USERS_COL, get_doc, serialize_firestore_doc, set_doc

logger = logging.getLogger(__name__)

ROLES = ("admin", "editor", "viewer")
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}

# editor: create and update contracts, including the pipeline stage. No deletes.
# viewer: read contracts. admin: everything, including who holds which role.
CAPABILITIES = {
    "viewer": {"read"},
    "editor": {"read", "write"},
    "admin": {"read", "write", "delete", "manage_users"},
}


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def bootstrap_roles() -> dict:
    configured = getattr(settings, "BOOTSTRAP_ROLES", None) or {}
    return {normalize_email(email): role for email, role in configured.items() if role in ROLES}


def protected_admin_emails() -> set:
    configured = getattr(settings, "PROTECTED_ADMIN_EMAILS", None) or []
    return {normalize_email(email) for email in configured}


def resolve_role(email: str, profile: dict | None) -> str:
    """Return the role that should be enforced for this account."""
    email = normalize_email(email)
    profile = profile or {}
    if email in protected_admin_emails():
        return "admin"
    if profile.get("role_customized"):
        role = profile.get("role")
        return role if role in ROLES else "viewer"
    assigned = bootstrap_roles().get(email)
    if assigned:
        return assigned
    stored = profile.get("role")
    if stored in ("editor", "viewer"):
        return stored
    # Older accounts were stored as admin for everyone. That is not a real assignment.
    return "viewer"


def role_can(role: str, capability: str) -> bool:
    return capability in CAPABILITIES.get(role if role in ROLES else "viewer", set())


def require(request, capability: str):
    from core.exceptions import error_response
    role = getattr(request.user, "role", "viewer")
    if role_can(role, capability):
        return None
    return error_response("You do not have permission to do that.", 403)


def ensure_user_profile(uid: str, email: str, display_name: str = "") -> dict:
    """Create or refresh the Firestore profile and persist the resolved role."""
    email = normalize_email(email)
    existing = get_doc(USERS_COL, uid) or {}
    role = resolve_role(email or existing.get("email", ""), existing)
    profile = {
        **existing,
        "uid": uid,
        "email": email or normalize_email(existing.get("email", "")),
        "display_name": existing.get("display_name") or display_name or "",
        "role": role,
        "is_active": existing.get("is_active", True),
        "role_customized": bool(existing.get("role_customized", False)),
    }
    if display_name and not existing.get("display_name"):
        profile["display_name"] = display_name
    if email in protected_admin_emails():
        profile["role"] = "admin"
    set_doc(USERS_COL, uid, profile)
    return serialize_firestore_doc({**profile, "id": uid})


def sync_bootstrap_accounts() -> None:
    """Make sure the named admin and editor exist in Firestore when Firebase already has them."""
    try:
        get_firebase_app()
    except Exception:
        logger.exception("Firebase is not ready; skipped role sync.")
        return

    for email in bootstrap_roles():
        try:
            fb_user = firebase_auth.get_user_by_email(email)
        except firebase_auth.UserNotFoundError:
            continue
        except Exception:
            logger.exception("Could not look up %s for role sync.", email)
            continue
        ensure_user_profile(fb_user.uid, email, fb_user.display_name or "")


def assign_role(actor_email: str, actor_uid: str, target_uid: str, new_role: str):
    from core.exceptions import error_response
    if new_role not in ROLES:
        return error_response("Choose Admin, Editor, or Viewer.")
    target = get_doc(USERS_COL, target_uid)
    if not target:
        return error_response("User not found.", 404)

    target_email = normalize_email(target.get("email", ""))
    if target_email in protected_admin_emails() and new_role != "admin":
        return error_response("This administrator cannot be changed.", 403)
    if actor_uid == target_uid and new_role != "admin":
        return error_response("You cannot remove your own admin access.", 403)

    target["role"] = new_role
    target["role_customized"] = True
    set_doc(USERS_COL, target_uid, target)
    return None
