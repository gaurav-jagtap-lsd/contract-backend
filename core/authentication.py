from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from firebase_admin import auth as firebase_auth
from core.firebase import get_firebase_app


class FirebaseUser:
    """Lightweight user object populated from the verified Firebase token."""

    def __init__(self, decoded_token: dict):
        self.uid = decoded_token["uid"]
        self.email = decoded_token.get("email", "")
        self.email_verified = decoded_token.get("email_verified", False)
        self.decoded_token = decoded_token
        self.is_authenticated = True
        self.is_active = True

    def __str__(self):
        return f"FirebaseUser({self.uid})"


class FirebaseAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            return None

        try:
            get_firebase_app()
            decoded_token = firebase_auth.verify_id_token(token)
        except firebase_auth.ExpiredIdTokenError:
            raise AuthenticationFailed("Firebase token has expired.")
        except firebase_auth.RevokedIdTokenError:
            raise AuthenticationFailed("Firebase token has been revoked.")
        except firebase_auth.InvalidIdTokenError:
            raise AuthenticationFailed("Firebase token is invalid.")
        except Exception as exc:
            raise AuthenticationFailed(f"Authentication failed: {str(exc)}")

        user = FirebaseUser(decoded_token)
        return (user, token)

    def authenticate_header(self, request):
        return "Bearer"
