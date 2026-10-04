"""Customer billing routes (ADR-0012), mounted under /api/v1/ by config.urls_api (bearer
tokens, plus provider webhooks) and config.urls_portal (the portal's session)."""

from django.urls import path

from apps.billing import api_customer

urlpatterns = [
    path("plans", api_customer.PublicPlanListView.as_view(), name="billing-plans"),
    path("payment-providers", api_customer.ProviderListView.as_view(), name="billing-providers"),
    path("checkout", api_customer.CheckoutView.as_view(), name="billing-checkout"),
    path("me/subscription", api_customer.MySubscriptionView.as_view(), name="me-subscription"),
    path("me/invoices", api_customer.MyInvoiceListView.as_view(), name="me-invoices"),
    path(
        "me/invoices/<uuid:pk>/document",
        api_customer.MyInvoiceDocumentView.as_view(),
        name="me-invoice-document",
    ),
]

#: api.<domain> only: providers call the public API host.
webhook_urlpatterns = [
    path("webhooks/<slug:provider>", api_customer.WebhookView.as_view(), name="billing-webhook"),
]
