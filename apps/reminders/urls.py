from django.urls import path
from . import views

urlpatterns = [
    path("logs/", views.ReminderLogListView.as_view(), name="reminder-logs"),
    path("<str:contract_id>/send/", views.ManualReminderView.as_view(), name="reminder-send"),
]
