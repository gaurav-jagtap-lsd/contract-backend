from django.http import JsonResponse
from django.urls import path, include


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("health/", health),
    path("api/auth/", include("apps.authentication.urls")),
    path("api/clients/", include("apps.clients.urls")),
    path("api/contracts/", include("apps.contracts.urls")),
    path("api/ai/", include("apps.ai_extraction.urls")),
    path("api/reminders/", include("apps.reminders.urls")),
    path("api/audit/", include("apps.audit.urls")),
    path("api/dashboard/", include("apps.contracts.dashboard_urls")),
]
