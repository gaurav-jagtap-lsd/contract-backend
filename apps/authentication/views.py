from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from firebase_admin import auth as firebase_auth
from core.firebase import get_firebase_app, get_firestore_client
from core.exceptions import success_response, error_response
from core.audit_service import log_action, get_client_ip, ACTIONS
from core.firestore_utils import get_doc, set_doc, USERS_COL
import requests
import logging

logger = logging.getLogger(__name__)


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        password = request.data.get("password", "")
        display_name = request.data.get("display_name", "").strip()

        if not email or not password:
            return error_response("Email and password are required.")
        if len(password) < 8:
            return error_response("Password must be at least 8 characters.")

        try:
            get_firebase_app()
            user = firebase_auth.create_user(
                email=email,
                password=password,
                display_name=display_name,
                email_verified=False,
            )

            # Store user profile in Firestore
            set_doc(USERS_COL, user.uid, {
                "uid": user.uid,
                "email": email,
                "display_name": display_name,
                "role": "admin",
                "is_active": True,
            })

            log_action(
                user_uid=user.uid,
                action=ACTIONS["LOGIN"],
                resource_type="user",
                resource_id=user.uid,
                description=f"User registered: {email}",
                ip_address=get_client_ip(request),
            )

            return success_response(
                data={"uid": user.uid, "email": email},
                message="Account created successfully.",
                status_code=201,
            )
        except firebase_auth.EmailAlreadyExistsError:
            return _register_existing_user(email, password, display_name)
        except Exception as exc:
            logger.error(f"Registration error: {exc}")
            return error_response("Registration failed. Please try again.", 500)


class LoginView(APIView):
    """
    Firebase authentication is handled client-side (Next.js).
    This endpoint validates the ID token and returns the user profile.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        id_token = request.data.get("id_token", "")
        if not id_token:
            return error_response("id_token is required.")

        try:
            get_firebase_app()
            decoded = firebase_auth.verify_id_token(id_token)
            uid = decoded["uid"]

            user_profile = get_doc(USERS_COL, uid)
            if not user_profile:
                firebase_user = firebase_auth.get_user(uid)
                user_profile = {
                    "uid": uid,
                    "email": decoded.get("email", ""),
                    "display_name": firebase_user.display_name or "",
                    "role": "admin",
                    "is_active": True,
                }
                set_doc(USERS_COL, uid, user_profile)

            log_action(
                user_uid=uid,
                action=ACTIONS["LOGIN"],
                resource_type="user",
                resource_id=uid,
                description="User logged in",
                ip_address=get_client_ip(request),
            )

            return success_response(data={"user": user_profile})
        except firebase_auth.ExpiredIdTokenError:
            return error_response("Session expired. Please log in again.", 401)
        except firebase_auth.InvalidIdTokenError:
            return error_response("Invalid authentication token.", 401)
        except Exception as exc:
            logger.error(f"Login error: {exc}")
            return error_response("Login failed.", 500)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        uid = request.user.uid
        log_action(
            user_uid=uid,
            action=ACTIONS["LOGOUT"],
            resource_type="user",
            resource_id=uid,
            description="User logged out",
            ip_address=get_client_ip(request),
        )
        return success_response(message="Logged out successfully.")


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        uid = request.user.uid
        user_profile = get_doc(USERS_COL, uid)
        if not user_profile:
            return error_response("User profile not found.", 404)
        return success_response(data={"user": user_profile})


class SendPasswordResetView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        if not email:
            return error_response("Email is required.")

        try:
            get_firebase_app()
            # Verify user exists (don't reveal if not found for security)
            try:
                firebase_auth.get_user_by_email(email)
            except firebase_auth.UserNotFoundError:
                # Return success anyway to prevent email enumeration
                return success_response(
                    message="If an account exists with this email, a reset link has been sent."
                )

            # Use Firebase REST API to send password reset email
            api_key = _get_firebase_web_api_key()
            if api_key:
                _send_firebase_password_reset(email, api_key)

            return success_response(
                message="If an account exists with this email, a reset link has been sent."
            )
        except Exception as exc:
            logger.error(f"Password reset error: {exc}")
            return success_response(
                message="If an account exists with this email, a reset link has been sent."
            )


def _register_existing_user(email: str, password: str, display_name: str):
    """A previous attempt may have created the Firebase user before the form finished.

    If this password matches that account, finish the profile and let the client sign in.
    """
    if not _password_matches(email, password):
        return error_response(
            "An account with this email already exists. Sign in instead.",
            409,
        )

    existing = firebase_auth.get_user_by_email(email)
    if display_name and not existing.display_name:
        firebase_auth.update_user(existing.uid, display_name=display_name)

    profile = get_doc(USERS_COL, existing.uid)
    if not profile:
        set_doc(USERS_COL, existing.uid, {
            "uid": existing.uid,
            "email": email,
            "display_name": display_name or existing.display_name or "",
            "role": "admin",
            "is_active": True,
        })

    return success_response(
        data={"uid": existing.uid, "email": email},
        message="Account already exists. Signing you in.",
        status_code=200,
    )


def _password_matches(email: str, password: str) -> bool:
    api_key = _get_firebase_web_api_key()
    if not api_key:
        return False
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    try:
        resp = requests.post(
            url,
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.error(f"Could not verify existing account: {exc}")
        return False
    return resp.ok


def _get_firebase_web_api_key():
    from django.conf import settings
    return getattr(settings, "FIREBASE_WEB_API_KEY", "")


def _send_firebase_password_reset(email: str, api_key: str):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={api_key}"
    payload = {"requestType": "PASSWORD_RESET", "email": email}
    resp = requests.post(url, json=payload, timeout=10)
    if not resp.ok:
        logger.error(f"Firebase password reset failed: {resp.text}")
