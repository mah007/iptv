"""Customer API representations: sign-in, the signed-in customer and their devices
(SPEC §9 Account, §10 Auth and Me; ADR-0013)."""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import AppHint, CustomerAccess, Device, User, XtreamCredential
from apps.accounts.serializers import (
    DeviceStatus,
    access_status,
    device_status,
    enum_choices,
    xtream_password_field,
    xtream_username_field,
)
from apps.accounts.validators import validate_timezone
from apps.playback.entitlements import EntitlementStatus

# --- Sign-in ---------------------------------------------------------------------------------


class CustomerLoginSerializer(serializers.Serializer[Any]):
    login = serializers.CharField(max_length=254, help_text="Username, email or phone number.")
    password = serializers.CharField(max_length=1024, trim_whitespace=False)


class AppLoginSerializer(CustomerLoginSerializer):
    device_name = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        default="",
        help_text='How the app shows in the customer\'s devices, e.g. "Living room TV".',
    )


class RefreshSerializer(serializers.Serializer[Any]):
    refresh_token = serializers.CharField(max_length=256, trim_whitespace=False)


class ForgotPasswordSerializer(serializers.Serializer[Any]):
    login = serializers.CharField(max_length=254, help_text="Username, email or phone number.")


class ResetPasswordSerializer(serializers.Serializer[Any]):
    uid = serializers.CharField(max_length=64, help_text="The link's uid parameter.")
    token = serializers.CharField(max_length=128, help_text="The link's token parameter.")
    password = serializers.CharField(
        max_length=1024, trim_whitespace=False, style={"input_type": "password"}
    )


# --- The customer ------------------------------------------------------------------------------


class CustomerAccessSummarySerializer(serializers.ModelSerializer[CustomerAccess]):
    """What the customer's access allows (their plan, until subscriptions exist)."""

    status = serializers.SerializerMethodField(method_name="status_of")

    class Meta:
        model = CustomerAccess
        fields = (
            "status",
            "expires_at",
            "max_streams",
            "max_devices",
            "max_quality",
            "allow_movies",
            "allow_series",
            "allow_live",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.ChoiceField(choices=enum_choices(EntitlementStatus)))
    def status_of(self, access: CustomerAccess) -> str:
        return access_status(access.user, access).value


class CustomerMeSerializer(serializers.ModelSerializer[User]):
    access = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "name",
            "email",
            "phone",
            "locale",
            "timezone",
            "marketing_opt_in",
            "status",
            "access",
        )
        read_only_fields = fields

    @extend_schema_field(CustomerAccessSummarySerializer(allow_null=True))
    def get_access(self, user: User) -> dict[str, Any] | None:
        try:
            access = user.access
        except CustomerAccess.DoesNotExist:
            return None
        return dict(CustomerAccessSummarySerializer(access).data)


class CustomerMeUpdateSerializer(serializers.ModelSerializer[User]):
    """What customers change themselves; email and phone stay with the admin."""

    class Meta:
        model = User
        fields = ("name", "locale", "timezone", "marketing_opt_in")
        extra_kwargs: dict[str, dict[str, Any]] = {  # noqa: RUF012 (DRF reads it)
            "name": {"allow_blank": False},
            "timezone": {"validators": []},
        }

    def validate_timezone(self, value: str) -> str:
        try:
            validate_timezone(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.messages, code=getattr(exc, "code", None)
            ) from None
        return value


class TokenPairSerializer(serializers.Serializer[Any]):
    """Shown once per sign-in or refresh; keep the refresh token in secure storage."""

    token_type = serializers.CharField(default="Bearer")
    access_token = serializers.CharField()
    expires_in = serializers.IntegerField(help_text="Seconds until the access token expires.")
    refresh_token = serializers.CharField(help_text="Single use: each refresh returns a new one.")
    refresh_expires_in = serializers.IntegerField(
        help_text="Seconds the refresh token stays valid without use."
    )


class AppSignInSerializer(TokenPairSerializer):
    user = CustomerMeSerializer()


# --- Devices -------------------------------------------------------------------------------------


class MyDeviceSerializer(serializers.ModelSerializer[Device]):
    status = serializers.SerializerMethodField(method_name="status_of")
    xtream_username = serializers.SerializerMethodField(method_name="username_of")
    current = serializers.SerializerMethodField(
        help_text="The device this request comes from (this browser or app)."
    )

    class Meta:
        model = Device
        fields = (
            "id",
            "kind",
            "name",
            "app_hint",
            "status",
            "xtream_username",
            "first_seen",
            "last_seen",
            "last_country",
            "created_at",
            "current",
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

    def get_current(self, device: Device) -> bool:
        return str(device.pk) == str(self.context.get("current_device_id"))


class MyDeviceCreateSerializer(serializers.Serializer[Any]):
    """ "Add TV app": a device with Xtream credentials, chosen or generated."""

    name = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    app_hint = serializers.ChoiceField(
        choices=AppHint.choices, required=False, default=AppHint.OTHER
    )
    username = xtream_username_field()
    password = xtream_password_field()


class MyDeviceUpdateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=100, required=False, allow_blank=False)
    app_hint = serializers.ChoiceField(choices=AppHint.choices, required=False)


class MyCredentialResetSerializer(serializers.Serializer[Any]):
    username = xtream_username_field()
    password = xtream_password_field()


class MyIssuedCredentialSerializer(serializers.Serializer[Any]):
    """Shown once: the password can never be read again (a reset makes a new one)."""

    device = MyDeviceSerializer(read_only=True)
    server_url = serializers.CharField(
        read_only=True, help_text="What the IPTV app asks for as the server or portal URL."
    )
    username = serializers.CharField(read_only=True)
    password = serializers.CharField(read_only=True)


# --- Admin: invitations ------------------------------------------------------------------------


class PasswordInvitationSerializer(serializers.Serializer[Any]):
    """Shown once. Send `url` to the customer if `emailed` is false (no email on file, or
    no email sender configured)."""

    url = serializers.URLField(help_text="The single-use link that sets the password.")
    expires_at = serializers.DateTimeField()
    emailed = serializers.BooleanField()
