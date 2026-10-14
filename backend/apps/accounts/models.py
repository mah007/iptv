"""Accounts (SPEC §6): users, RBAC, customer access profiles, devices, Xtream
credentials, access rules and admin MFA (TOTP, and WebAuthn from M14a)."""

from typing import Any

from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.crypto import salted_hmac

from apps.accounts.validators import validate_e164_phone, validate_timezone
from apps.core.models import BaseModel


class UserStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"
    DISABLED = "disabled", "Disabled"


class Locale(models.TextChoices):
    AR = "ar", "العربية"
    EN = "en", "English"


DEFAULT_TIMEZONE = "Asia/Riyadh"


def _service_setting(key: str, fallback: str) -> str:
    # Local import: apps.core.services imports this module.
    from django.db import DatabaseError  # noqa: PLC0415

    from apps.core.services import get_setting  # noqa: PLC0415

    try:
        return str(get_setting(key)) or fallback
    except DatabaseError:  # before the settings table exists (early migrations)
        return fallback


def default_locale() -> str:
    """New accounts speak the service's default language (`branding.default_language`)."""
    value = _service_setting("branding.default_language", Locale.AR)
    return value if value in Locale.values else Locale.AR


def default_timezone() -> str:
    """New accounts live in the service's time zone (`branding.time_zone`)."""
    return _service_setting("branding.time_zone", DEFAULT_TIMEZONE)


class User(BaseModel, AbstractUser):
    """Customers and staff alike; `is_staff=True` marks admin users (SPEC §6).

    One `name` replaces Django's first/last name pair: names are entered and shown
    whole, in Arabic or English. Emails are stored lowercased and are unique
    case-insensitively when present; customers may have only a phone number.
    `is_active` follows `status`: a disabled account cannot authenticate at all,
    a suspended one keeps its login but loses playback (its entitlement).
    """

    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]

    name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=16, blank=True, validators=[validate_e164_phone])
    status = models.CharField(max_length=16, choices=UserStatus.choices, default=UserStatus.ACTIVE)
    locale = models.CharField(max_length=2, choices=Locale.choices, default=default_locale)
    timezone = models.CharField(
        max_length=64, default=default_timezone, validators=[validate_timezone]
    )
    notes = models.TextField(blank=True)
    mfa_enabled = models.BooleanField(default=False)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    marketing_opt_in = models.BooleanField(default=False)
    roles = models.ManyToManyField("accounts.Role", blank=True, related_name="users")
    #: Raised to end every session of the account at once (an admin's MFA reset, M14a).
    #: Zero keeps Django's own session hash, so existing sessions survive the upgrade.
    auth_epoch = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                Lower("email"),
                condition=~models.Q(email=""),
                name="accounts_user_email_ci_unique",
            ),
        )
        indexes = (
            models.Index(fields=("is_staff", "-created_at"), name="accounts_user_staff_created"),
            models.Index(fields=("phone",), name="accounts_user_phone"),
        )

    def __str__(self) -> str:
        return self.username

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = (self.email or "").strip().lower()
        self.is_active = self.status != UserStatus.DISABLED
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "status" in update_fields:
            kwargs["update_fields"] = {*update_fields, "is_active"}
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return self.name.strip() or self.username

    def get_short_name(self) -> str:
        return self.get_full_name()

    def get_session_auth_hash(self) -> str:
        """Django's hash (over the password), plus the epoch once it was ever raised:
        sessions made before the last raise stop verifying on their next request."""
        base = super().get_session_auth_hash()
        if not self.auth_epoch:
            return base
        return salted_hmac(
            "apps.accounts.User.auth_epoch", f"{base}:{self.auth_epoch}", algorithm="sha256"
        ).hexdigest()


class Permission(BaseModel):
    """A fine-grained RBAC permission such as `customers.edit` (SPEC §6).

    The catalogue lives in code (`apps.accounts.rbac.PERMISSIONS`); rows exist so
    roles can reference them. Unrelated to Django's `auth.Permission`.
    """

    code = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=200)

    class Meta:
        ordering = ("code",)

    def __str__(self) -> str:
        return self.code


class Role(BaseModel):
    """A named set of permissions assigned to admin users (SPEC §6, §8.3 Security)."""

    name = models.CharField(max_length=64, unique=True)
    description = models.CharField(max_length=200, blank=True)
    permissions = models.ManyToManyField(Permission, blank=True, related_name="roles")

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class MaxQuality(models.IntegerChoices):
    SD = 480, "480p"
    HD = 720, "720p"
    FHD = 1080, "1080p"
    UHD = 2160, "2160p"


class ConcurrencyPolicy(models.TextChoices):
    REJECT = "reject", "Reject new streams"
    KICK_OLDEST = "kick_oldest", "Stop the oldest stream"


LIMIT_MAX = 50


class CustomerAccess(BaseModel):
    """The manual access profile of a customer (proof-of-concept scope, ADR-0006).

    Until the commercial slice adds plans and subscriptions, this profile is the
    only source of a customer's entitlement (`apps.playback.entitlements`).
    `expires_at` null means no expiry; no categories means every category.
    `expiry_processed_for` records the `expires_at` the expiry job last handled,
    so each expiry is processed exactly once and extending re-arms it.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="access")
    expires_at = models.DateTimeField(null=True, blank=True)
    max_streams = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(LIMIT_MAX)]
    )
    max_devices = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(1), MaxValueValidator(LIMIT_MAX)]
    )
    max_quality = models.PositiveSmallIntegerField(
        choices=MaxQuality.choices, default=MaxQuality.FHD
    )
    concurrency_policy = models.CharField(
        max_length=16, choices=ConcurrencyPolicy.choices, default=ConcurrencyPolicy.REJECT
    )
    allow_movies = models.BooleanField(default=True)
    allow_series = models.BooleanField(default=True)
    allow_live = models.BooleanField(default=True)
    categories = models.ManyToManyField("catalog.Category", blank=True, related_name="+")
    expiry_processed_for = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name_plural = "customer access profiles"
        indexes = (models.Index(fields=("expires_at",), name="accounts_access_expires"),)
        constraints = (
            models.CheckConstraint(
                condition=models.Q(max_streams__gte=1, max_streams__lte=LIMIT_MAX),
                name="accounts_access_max_streams_range",
            ),
            models.CheckConstraint(
                condition=models.Q(max_devices__gte=1, max_devices__lte=LIMIT_MAX),
                name="accounts_access_max_devices_range",
            ),
            models.CheckConstraint(
                condition=models.Q(max_quality__in=MaxQuality.values),
                name="accounts_access_max_quality_valid",
            ),
        )

    def __str__(self) -> str:
        return f"access:{self.user_id}"


class DeviceKind(models.TextChoices):
    XTREAM = "xtream", "IPTV app (Xtream)"
    WEB = "web", "Web player"
    APP = "app", "Mobile app"


class AppHint(models.TextChoices):
    SMARTERS = "smarters", "IPTV Smarters"
    TIVIMATE = "tivimate", "TiviMate"
    IBO = "ibo", "IBO Player"
    XCIPTV = "xciptv", "XCIPTV"
    OTT_NAVIGATOR = "ott_navigator", "OTT Navigator"
    SMARTONE = "smartone", "SmartOne"
    IPTVNATOR = "iptvnator", "IPTVnator"
    UHF = "uhf", "UHF"
    OTHER = "other", "Other"


class Device(BaseModel):
    """A customer device: an IPTV app with Xtream credentials, a browser or an app.

    Revoked devices stay for history but no longer count towards `max_devices`.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    kind = models.CharField(max_length=8, choices=DeviceKind.choices, default=DeviceKind.XTREAM)
    name = models.CharField(max_length=100)
    app_hint = models.CharField(max_length=16, choices=AppHint.choices, default=AppHint.OTHER)
    fingerprint = models.CharField(max_length=128, blank=True)
    first_seen = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    last_country = models.CharField(max_length=2, blank=True)
    approved = models.BooleanField(default=True)
    blocked = models.BooleanField(default=False)
    blocked_reason = models.CharField(max_length=200, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = (models.Index(fields=("user", "revoked_at"), name="accounts_device_user_live"),)

    def __str__(self) -> str:
        return f"{self.name} ({self.kind})"


class XtreamCredential(BaseModel):
    """Username and Argon2id password hash an IPTV app logs in with (SPEC §6, §11).

    The plaintext password exists only in the response that creates or resets it.
    """

    device = models.OneToOneField(Device, on_delete=models.CASCADE, related_name="credential")
    username = models.CharField(max_length=32, unique=True)
    password_hash = models.CharField(max_length=255)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.username


class AccessRuleType(models.TextChoices):
    IP_ALLOW = "ip_allow", "Allow IP address"
    IP_DENY = "ip_deny", "Deny IP address"
    CIDR_DENY = "cidr_deny", "Deny network (CIDR)"
    COUNTRY_ALLOW = "country_allow", "Allow country"
    COUNTRY_DENY = "country_deny", "Deny country"


class AccessRule(BaseModel):
    """An IP, network or country rule for one customer, or for everyone when `user`
    is null (SPEC §6, §7.4 check 4). Values are normalised by `validators`."""

    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="access_rules"
    )
    type = models.CharField(max_length=16, choices=AccessRuleType.choices)
    value = models.CharField(max_length=64)
    reason = models.CharField(max_length=200, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = (models.Index(fields=("user", "type"), name="accounts_rule_user_type"),)

    def __str__(self) -> str:
        return f"{self.type}:{self.value}"


class MfaTotp(BaseModel):
    """An admin's TOTP authenticator (SPEC §11; WebAuthn joins it in M14).

    The seed is Fernet-encrypted at rest (`apps.accounts.crypto`). Until
    `confirmed_at` is set the authenticator is being enrolled. `last_used_step`
    is the last accepted 30-second time step: a code is never accepted twice.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="mfa_totp")
    secret_encrypted = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return f"totp:{self.user_id}"


class WebAuthnCredential(BaseModel):
    """An admin's passkey or security key (SPEC §11; G-40, ADR-0021).

    A second factor next to TOTP: used to sign in after the password and to confirm
    sensitive actions (step-up). `credential_id` is the authenticator's id in unpadded
    base64url; `public_key` its COSE key. `sign_count` must grow with every use when the
    authenticator keeps a counter: a lower value means a cloned key and is refused.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="webauthn_credentials")
    credential_id = models.CharField(max_length=1400, unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    transports = models.JSONField(default=list, blank=True)
    aaguid = models.CharField(max_length=36, blank=True)
    name = models.CharField(max_length=64)
    #: A synced passkey (multi-device) rather than a single hardware key, and whether it
    #: is backed up; shown to the admin, never used for decisions.
    multi_device = models.BooleanField(default=False)
    backed_up = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)
        indexes = (models.Index(fields=("user", "created_at"), name="accounts_webauthn_user"),)

    def __str__(self) -> str:
        return f"webauthn:{self.user_id}:{self.name}"
