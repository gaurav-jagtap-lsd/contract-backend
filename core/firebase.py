import firebase_admin
from firebase_admin import credentials, firestore, storage, auth
from django.conf import settings

_firebase_app = None


def get_firebase_app():
    global _firebase_app
    if _firebase_app is None:
        cred = credentials.Certificate(
            {
                "type": "service_account",
                "project_id": settings.FIREBASE_PROJECT_ID,
                "private_key_id": settings.FIREBASE_PRIVATE_KEY_ID,
                "private_key": settings.FIREBASE_PRIVATE_KEY,
                "client_email": settings.FIREBASE_CLIENT_EMAIL,
                "client_id": settings.FIREBASE_CLIENT_ID,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        )
        _firebase_app = firebase_admin.initialize_app(
            cred,
            {"storageBucket": settings.FIREBASE_STORAGE_BUCKET},
        )
    return _firebase_app


def get_firestore_client():
    get_firebase_app()
    return firestore.client()


def get_storage_bucket():
    get_firebase_app()
    return storage.bucket()


def get_auth_client():
    get_firebase_app()
    return auth
