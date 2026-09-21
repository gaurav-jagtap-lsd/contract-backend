from django.urls import path
from . import views

urlpatterns = [
    path("", views.ClientListCreateView.as_view(), name="client-list-create"),
    path("<str:client_id>/", views.ClientDetailView.as_view(), name="client-detail"),
    path("<str:client_id>/pause/", views.ClientPauseView.as_view(), name="client-pause"),
    path("<str:client_id>/resume/", views.ClientResumeView.as_view(), name="client-resume"),
    path("<str:client_id>/emails/", views.ClientEmailsView.as_view(), name="client-emails"),
]
