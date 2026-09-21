from django.urls import path
from . import views

urlpatterns = [
    path("extract/", views.ExtractContractView.as_view(), name="ai-extract"),
    path("re-extract/", views.ReExtractView.as_view(), name="ai-re-extract"),
]
