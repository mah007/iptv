"""Admin API routes of the accounts app, mounted under /api/v1/admin/ by config.urls_admin."""

from django.urls import path

from apps.accounts import api

urlpatterns = [
    path("customers", api.CustomerListView.as_view(), name="admin-customers-list"),
    path("customers/<uuid:pk>", api.CustomerDetailView.as_view(), name="admin-customers-detail"),
    path(
        "customers/<uuid:pk>/suspend",
        api.CustomerSuspendView.as_view(),
        name="admin-customers-suspend",
    ),
    path(
        "customers/<uuid:pk>/reactivate",
        api.CustomerReactivateView.as_view(),
        name="admin-customers-reactivate",
    ),
    path(
        "customers/<uuid:pk>/access",
        api.CustomerAccessView.as_view(),
        name="admin-customers-access",
    ),
    path(
        "customers/<uuid:pk>/devices",
        api.CustomerDeviceListView.as_view(),
        name="admin-customers-devices",
    ),
    path("devices/<uuid:pk>/block", api.DeviceBlockView.as_view(), name="admin-devices-block"),
    path(
        "devices/<uuid:pk>/unblock", api.DeviceUnblockView.as_view(), name="admin-devices-unblock"
    ),
    path(
        "devices/<uuid:pk>/approve", api.DeviceApproveView.as_view(), name="admin-devices-approve"
    ),
    path("devices/<uuid:pk>/revoke", api.DeviceRevokeView.as_view(), name="admin-devices-revoke"),
    path(
        "devices/<uuid:pk>/reset-credentials",
        api.DeviceResetCredentialsView.as_view(),
        name="admin-devices-reset-credentials",
    ),
    path("access-rules", api.AccessRuleListView.as_view(), name="admin-access-rules-list"),
    path(
        "access-rules/<uuid:pk>",
        api.AccessRuleDetailView.as_view(),
        name="admin-access-rules-detail",
    ),
    path("permissions", api.PermissionListView.as_view(), name="admin-permissions-list"),
    path("roles", api.RoleListView.as_view(), name="admin-roles-list"),
    path("roles/<uuid:pk>", api.RoleDetailView.as_view(), name="admin-roles-detail"),
    path("admins", api.AdminListView.as_view(), name="admin-admins-list"),
    path("admins/<uuid:pk>", api.AdminDetailView.as_view(), name="admin-admins-detail"),
]
