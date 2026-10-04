"""Admin API routes of the billing app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.billing import api

urlpatterns = [
    path("plans", api.PlanListView.as_view(), name="admin-plans-list"),
    path("plans/reorder", api.PlanReorderView.as_view(), name="admin-plans-reorder"),
    path("plans/<uuid:pk>", api.PlanDetailView.as_view(), name="admin-plans-detail"),
    path(
        "plans/<uuid:pk>/migrate-subscriptions",
        api.PlanMigrateView.as_view(),
        name="admin-plans-migrate",
    ),
    path("subscriptions", api.SubscriptionListView.as_view(), name="admin-subscriptions-list"),
    path("subscriptions/trial", api.TrialStartView.as_view(), name="admin-subscriptions-trial"),
    path(
        "subscriptions/<uuid:pk>",
        api.SubscriptionDetailView.as_view(),
        name="admin-subscriptions-detail",
    ),
    path(
        "subscriptions/<uuid:pk>/extend",
        api.SubscriptionExtendView.as_view(),
        name="admin-subscriptions-extend",
    ),
    path(
        "subscriptions/<uuid:pk>/change-plan",
        api.SubscriptionChangePlanView.as_view(),
        name="admin-subscriptions-change-plan",
    ),
    path(
        "subscriptions/<uuid:pk>/cancel",
        api.SubscriptionCancelView.as_view(),
        name="admin-subscriptions-cancel",
    ),
    path(
        "subscriptions/<uuid:pk>/suspend",
        api.SubscriptionSuspendView.as_view(),
        name="admin-subscriptions-suspend",
    ),
    path(
        "subscriptions/<uuid:pk>/resume",
        api.SubscriptionResumeView.as_view(),
        name="admin-subscriptions-resume",
    ),
    path(
        "subscriptions/<uuid:pk>/approve",
        api.SubscriptionApproveView.as_view(),
        name="admin-subscriptions-approve",
    ),
    path("payments", api.PaymentListView.as_view(), name="admin-payments-list"),
    path("payments/<uuid:pk>", api.PaymentDetailView.as_view(), name="admin-payments-detail"),
    path(
        "payments/<uuid:pk>/refund", api.PaymentRefundView.as_view(), name="admin-payments-refund"
    ),
    path("invoices", api.InvoiceListView.as_view(), name="admin-invoices-list"),
    path("invoices/<uuid:pk>", api.InvoiceDetailView.as_view(), name="admin-invoices-detail"),
    path(
        "invoices/<uuid:pk>/document",
        api.InvoiceDocumentView.as_view(),
        name="admin-invoices-document",
    ),
    path("dashboard/billing", api.BillingKpisView.as_view(), name="admin-dashboard-billing"),
]
