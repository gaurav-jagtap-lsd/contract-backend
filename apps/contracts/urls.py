from django.urls import path
from . import views

urlpatterns = [
    path("", views.ContractListCreateView.as_view(), name="contract-list-create"),
    path("upload-file/", views.ContractUploadFileView.as_view(), name="contract-upload-file"),
    path("<str:contract_id>/", views.ContractDetailView.as_view(), name="contract-detail"),
    path("<str:contract_id>/pause/", views.ContractPauseView.as_view(), name="contract-pause"),
    path("<str:contract_id>/resume/", views.ContractResumeView.as_view(), name="contract-resume"),
    path("<str:contract_id>/snooze/", views.ContractSnoozeView.as_view(), name="contract-snooze"),
    path("<str:contract_id>/unsnooze/", views.ContractUnsnoozeView.as_view(), name="contract-unsnooze"),
    path("<str:contract_id>/renew/", views.ContractRenewView.as_view(), name="contract-renew"),
    path("<str:contract_id>/versions/", views.ContractVersionHistoryView.as_view(), name="contract-versions"),
    path("<str:contract_id>/file-url/", views.ContractFileUrlView.as_view(), name="contract-file-url"),
    path("<str:contract_id>/comments/", views.ContractCommentsView.as_view(), name="contract-comments"),
]
