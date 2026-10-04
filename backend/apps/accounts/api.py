"""Admin API of the accounts app (SPEC §8.3, §10): customers and their access
profiles, devices and Xtream credentials, access rules, roles and admins.

Views stay thin: they validate with serializers, call `apps.accounts.services`
(which audits every change) and answer with response serializers. Responses
that carry a plaintext password are marked `Cache-Control: no-store`.
"""

from typing import Any, ClassVar
from uuid import UUID

from django.db.models import Count, Max, Prefetch, Q, QuerySet
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import filters, generics
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services
from apps.accounts.filters import AccessRuleFilter, CustomerFilter
from apps.accounts.models import AccessRule, CustomerAccess, Device, Permission, Role, User
from apps.accounts.permissions import HasPermission, Requirements
from apps.accounts.serializers import (
    AccessProfileSerializer,
    AccessRuleCreateSerializer,
    AccessRuleSerializer,
    AdminCreatedSerializer,
    AdminCreateSerializer,
    AdminSerializer,
    AdminUpdateSerializer,
    CredentialResetSerializer,
    CustomerCreatedSerializer,
    CustomerCreateSerializer,
    CustomerDetailSerializer,
    CustomerProfileSerializer,
    CustomerSummarySerializer,
    DeviceBlockSerializer,
    DeviceCreateSerializer,
    DeviceSerializer,
    IssuedCredentialSerializer,
    PermissionSerializer,
    RoleSerializer,
    SuspendSerializer,
)
from apps.core.errors import ErrorCode, ProblemError, field_error
from apps.core.http import acting_user, client_ip
from apps.core.schema import problems

# --- Shared helpers ---------------------------------------------------------------------


def secret_response(data: Any, status: int = 200) -> Response:
    """A response carrying a one-time secret: never stored by browsers or proxies."""
    response = Response(data, status=status)
    response["Cache-Control"] = "no-store"
    return response


def issued_body(issued: services.IssuedCredential) -> dict[str, Any]:
    return {
        "device": issued.device,
        "server_url": services.xtream_server_url(),
        "username": issued.username,
        "password": issued.password,
    }


def customers() -> QuerySet[User]:
    return User.objects.filter(is_staff=False)


def customer_list_queryset() -> QuerySet[User]:
    """One query per page: the access profile joined, device figures aggregated.

    `distinct=True` keeps the count right when a search joins devices again.
    """
    return (
        customers()
        .select_related("access")
        .annotate(
            device_count=Count(
                "devices", filter=Q(devices__revoked_at__isnull=True), distinct=True
            ),
            last_seen=Max("devices__last_seen"),
        )
        .order_by("-created_at", "-id")
    )


def devices_with_credentials() -> QuerySet[Device]:
    return Device.objects.select_related("credential").order_by("created_at", "id")


def customer_detail_queryset() -> QuerySet[User]:
    return (
        customers()
        .select_related("access")
        .prefetch_related(
            "access__categories", Prefetch("devices", queryset=devices_with_credentials())
        )
    )


def customer_detail(pk: UUID) -> User:
    return get_object_or_404(customer_detail_queryset(), pk=pk)


def access_of(user: User) -> CustomerAccess:
    return (
        CustomerAccess.objects.select_related("user").prefetch_related("categories").get(user=user)
    )


class AdminView(APIView):
    """RBAC-guarded admin endpoint: subclasses declare `required_permissions`."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {}


# --- Customers ----------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="customers_list",
        summary="List customers with their access status and device counts",
        responses={200: CustomerSummarySerializer(many=True), **problems(400, 401, 403)},
    ),
    post=extend_schema(
        operation_id="customers_create",
        summary="Create a customer, their access profile and optionally a first device",
        description="The response holds the device credential's password; it is shown once.",
        request=CustomerCreateSerializer,
        responses={201: CustomerCreatedSerializer, **problems(400, 401, 403, 409)},
    ),
)
class CustomerListView(generics.ListCreateAPIView[User]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": "customers.view",
        "POST": "customers.edit",
    }
    serializer_class = CustomerSummarySerializer
    filter_backends = (DjangoFilterBackend, filters.SearchFilter)
    filterset_class = CustomerFilter
    search_fields = ("name", "email", "phone", "username", "devices__credential__username")

    def get_queryset(self) -> QuerySet[User]:
        return customer_list_queryset()

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = CustomerCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        profile = dict(payload.validated_data)
        access = profile.pop("access", None)
        device = profile.pop("device", None)
        user, issued = services.create_customer(
            profile,
            access=access,
            device=device,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        body = {
            "customer": customer_detail(user.pk),
            "credential": issued_body(issued) if issued is not None else None,
        }
        return secret_response(CustomerCreatedSerializer(body).data, status=201)


class CustomerDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {
        "GET": "customers.view",
        "PATCH": "customers.edit",
    }

    @extend_schema(
        operation_id="customers_retrieve",
        summary="A customer with their access profile and devices",
        responses={200: CustomerDetailSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(CustomerDetailSerializer(customer_detail(pk)).data)

    @extend_schema(
        operation_id="customers_update",
        summary="Change a customer's profile",
        request=CustomerProfileSerializer,
        responses={200: CustomerDetailSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        user = get_object_or_404(customers(), pk=pk)
        payload = CustomerProfileSerializer(user, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        services.update_customer(
            user, payload.validated_data, actor=acting_user(request), ip=client_ip(request)
        )
        return Response(CustomerDetailSerializer(customer_detail(pk)).data)


class CustomerSuspendView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "customers.edit"}

    @extend_schema(
        operation_id="customers_suspend",
        summary="Suspend a customer: playback stops, the account is kept",
        request=SuspendSerializer,
        responses={200: CustomerDetailSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        user = get_object_or_404(customers(), pk=pk)
        payload = SuspendSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.suspend_customer(
            user,
            actor=acting_user(request),
            ip=client_ip(request),
            reason=payload.validated_data["reason"],
        )
        return Response(CustomerDetailSerializer(customer_detail(pk)).data)


class CustomerReactivateView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "customers.edit"}

    @extend_schema(
        operation_id="customers_reactivate",
        summary="Reactivate a suspended or disabled customer",
        request=None,
        responses={200: CustomerDetailSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        user = get_object_or_404(customers(), pk=pk)
        services.reactivate_customer(user, actor=acting_user(request), ip=client_ip(request))
        return Response(CustomerDetailSerializer(customer_detail(pk)).data)


class CustomerAccessView(AdminView):
    required_permissions: ClassVar[Requirements] = {
        "GET": "customers.view",
        "PATCH": "customers.edit",
    }

    @extend_schema(
        operation_id="customers_access_retrieve",
        summary="A customer's access profile",
        responses={200: AccessProfileSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        user = get_object_or_404(customers(), pk=pk)
        services.ensure_access(user)
        return Response(AccessProfileSerializer(access_of(user)).data)

    @extend_schema(
        operation_id="customers_access_update",
        summary="Change a customer's access profile; the entitlement follows at once",
        request=AccessProfileSerializer,
        responses={200: AccessProfileSerializer, **problems(400, 401, 403, 404)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        user = get_object_or_404(customers(), pk=pk)
        payload = AccessProfileSerializer(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        changes = dict(payload.validated_data)
        category_ids = changes.pop("category_ids", None)
        services.update_access(
            user,
            changes,
            category_ids=category_ids,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(AccessProfileSerializer(access_of(user)).data)


# --- Devices and Xtream credentials ---------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="customers_devices_list",
        summary="A customer's devices, revoked ones included",
        responses={200: DeviceSerializer(many=True), **problems(401, 403, 404)},
    ),
    post=extend_schema(
        operation_id="customers_devices_create",
        summary="Add an IPTV app device with a new Xtream credential",
        description="The response holds the password; it is shown once. Refused with "
        "DEVICE_LIMIT when the customer already has max_devices devices.",
        request=DeviceCreateSerializer,
        responses={201: IssuedCredentialSerializer, **problems(400, 401, 403, 404, 409)},
    ),
)
class CustomerDeviceListView(generics.ListCreateAPIView[Device]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": "customers.view",
        "POST": "devices.manage",
    }
    serializer_class = DeviceSerializer
    filter_backends = ()

    def customer(self) -> User:
        return get_object_or_404(customers(), pk=self.kwargs["pk"])

    def get_queryset(self) -> QuerySet[Device]:
        return devices_with_credentials().filter(user=self.customer())

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        user = self.customer()
        payload = DeviceCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        issued = services.create_device_credential(
            user,
            name=payload.validated_data["name"],
            app_hint=payload.validated_data["app_hint"],
            username=payload.validated_data["username"],
            password=payload.validated_data["password"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return secret_response(IssuedCredentialSerializer(issued_body(issued)).data, status=201)


def device_of(pk: UUID) -> Device:
    return get_object_or_404(devices_with_credentials().filter(user__is_staff=False), pk=pk)


def device_response(pk: UUID) -> Response:
    return Response(DeviceSerializer(device_of(pk)).data)


class DeviceBlockView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "devices.manage"}

    @extend_schema(
        operation_id="devices_block",
        summary="Block a device: its sessions stop and it cannot play",
        request=DeviceBlockSerializer,
        responses={200: DeviceSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = DeviceBlockSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        services.block_device(
            device_of(pk),
            reason=payload.validated_data["reason"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return device_response(pk)


class DeviceUnblockView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "devices.manage"}

    @extend_schema(
        operation_id="devices_unblock",
        summary="Unblock a device",
        request=None,
        responses={200: DeviceSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        services.unblock_device(device_of(pk), actor=acting_user(request), ip=client_ip(request))
        return device_response(pk)


class DeviceApproveView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "devices.manage"}

    @extend_schema(
        operation_id="devices_approve",
        summary="Approve a device waiting for approval",
        request=None,
        responses={200: DeviceSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        services.approve_device(device_of(pk), actor=acting_user(request), ip=client_ip(request))
        return device_response(pk)


class DeviceRevokeView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "devices.manage"}

    @extend_schema(
        operation_id="devices_revoke",
        summary="Revoke a device and its credential for good",
        request=None,
        responses={200: DeviceSerializer, **problems(401, 403, 404)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        services.revoke_device(device_of(pk), actor=acting_user(request), ip=client_ip(request))
        return device_response(pk)


class DeviceResetCredentialsView(AdminView):
    required_permissions: ClassVar[Requirements] = {"POST": "devices.manage"}

    @extend_schema(
        operation_id="devices_reset_credentials",
        summary="Issue a new password for the device; the old one stops working",
        description="The response holds the new password; it is shown once. The body is "
        "optional: a chosen password (else one is generated) and a new username (else it "
        "stays).",
        request=CredentialResetSerializer,
        responses={200: IssuedCredentialSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def post(self, request: Request, pk: UUID) -> Response:
        payload = CredentialResetSerializer(data=request.data or {})
        payload.is_valid(raise_exception=True)
        issued = services.reset_credential(
            device_of(pk),
            username=payload.validated_data["username"],
            password=payload.validated_data["password"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return secret_response(IssuedCredentialSerializer(issued_body(issued)).data)


# --- Access rules -----------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        operation_id="access_rules_list",
        summary="IP, network and country rules, global and per customer",
        responses={200: AccessRuleSerializer(many=True), **problems(400, 401, 403)},
    ),
    post=extend_schema(
        operation_id="access_rules_create",
        summary="Add a rule for one customer, or for everyone",
        request=AccessRuleCreateSerializer,
        responses={201: AccessRuleSerializer, **problems(400, 401, 403)},
    ),
)
class AccessRuleListView(generics.ListCreateAPIView[AccessRule]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {
        "GET": "customers.view",
        "POST": "customers.edit",
    }
    serializer_class = AccessRuleSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_class = AccessRuleFilter

    def get_queryset(self) -> QuerySet[AccessRule]:
        return AccessRule.objects.order_by("-created_at", "-id")

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = AccessRuleCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        user = None
        if data["user"] is not None:
            user = customers().filter(pk=data["user"]).first()
            if user is None:
                raise ProblemError(
                    ErrorCode.VALIDATION_ERROR,
                    field_errors={
                        "user": [field_error("Unknown customer.", code="does_not_exist")]
                    },
                )
        rule = services.create_access_rule(
            user=user,
            rule_type=data["type"],
            value=data["value"],
            reason=data["reason"],
            expires_at=data["expires_at"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(AccessRuleSerializer(rule).data, status=201)


class AccessRuleDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {"DELETE": "customers.edit"}

    @extend_schema(
        operation_id="access_rules_destroy",
        summary="Remove a rule",
        responses={204: OpenApiResponse(description="Removed."), **problems(401, 403, 404)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        rule = get_object_or_404(AccessRule, pk=pk)
        services.delete_access_rule(rule, actor=acting_user(request), ip=client_ip(request))
        return Response(status=204)


# --- RBAC: permissions, roles, admins ------------------------------------------------------------

ROLE_READERS = ("roles.manage", "admins.manage")


@extend_schema_view(
    get=extend_schema(
        operation_id="permissions_list",
        summary="The permission catalogue",
        responses={200: PermissionSerializer(many=True), **problems(401, 403)},
    )
)
class PermissionListView(generics.ListAPIView[Permission]):
    """Small and fixed (defined in code), so not paginated."""

    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": ROLE_READERS}
    serializer_class = PermissionSerializer
    pagination_class = None
    filter_backends = ()

    def get_queryset(self) -> QuerySet[Permission]:
        return Permission.objects.order_by("code")


def roles_queryset() -> QuerySet[Role]:
    return (
        Role.objects.annotate(
            admin_count=Count("users", filter=Q(users__is_staff=True), distinct=True)
        )
        .prefetch_related("permissions")
        .order_by("name")
    )


@extend_schema_view(
    get=extend_schema(
        operation_id="roles_list",
        summary="Every role with its permissions (a handful, so not paginated)",
        responses={200: RoleSerializer(many=True), **problems(401, 403)},
    ),
    post=extend_schema(
        operation_id="roles_create",
        summary="Create a role",
        request=RoleSerializer,
        responses={201: RoleSerializer, **problems(400, 401, 403)},
    ),
)
class RoleListView(generics.ListCreateAPIView[Role]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": ROLE_READERS, "POST": "roles.manage"}
    serializer_class = RoleSerializer
    pagination_class = None
    filter_backends = ()

    def get_queryset(self) -> QuerySet[Role]:
        return roles_queryset()

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = RoleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        role = services.create_role(
            name=payload.validated_data["name"],
            description=payload.validated_data.get("description", ""),
            permissions=payload.validated_data.get("permissions", []),
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(RoleSerializer(roles_queryset().get(pk=role.pk)).data, status=201)


class RoleDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {
        "GET": ROLE_READERS,
        "PATCH": "roles.manage",
        "DELETE": "roles.manage",
    }

    @extend_schema(
        operation_id="roles_retrieve",
        summary="A role with its permissions",
        responses={200: RoleSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(RoleSerializer(get_object_or_404(roles_queryset(), pk=pk)).data)

    @extend_schema(
        operation_id="roles_update",
        summary="Rename a role or change its permissions",
        request=RoleSerializer,
        responses={200: RoleSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        role = get_object_or_404(Role, pk=pk)
        payload = RoleSerializer(role, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        changes = dict(payload.validated_data)
        permissions = changes.pop("permissions", None)
        services.update_role(
            role,
            changes,
            permissions=permissions,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(RoleSerializer(roles_queryset().get(pk=pk)).data)

    @extend_schema(
        operation_id="roles_destroy",
        summary="Delete a role no admin holds",
        responses={204: OpenApiResponse(description="Deleted."), **problems(401, 403, 404, 409)},
    )
    def delete(self, request: Request, pk: UUID) -> Response:
        role = get_object_or_404(Role, pk=pk)
        services.delete_role(role, actor=acting_user(request), ip=client_ip(request))
        return Response(status=204)


def admins_queryset() -> QuerySet[User]:
    return User.objects.filter(is_staff=True).prefetch_related("roles").order_by("username")


@extend_schema_view(
    get=extend_schema(
        operation_id="admins_list",
        summary="Admin users with their roles and MFA status",
        responses={200: AdminSerializer(many=True), **problems(401, 403)},
    ),
    post=extend_schema(
        operation_id="admins_create",
        summary="Create an admin with a generated password",
        description="The password is shown once. MFA enrolment happens at first sign-in.",
        request=AdminCreateSerializer,
        responses={201: AdminCreatedSerializer, **problems(400, 401, 403)},
    ),
)
class AdminListView(generics.ListCreateAPIView[User]):
    permission_classes = (HasPermission,)
    required_permissions: ClassVar[Requirements] = {"GET": "admins.manage", "POST": "admins.manage"}
    serializer_class = AdminSerializer
    filter_backends = (filters.SearchFilter,)
    search_fields = ("username", "name", "email")

    def get_queryset(self) -> QuerySet[User]:
        return admins_queryset()

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = AdminCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        issued = services.create_admin(
            username=data["username"],
            name=data.get("name", ""),
            email=data.get("email", ""),
            role_ids=data["role_ids"],
            actor=acting_user(request),
            ip=client_ip(request),
        )
        body = {"admin": admins_queryset().get(pk=issued.user.pk), "password": issued.password}
        return secret_response(AdminCreatedSerializer(body).data, status=201)


class AdminDetailView(AdminView):
    required_permissions: ClassVar[Requirements] = {
        "GET": "admins.manage",
        "PATCH": "admins.manage",
    }

    @extend_schema(
        operation_id="admins_retrieve",
        summary="An admin user",
        responses={200: AdminSerializer, **problems(401, 403, 404)},
    )
    def get(self, request: Request, pk: UUID) -> Response:
        return Response(AdminSerializer(get_object_or_404(admins_queryset(), pk=pk)).data)

    @extend_schema(
        operation_id="admins_update",
        summary="Change an admin's profile, status or roles",
        request=AdminUpdateSerializer,
        responses={200: AdminSerializer, **problems(400, 401, 403, 404, 409)},
    )
    def patch(self, request: Request, pk: UUID) -> Response:
        target = get_object_or_404(admins_queryset(), pk=pk)
        payload = AdminUpdateSerializer(target, data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        changes = dict(payload.validated_data)
        role_ids = changes.pop("role_ids", None)
        services.update_admin(
            target,
            changes,
            role_ids=role_ids,
            actor=acting_user(request),
            ip=client_ip(request),
        )
        return Response(AdminSerializer(admins_queryset().get(pk=pk)).data)
