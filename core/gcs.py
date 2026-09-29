from google.cloud import storage
from google.oauth2 import service_account
from django.conf import settings


def get_gcs_bucket():
    credentials = service_account.Credentials.from_service_account_file(
        settings.GCS_CREDENTIALS_PATH
    )

    client = storage.Client(
        project=settings.GCS_PROJECT_ID,
        credentials=credentials,
    )

    return client.bucket(settings.GCS_BUCKET_NAME)