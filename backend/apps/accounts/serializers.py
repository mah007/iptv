"""Request and response bodies of the auth and admin account endpoints.

Choice fields: Django choices classes are used as `.choices` (value, label) and
plain enums as (value, NAME) pairs; that is how drf-spectacular matches them to
the stable names in SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"].
"""

from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, ClassVar

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.auth import LoginStatus
from apps.accounts.models import (
    AccessRule,
    AccessRuleType,
    AppHint,
    CustomerAccess,
    Device,
    Permission,
    Role,
    User,
    XtreamCredential,
)
from apps.accounts.rbac import ALL_PERMISSIONS, OWNER_ROLE, SYSTEM_ROLES
from apps.accounts.validators import normalize_phone, validate_timezone
from apps.catalog.serializers import CategoryBriefSerializer
from apps.playback.entitlements import EntitlementStatus, status_of


def enum_choices(enum: type[Enum]) -> list[tuple[Any, str]]:
    return [(member.value, member.name) for member in enum]


class DeviceStatus(StrEnum):
    """Derived from a device's flags, most severe first."""

    ACTIVE = "active"
    PENDING = "pending"
    BLOCKED = "blocked"
    REVOKED = "revoked"


def device_status(device: Device) -> DeviceStatus:
    if device.revoked_at is not None:
        return DeviceStatus.REVOKED
    if device.blocked:
        return DeviceStatus.BLOCKED
    if not device.approved:
        return DeviceStatus.PENDING
    return DeviceStatus.ACTIVE


def _access_of(user: User) -> CustomerAccess | None:
    try:
        return user.access
    except CustomerAccess.DoesNotExist:
        return None


def access_status(user: User, access: CustomerAccess | None) -> EntitlementStatus:
    expires_at = access.expires_at if access is not None else None
    return status_of(user.status, expires_at, timezone.now())


# --- Auth -------------------------------------------------------------------------------


class LoginSerializer(serializers.Serializer[Any]):
    login = serializers.CharField(max_length=254, help_text="Username or email address.")
    password = serializers.CharField(max_length=1024, trim_whitespace=False)


class LoginResponseSerializer(serializers.Serializer[Any]):
    status = serializers.ChoiceField(choices=enum_choices(LoginStatus))
    otpauth_uri = serializers.CharField(
        required=False,
        help_text="Only with mfa_setup_required: the content of the authenticator QR code.",
    )


class MfaVerifySerializer(serializers.Serializer[Any]):
    code = serializers.CharField(max_length=16, help_text="The six-digit code.")


class MeSerializer(serializers.ModelSerializer[User]):
    """The signed-in admin. `permissions` is what the UI may offer; the API checks again."""

    name = serializers.CharField(source="get_full_name", read_only=True)
    roles = serializers.SerializerMethodField(method_name="role_names")
    permissions = serializers.SerializerMethodField(method_name="permission_list")

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "email",
            "locale",
            "timezone",
            "mfa_enabled",
            "roles",
            "permissions",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def role_names(self, user: User) -> list[str]:
        return sorted(role.name for role in user.roles.all())

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def permission_list(self, user: User) -> list[str]:
        codes: frozenset[str] = self.context["permission_codes"]
        return sorted(codes)


# --- Devices ----------------------------------------------------------------------------


class DeviceSerializer(serializers.ModelSerializer[Device]):
    status = serializers.SerializerMethodField(method_name="status_of")
    xtream_username = serializers.SerializerMethodField(method_name="username_of")

    class Meta:
        model = Device
        fields = (
            "id",
            "kind",
            "name",
            "app_hint",
            "status",
            "approved",
            "blocked",
            "blocked_reason",
            "revoked_at",
            "xtream_username",
            "first_seen",
            "last_seen",
            "last_ip",
            "last_country",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=enum_choices(DeviceStatus)))
    def status_of(self, device: Device) -> str:
        return device_status(device).value

    @extend_schema_field(serializers.CharField(allow_null=True))
    def username_of(self, device: Device) -> str | None:
        try:
            return device.credential.username
        except XtreamCredential.DoesNotExist:
            return None


class DeviceCreateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    app_hint = serializers.ChoiceField(
        choices=AppHint.choices, required=False, default=AppHint.OTHER
    )


class DeviceBlockSerializer(serializers.Serializer[Any]):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class IssuedCredentialSerializer(serializers.Serializer[Any]):
    """Shown once: the plaintext password can never be read again (reset makes a new one)."""

    device = DeviceSerializer(read_only=True)
    server_url = serializers.CharField(
        read_only=True, help_text="What the IPTV app asks for as the server or portal URL."
    )
    username = serializers.CharField(read_only=True)
    password = serializers.CharField(read_only=True)


# --- Access profiles -----------------------------------------------------------------------


class AccessProfileSerializer(serializers.ModelSerializer[CustomerAccess]):
    """A customer's access profile. Empty `category_ids` means every category."""

    category_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, write_only=True, max_length=500
    )
    categories = CategoryBriefSerializer(many=True, read_only=True)
    status = serializers.SerializerMethodField(method_name="status_of")

    class Meta:
        model = CustomerAccess
        fields = (
            "status",
            "expires_at",
            "max_streams",
            "max_devices",
            "max_quality",
            "concurrency_policy",
            "allow_movies",
            "allow_series",
            "allow_live",
            "category_ids",
            "categories",
            "updated_at",
        )
        read_only_fields = ("status", "categories", "updated_at")

    @extend_schema_field(serializers.ChoiceField(choices=enum_choices(EntitlementStatus)))
    def status_of(self, access: CustomerAccess) -> str:
        return access_status(access.user, access).value


# --- Customers ------------------------------------------------------------------------------


def _unique_email(value: str, instance: User | None) -> str:
    email = value.strip().lower()
    if not email:
        return ""
    clash = User.objects.filter(email__iexact=email)
    if instance is not None:
        clash = clash.exclude(pk=instance.pk)
    if clash.exists():
        raise serializers.ValidationError("Another account already uses this email address.")
    return email


class CustomerProfileSerializer(serializers.ModelSerializer[User]):
    """Writable profile fields. Phones are stored in E.164 (+966 when no prefix is typed)."""

    class Meta:
        model = User
        fields: tuple[str, ...] = (
            "name",
            "email",
            "phone",
            "locale",
            "timezone",
            "notes",
            "marketing_opt_in",
        )
        extra_kwargs: ClassVar[dict[str, dict[str, Any]]] = {
            "name": {"required": True, "allow_blank": False},
            "phone": {"validators": []},
            "timezone": {"validators": []},
        }

    def validate_email(self, value: str) -> str:
        return _unique_email(value, self.instance)

    def validate_phone(self, value: str) -> str:
        if not value.strip():
            return ""
        try:
            return normalize_phone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from None

    def validate_timezone(self, value: str) -> str:
        try:
            validate_timezone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from None
        return value


class CustomerCreateSerializer(CustomerProfileSerializer):
    """The create-customer wizard: profile, access profile and an optional first device."""

    access = AccessProfileSerializer(required=False)
    device = DeviceCreateSerializer(required=False, allow_null=True)

    class Meta(CustomerProfileSerializer.Meta):
        fields = (*CustomerProfileSerializer.Meta.fields, "access", "device")


class CustomerSummarySerializer(serializers.ModelSerializer[User]):
    access_status = serializers.SerializerMethodField(method_name="access_status_of")
    expires_at = serializers.SerializerMethodField(method_name="expires_at_of")
    max_devices = serializers.SerializerMethodField(method_name="max_devices_of")
    device_count = serializers.IntegerField(read_only=True)
    last_seen = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "email",
            "phone",
            "status",
            "access_status",
            "expires_at",
            "max_devices",
            "device_count",
            "last_seen",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=enum_choices(EntitlementStatus)))
    def access_status_of(self, user: User) -> str:
        return access_status(user, _access_of(user)).value

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def expires_at_of(self, user: User) -> datetime | None:
        access = _access_of(user)
        return access.expires_at if access is not None else None

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def max_devices_of(self, user: User) -> int | None:
        access = _access_of(user)
        return access.max_devices if access is not None else None


class CustomerDetailSerializer(serializers.ModelSerializer[User]):
    access = serializers.SerializerMethodField(method_name="access_of")
    devices = DeviceSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "email",
            "phone",
            "status",
            "locale",
            "timezone",
            "notes",
            "marketing_opt_in",
            "access",
            "devices",
            "last_login",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    @extend_schema_field(AccessProfileSerializer(allow_null=True))
    def access_of(self, user: User) -> dict[str, Any] | None:
        access = _access_of(user)
        return dict(AccessProfileSerializer(access).data) if access is not None else None


class CustomerCreatedSerializer(serializers.Serializer[Any]):
    customer = CustomerDetailSerializer(read_only=True)
    credential = IssuedCredentialSerializer(read_only=True, allow_null=True)


class SuspendSerializer(serializers.Serializer[Any]):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


# --- Access rules ------------------------------------------------------------------------------


class AccessRuleSerializer(serializers.ModelSerializer[AccessRule]):
    """An IP, network or country rule; `user` null means it applies to everyone."""

    user = serializers.UUIDField(source="user_id", read_only=True, allow_null=True)

    class Meta:
        model = AccessRule
        fields = ("id", "user", "type", "value", "reason", "expires_at", "created_at")
        read_only_fields = fields


class AccessRuleCreateSerializer(serializers.Serializer[Any]):
    user = serializers.UUIDField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Customer id; omit for a global rule.",
    )
    type = serializers.ChoiceField(choices=AccessRuleType.choices)
    value = serializers.CharField(
        max_length=64, help_text="IP address, CIDR network or ISO 3166-1 alpha-2 country code."
    )
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


# --- RBAC: permissions, roles, admins ----------------------------------------------------------


class PermissionSerializer(serializers.ModelSerializer[Permission]):
    class Meta:
        model = Permission
        fields = ("code", "description")
        read_only_fields = fields


class PermissionCodesField(serializers.ListField):
    """A role's permission codes, sorted; the owner role always holds every one."""

    def get_attribute(self, instance: Role) -> list[str]:
        if instance.name == OWNER_ROLE:
            return sorted(ALL_PERMISSIONS)
        return sorted(permission.code for permission in instance.permissions.all())


class RoleSerializer(serializers.ModelSerializer[Role]):
    """A role and its permission codes. The owner role always lists every permission."""

    permissions = PermissionCodesField(
        child=serializers.CharField(max_length=64),
        required=False,
        max_length=len(ALL_PERMISSIONS),
    )
    is_system = serializers.SerializerMethodField(method_name="is_system_of")
    admin_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Role
        fields = (
            "id",
            "name",
            "description",
            "permissions",
            "is_system",
            "admin_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "is_system", "admin_count", "created_at", "updated_at")
        extra_kwargs: ClassVar[dict[str, dict[str, Any]]] = {"name": {"validators": []}}

    def validate_name(self, value: str) -> str:
        name = value.strip().lower()
        if not name.isascii() or not name.replace("_", "").isalnum() or not name[:1].isalpha():
            raise serializers.ValidationError(
                "Start with a letter; use lowercase letters, digits and underscores."
            )
        clash = Role.objects.filter(name=name)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("A role with this name already exists.")
        return name

    @extend_schema_field(serializers.BooleanField())
    def is_system_of(self, role: Role) -> bool:
        return role.name in SYSTEM_ROLES


class RoleBriefSerializer(serializers.ModelSerializer[Role]):
    class Meta:
        model = Role
        fields = ("id", "name")
        read_only_fields = fields


class AdminSerializer(serializers.ModelSerializer[User]):
    roles = RoleBriefSerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "email",
            "status",
            "roles",
            "mfa_enabled",
            "last_login",
            "last_login_ip",
            "created_at",
        )
        read_only_fields = fields


class AdminCreateSerializer(serializers.ModelSerializer[User]):
    role_ids = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)

    class Meta:
        model = User
        fields = ("username", "name", "email", "role_ids")

    def validate_email(self, value: str) -> str:
        return _unique_email(value, None)


class AdminUpdateSerializer(serializers.ModelSerializer[User]):
    """Suspended or disabled admins cannot sign in, and open sessions lose all access."""

    role_ids = serializers.ListField(child=serializers.UUIDField(), required=False)

    class Meta:
        model = User
        fields = ("name", "email", "status", "role_ids")

    def validate_email(self, value: str) -> str:
        return _unique_email(value, self.instance)


class AdminCreatedSerializer(serializers.Serializer[Any]):
    admin = AdminSerializer(read_only=True)
    password = serializers.CharField(read_only=True, help_text="Shown once.")
