"""Account services: customers, access profiles, devices and Xtream credentials,
admin users and roles (SPEC §6, §7.4, §8.3, §11).

Every mutation runs in a transaction, records an audit entry in it, and
schedules the entitlement refresh and signals for after the commit. Plaintext
passwords exist only in the returned `IssuedCredential`/`IssuedPassword`.
"""

import logging
import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Any, cast
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.accounts import credentials as creds
from apps.accounts.models import (
    AccessRule,
    AccessRuleType,
    AppHint,
    CustomerAccess,
    Device,
    DeviceKind,
    Permission,
    Role,
    User,
    UserStatus,
    XtreamCredential,
)
from apps.accounts.rbac import OWNER_ROLE
from apps.accounts.signals import access_expired, access_suspended, device_disabled
from apps.accounts.validators import normalize_country, normalize_ip, normalize_network
from apps.audit import services as audit
from apps.catalog.models import Category
from apps.core.errors import ErrorCode, ProblemError
from apps.core.metrics import SUBSCRIPTIONS
from apps.core.services import get_setting
from apps.core.stores import state_redis
from apps.playback import entitlements
from apps.playback.entitlements import EntitlementStatus
from config.origins import origin

logger = logging.getLogger(__name__)

PROFILE_FIELDS = ("name", "email", "phone", "locale", "timezone", "notes", "marketing_opt_in")
ACCESS_FIELDS = (
    "expires_at",
    "max_streams",
    "max_devices",
    "max_quality",
    "concurrency_policy",
    "allow_movies",
    "allow_series",
    "allow_live",
)
ADMIN_FIELDS = ("name", "email", "status")
CUSTOMER_USERNAME_PREFIX = "cus"
CUSTOMER_USERNAME_SUFFIX_LENGTH = 8
CUSTOMER_USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._@+-]{3,150}")
USERNAME_TAKEN = "This username is already taken."
ADMIN_PASSWORD_BYTES = 15  # token_urlsafe: 20 characters
_UNIQUE_ATTEMPTS = 8
EXPIRY_BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    """A device credential as issued: show `password` once, never store or log it."""

    device: Device
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class IssuedPassword:
    """A newly created admin and their one-time password."""

    user: User
    password: str


# --- Snapshots for the audit log (never secrets; keys avoid the redaction list) -------


def profile_snapshot(user: User) -> dict[str, Any]:
    return {
        "username": user.username,
        **{field: getattr(user, field) for field in PROFILE_FIELDS},
        "status": user.status,
    }


def access_snapshot(access: CustomerAccess, category_ids: Iterable[UUID | str]) -> dict[str, Any]:
    return {
        **{field: getattr(access, field) for field in ACCESS_FIELDS},
        "category_ids": sorted(str(category_id) for category_id in category_ids),
    }


def device_snapshot(device: Device, xtream_username: str | None = None) -> dict[str, Any]:
    return {
        "user_id": str(device.user_id),
        "name": device.name,
        "kind": device.kind,
        "app_hint": device.app_hint,
        "approved": device.approved,
        "blocked": device.blocked,
        "blocked_reason": device.blocked_reason,
        "revoked_at": device.revoked_at,
        "xtream_username": xtream_username,
    }


def admin_snapshot(user: User, role_names: Iterable[str]) -> dict[str, Any]:
    return {
        "username": user.username,
        **{field: getattr(user, field) for field in ADMIN_FIELDS},
        "roles": sorted(role_names),
    }


def _changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Only the keys whose values differ, as (before, after)."""
    keys = [key for key in after if before.get(key) != after.get(key)]
    return {key: before.get(key) for key in keys}, {key: after.get(key) for key in keys}


# --- Server URL shown with credentials ---------------------------------------------


def xtream_server_url() -> str:
    """What IPTV apps enter as the server: the `xtream.server_url` setting, else the
    public tv. host."""
    configured = str(get_setting("xtream.server_url"))
    if configured:
        return configured.rstrip("/")
    return origin(settings.PUBLIC_SCHEME, settings.TV_HOST, settings.PUBLIC_PORT)


# --- Customers ---------------------------------------------------------------------


def _new_customer_username() -> str:
    alphabet = creds.USERNAME_SUFFIX_ALPHABET
    for _ in range(_UNIQUE_ATTEMPTS):
        suffix = "".join(secrets.choice(alphabet) for _ in range(CUSTOMER_USERNAME_SUFFIX_LENGTH))
        candidate = f"{CUSTOMER_USERNAME_PREFIX}-{suffix}"
        if not User.objects.filter(username=candidate).exists():
            return candidate
    msg = "could not find a free customer username"
    raise RuntimeError(msg)


def _customer_username(wanted: str) -> str:
    """The admin's choice when given and free, else a generated `cus-…` username."""
    if not wanted:
        return _new_customer_username()
    if not CUSTOMER_USERNAME_PATTERN.fullmatch(wanted):
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            field_errors={"username": ["Use 3 to 150 letters, digits and . _ @ + -"]},
        )
    if User.objects.filter(username__iexact=wanted).exists():
        raise ProblemError(ErrorCode.VALIDATION_ERROR, field_errors={"username": [USERNAME_TAKEN]})
    return wanted


def _categories(category_ids: Sequence[UUID | str]) -> list[Category]:
    wanted = {str(category_id) for category_id in category_ids}
    found = list(Category.objects.filter(pk__in=wanted))
    missing = wanted - {str(category.pk) for category in found}
    if missing:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "Unknown category.",
            field_errors={
                "category_ids": [f"Unknown category {item}." for item in sorted(missing)]
            },
        )
    return found


def _apply(instance: Any, values: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    changed = []
    for field in fields:
        if field in values and getattr(instance, field) != values[field]:
            setattr(instance, field, values[field])
            changed.append(field)
    return changed


def create_customer(
    profile: Mapping[str, Any],
    *,
    access: Mapping[str, Any] | None = None,
    device: Mapping[str, Any] | None = None,
    actor: User | None,
    ip: str | None = None,
) -> tuple[User, IssuedCredential | None]:
    """Create a customer, their access profile and optionally their first device.

    `profile` may hold the admin's choice of `username` (else one is generated).
    `access` holds CustomerAccess fields plus `category_ids`; `device` holds `name`,
    `app_hint` and optionally the admin's choice of Xtream `username` and
    `password`. Customers have no usable password until the portal (M11b) lets
    them set one.
    """
    access_values = dict(access or {})
    category_ids = access_values.pop("category_ids", None) or []
    if device is not None:
        _check_chosen_credentials(
            str(device.get("username") or ""), str(device.get("password") or ""), prefix="device."
        )
    with transaction.atomic():
        user = User(username=_customer_username(str(profile.get("username") or "")), is_staff=False)
        _apply(user, profile, PROFILE_FIELDS)
        user.set_unusable_password()
        try:
            with transaction.atomic():
                user.save()
        except IntegrityError:
            raise ProblemError(
                ErrorCode.VALIDATION_ERROR, field_errors={"username": [USERNAME_TAKEN]}
            ) from None
        profile_row = CustomerAccess(user=user)
        _apply(profile_row, access_values, ACCESS_FIELDS)
        profile_row.save()
        categories = _categories(category_ids)
        profile_row.categories.set(categories)
        audit.record(
            "customer.create",
            actor=actor,
            target=user,
            after={
                **profile_snapshot(user),
                "access": access_snapshot(profile_row, (category.pk for category in categories)),
            },
            ip=ip,
        )
        issued = None
        if device is not None:
            issued = _issue_device(
                user, profile_row, device, actor=actor, ip=ip, field_prefix="device."
            )
        entitlements.schedule_refresh(user.pk)
    return user, issued


def update_customer(
    user: User, changes: Mapping[str, Any], *, actor: User | None, ip: str | None = None
) -> User:
    with transaction.atomic():
        before = profile_snapshot(user)
        changed = _apply(user, changes, PROFILE_FIELDS)
        if changed:
            user.save(update_fields=[*changed, "updated_at"])
            old, new = _changed(before, profile_snapshot(user))
            audit.record("customer.update", actor=actor, target=user, before=old, after=new, ip=ip)
    return user


def _set_status(  # noqa: PLR0913 (all but the first two are keyword-only)
    user: User,
    status: UserStatus,
    *,
    action: str,
    actor: User | None,
    ip: str | None,
    reason: str = "",
) -> bool:
    if user.status == status:
        return False
    before = user.status
    user.status = status
    user.save(update_fields=["status", "updated_at"])
    after: dict[str, Any] = {"status": status}
    if reason:
        after["reason"] = reason
    audit.record(action, actor=actor, target=user, before={"status": before}, after=after, ip=ip)
    entitlements.schedule_refresh(user.pk)
    return True


def suspend_customer(
    user: User, *, actor: User | None, ip: str | None = None, reason: str = ""
) -> User:
    """Stop the customer's playback: their entitlement becomes `suspended`."""
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        if user.status == UserStatus.DISABLED:
            raise ProblemError(ErrorCode.CONFLICT, "Disabled customers cannot be suspended.")
        if _set_status(
            user, UserStatus.SUSPENDED, action="customer.suspend", actor=actor, ip=ip, reason=reason
        ):
            transaction.on_commit(
                partial(access_suspended.send, sender=User, user_id=user.pk), robust=True
            )
    return user


def reactivate_customer(user: User, *, actor: User | None, ip: str | None = None) -> User:
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        _set_status(user, UserStatus.ACTIVE, action="customer.reactivate", actor=actor, ip=ip)
    return user


def ensure_access(user: User) -> CustomerAccess:
    access, _created = CustomerAccess.objects.get_or_create(user=user)
    return access


def update_access(
    user: User,
    changes: Mapping[str, Any],
    *,
    category_ids: Sequence[UUID | str] | None = None,
    actor: User | None,
    ip: str | None = None,
) -> CustomerAccess:
    """Change the access profile; `category_ids` None keeps the categories ([] = all)."""
    with transaction.atomic():
        access, _created = CustomerAccess.objects.select_for_update().get_or_create(user=user)
        current_ids = list(access.categories.values_list("pk", flat=True))
        before = access_snapshot(access, current_ids)
        changed = _apply(access, changes, ACCESS_FIELDS)
        if changed:
            access.save(update_fields=[*changed, "updated_at"])
        new_ids: Iterable[UUID | str] = current_ids
        if category_ids is not None:
            categories = _categories(category_ids)
            access.categories.set(categories)
            new_ids = [category.pk for category in categories]
        old, new = _changed(before, access_snapshot(access, new_ids))
        if new:
            audit.record(
                "customer.access.update", actor=actor, target=user, before=old, after=new, ip=ip
            )
            entitlements.schedule_refresh(user.pk)
    return access


# --- Devices and Xtream credentials -------------------------------------------------


def _save_credential(credential: XtreamCredential, prefix: str) -> None:
    """Insert with a fresh random username, retrying on the rare clash."""
    for attempt in range(_UNIQUE_ATTEMPTS):
        credential.username = creds.generate_username(prefix)
        try:
            with transaction.atomic():
                credential.save()
        except IntegrityError:
            if attempt == _UNIQUE_ATTEMPTS - 1:
                raise
            continue
        return


def _username_taken(username: str, *, exclude: XtreamCredential | None = None) -> bool:
    rows = XtreamCredential.objects.filter(username__iexact=username)
    if exclude is not None:
        rows = rows.exclude(pk=exclude.pk)
    return rows.exists()


def _check_chosen_credentials(
    username: str,
    password: str,
    *,
    prefix: str = "",
    current: XtreamCredential | None = None,
) -> None:
    """Refuse an admin-chosen Xtream username or password, field by field.

    Usernames are unique regardless of case (revoked devices keep theirs), so two
    logins never differ only in case. `current` is the credential being reset.
    """
    errors: dict[str, list[str]] = {}
    if username:
        problems = creds.username_problems(username)
        if not problems and _username_taken(username, exclude=current):
            problems = [USERNAME_TAKEN]
        if problems:
            errors[f"{prefix}username"] = problems
    if password:
        problems = creds.password_problems(
            password,
            min_length=int(get_setting("xtream.password_min_length")),
            username=username or (current.username if current is not None else ""),
        )
        if problems:
            errors[f"{prefix}password"] = problems
    if errors:
        raise ProblemError(ErrorCode.VALIDATION_ERROR, field_errors=errors)


def _save_chosen_username(credential: XtreamCredential, username: str, *, field: str) -> None:
    """Save with the admin's username; a concurrent taker of the same name loses cleanly."""
    credential.username = username
    try:
        with transaction.atomic():
            credential.save()
    except IntegrityError:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR, field_errors={field: [USERNAME_TAKEN]}
        ) from None


def _issue_device(  # noqa: PLR0913 (all but the first three are keyword-only)
    user: User,
    access: CustomerAccess,
    device: Mapping[str, Any],
    *,
    actor: User | None,
    ip: str | None,
    field_prefix: str = "",
) -> IssuedCredential:
    """Caller holds the user's row lock (or created the user in this transaction) and has
    checked any chosen credentials with `_check_chosen_credentials`."""
    active = Device.objects.filter(user=user, revoked_at__isnull=True).count()
    if active >= access.max_devices:
        raise ProblemError(
            ErrorCode.DEVICE_LIMIT,
            f"This customer already has {active} of {access.max_devices} allowed devices.",
        )
    row = Device.objects.create(
        user=user,
        kind=DeviceKind.XTREAM,
        name=str(device.get("name") or "").strip() or f"Device {active + 1}",
        app_hint=device.get("app_hint") or AppHint.OTHER,
        approved=True,
    )
    password = str(device.get("password") or "") or creds.generate_password()
    credential = XtreamCredential(device=row, password_hash=creds.hash_password(password))
    chosen_username = str(device.get("username") or "")
    if chosen_username:
        _save_chosen_username(credential, chosen_username, field=f"{field_prefix}username")
    else:
        _save_credential(credential, creds.username_prefix(user.name, user.email, user.username))
    audit.record(
        "device.create",
        actor=actor,
        target=row,
        after=device_snapshot(row, credential.username),
        ip=ip,
    )
    return IssuedCredential(device=row, username=credential.username, password=password)


def create_device_credential(  # noqa: PLR0913 (all but the first are keyword-only)
    user: User,
    *,
    name: str = "",
    app_hint: str = "",
    username: str = "",
    password: str = "",
    actor: User | None,
    ip: str | None = None,
) -> IssuedCredential:
    """Add an Xtream device within the profile's max_devices. The credential uses the
    admin's `username` and `password` when given, else generated ones."""
    if app_hint and app_hint not in AppHint.values:
        raise ProblemError(ErrorCode.VALIDATION_ERROR, field_errors={"app_hint": ["Unknown app."]})
    _check_chosen_credentials(username, password)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        access = ensure_access(user)
        values = {"name": name, "app_hint": app_hint, "username": username, "password": password}
        return _issue_device(user, access, values, actor=actor, ip=ip)


def _locked_device(device: Device) -> Device:
    # Only the device row is locked: the credential is on the nullable side of the join.
    return (
        Device.objects.select_for_update(of=("self",))
        .select_related("credential")
        .get(pk=device.pk)
    )


def _credential_of(device: Device) -> XtreamCredential | None:
    try:
        return device.credential
    except XtreamCredential.DoesNotExist:
        return None


def _username_of(device: Device) -> str | None:
    credential = _credential_of(device)
    return credential.username if credential is not None else None


def reset_credential(
    device: Device,
    *,
    username: str = "",
    password: str = "",
    actor: User | None,
    ip: str | None = None,
) -> IssuedCredential:
    """A new password for the device; the old one stops working at once.

    The admin may choose the new `password` (else one is generated) and rename the
    login with `username` (else it stays). Cached logins end too: the auth cache is
    keyed by the username and bound to the stored hash.
    """
    with transaction.atomic():
        device = _locked_device(device)
        if device.revoked_at is not None:
            raise ProblemError(ErrorCode.CONFLICT, "Revoked devices cannot get new credentials.")
        if device.kind != DeviceKind.XTREAM:
            raise ProblemError(ErrorCode.CONFLICT, "Only IPTV app devices have Xtream credentials.")
        credential = _credential_of(device)
        _check_chosen_credentials(username, password, current=credential)
        password = password or creds.generate_password()
        before = credential.username if credential is not None else None
        if credential is None:
            owner = device.user
            credential = XtreamCredential(
                device=device, password_hash=creds.hash_password(password)
            )
            if username:
                _save_chosen_username(credential, username, field="username")
            else:
                _save_credential(
                    credential, creds.username_prefix(owner.name, owner.email, owner.username)
                )
        else:
            credential.password_hash = creds.hash_password(password)
            fields = ["password_hash", "updated_at"]
            if username and username != credential.username:
                credential.username = username
                fields.append("username")
            try:
                with transaction.atomic():
                    credential.save(update_fields=fields)
            except IntegrityError:
                raise ProblemError(
                    ErrorCode.VALIDATION_ERROR, field_errors={"username": [USERNAME_TAKEN]}
                ) from None
        audit.record(
            "device.reset_credentials",
            actor=actor,
            target=device,
            before={"xtream_username": before},
            after={"xtream_username": credential.username},
            ip=ip,
        )
    return IssuedCredential(device=device, username=credential.username, password=password)


def _device_disabled_on_commit(device: Device) -> None:
    transaction.on_commit(
        partial(device_disabled.send, sender=Device, user_id=device.user_id, device_id=device.pk),
        robust=True,
    )


def _update_device(
    device: Device,
    changes: Mapping[str, Any],
    *,
    action: str,
    actor: User | None,
    ip: str | None,
) -> tuple[Device, bool]:
    device = _locked_device(device)
    before = device_snapshot(device, _username_of(device))
    changed = _apply(device, changes, tuple(changes))
    if not changed:
        return device, False
    device.save(update_fields=[*changed, "updated_at"])
    old, new = _changed(before, device_snapshot(device, _username_of(device)))
    audit.record(action, actor=actor, target=device, before=old, after=new, ip=ip)
    return device, True


def block_device(
    device: Device, *, reason: str = "", actor: User | None, ip: str | None = None
) -> Device:
    with transaction.atomic():
        if _locked_device(device).revoked_at is not None:
            raise ProblemError(ErrorCode.CONFLICT, "The device is revoked.")
        device, changed = _update_device(
            device,
            {"blocked": True, "blocked_reason": reason.strip()},
            action="device.block",
            actor=actor,
            ip=ip,
        )
        if changed:
            _device_disabled_on_commit(device)
    return device


def unblock_device(device: Device, *, actor: User | None, ip: str | None = None) -> Device:
    with transaction.atomic():
        device, _ = _update_device(
            device,
            {"blocked": False, "blocked_reason": ""},
            action="device.unblock",
            actor=actor,
            ip=ip,
        )
    return device


def approve_device(device: Device, *, actor: User | None, ip: str | None = None) -> Device:
    with transaction.atomic():
        device, _ = _update_device(
            device, {"approved": True}, action="device.approve", actor=actor, ip=ip
        )
    return device


def revoke_device(device: Device, *, actor: User | None, ip: str | None = None) -> Device:
    """Retire the device and its credential for good; it stops counting towards max_devices."""
    with transaction.atomic():
        device = _locked_device(device)
        if device.revoked_at is not None:
            return device
        now = timezone.now()
        device, _ = _update_device(
            device, {"revoked_at": now}, action="device.revoke", actor=actor, ip=ip
        )
        XtreamCredential.objects.filter(device=device, revoked_at__isnull=True).update(
            revoked_at=now, updated_at=now
        )
        _device_disabled_on_commit(device)
    return device


@dataclass(frozen=True, slots=True)
class XtreamLogin:
    credential: XtreamCredential
    device: Device
    user: User


def authenticate_xtream(username: str, password: str) -> XtreamLogin | None:
    """Check an IPTV app's username and password (SPEC §11); None when refused.

    One query loads the credential with its device and customer. A recent success
    for the same pair (cached 5 minutes in redis-state under an HMAC of the pair)
    skips Argon2id while the stored hash is unchanged. Unknown usernames still pay
    for one verification, so timing does not reveal which usernames exist. Revoked
    credentials and devices are refused here; blocked devices and inactive
    accounts are left to playback, which answers with a specific error code.
    """
    credential = (
        XtreamCredential.objects.select_related("device", "device__user")
        .filter(username=username)
        .first()
    )
    cache_key = creds.auth_cache_key(username, password)
    # Read before the unknown-user branch, so both paths make the same Redis round trip.
    cached_raw = cast("bytes | None", state_redis().get(cache_key))
    if credential is None:
        creds.burn_verify(password)
        return None
    fingerprint = creds.hash_fingerprint(credential.password_hash)
    cached = creds.CachedAuth.decode(cached_raw) if cached_raw else None
    verified = (
        cached is not None
        and cached.credential_id == str(credential.pk)
        and secrets.compare_digest(cached.hash_fingerprint, fingerprint)
    )
    if not verified:
        if not creds.verify_password(credential.password_hash, password):
            return None
        now = timezone.now()
        updates: dict[str, Any] = {"last_used_at": now, "updated_at": now}
        if creds.needs_rehash(credential.password_hash):
            updates["password_hash"] = creds.hash_password(password)
            fingerprint = creds.hash_fingerprint(updates["password_hash"])
        XtreamCredential.objects.filter(pk=credential.pk).update(**updates)
        state_redis().set(
            cache_key,
            creds.CachedAuth(str(credential.pk), fingerprint).encode(),
            ex=creds.AUTH_CACHE_TTL_S,
        )
    device = credential.device
    if credential.revoked_at is not None or device.revoked_at is not None:
        return None
    return XtreamLogin(credential=credential, device=device, user=device.user)


# --- Access rules (SPEC §6, §7.4 check 4) -------------------------------------------

_RULE_NORMALIZERS = {
    AccessRuleType.IP_ALLOW: normalize_ip,
    AccessRuleType.IP_DENY: normalize_ip,
    AccessRuleType.CIDR_DENY: normalize_network,
    AccessRuleType.COUNTRY_ALLOW: normalize_country,
    AccessRuleType.COUNTRY_DENY: normalize_country,
}


def normalize_rule_value(rule_type: str, value: str) -> str:
    """The canonical value for the rule type; raises ProblemError on a bad value."""
    try:
        normalizer = _RULE_NORMALIZERS[AccessRuleType(rule_type)]
    except ValueError:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR, field_errors={"type": ["Unknown rule type."]}
        ) from None
    try:
        return normalizer(value)
    except ValidationError as exc:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR, exc.messages[0], field_errors={"value": exc.messages}
        ) from None


def rule_snapshot(rule: AccessRule) -> dict[str, Any]:
    return {
        "user_id": str(rule.user_id) if rule.user_id else None,
        "type": rule.type,
        "value": rule.value,
        "reason": rule.reason,
        "expires_at": rule.expires_at,
    }


def create_access_rule(  # noqa: PLR0913 (keyword-only fields of one rule)
    *,
    user: User | None,
    rule_type: str,
    value: str,
    reason: str = "",
    expires_at: datetime | None = None,
    actor: User | None,
    ip: str | None = None,
) -> AccessRule:
    """A rule for one customer, or for everyone when `user` is None."""
    if user is not None and user.is_staff:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR, field_errors={"user": ["Rules apply to customers only."]}
        )
    if expires_at is not None and expires_at <= timezone.now():
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR, field_errors={"expires_at": ["Must be in the future."]}
        )
    canonical = normalize_rule_value(rule_type, value)
    with transaction.atomic():
        rule = AccessRule.objects.create(
            user=user, type=rule_type, value=canonical, reason=reason.strip(), expires_at=expires_at
        )
        audit.record(
            "access_rule.create", actor=actor, target=rule, after=rule_snapshot(rule), ip=ip
        )
        if user is not None:
            entitlements.schedule_refresh(user.pk)
    return rule


def delete_access_rule(rule: AccessRule, *, actor: User | None, ip: str | None = None) -> None:
    with transaction.atomic():
        audit.record(
            "access_rule.delete", actor=actor, target=rule, before=rule_snapshot(rule), ip=ip
        )
        user_id = rule.user_id
        rule.delete()
        if user_id is not None:
            entitlements.schedule_refresh(user_id)


# --- The expiry job -------------------------------------------------------------------


def _expire_one(access: CustomerAccess) -> None:
    access.expiry_processed_for = access.expires_at
    access.save(update_fields=["expiry_processed_for", "updated_at"])
    audit.record(
        "customer.access.expire",
        actor=None,
        target=access.user,
        after={"expires_at": access.expires_at},
    )
    entitlements.schedule_refresh(access.user_id)
    transaction.on_commit(
        partial(access_expired.send, sender=CustomerAccess, user_id=access.user_id), robust=True
    )


def process_expired_access(
    *, now: datetime | None = None, batch_size: int = EXPIRY_BATCH_SIZE
) -> int:
    """Handle every access period that ended and was not handled yet; returns the count.

    Each one gets an audit entry, an `expired` entitlement and the `access_expired`
    signal, exactly once per `expires_at` value. Safe to run concurrently: rows
    another run holds are skipped.
    """
    moment = now or timezone.now()
    total = 0
    while True:
        with transaction.atomic():
            batch = list(
                CustomerAccess.objects.select_for_update(skip_locked=True, of=("self",))
                .select_related("user")
                .filter(expires_at__lte=moment)
                .exclude(expiry_processed_for=F("expires_at"))
                .order_by("expires_at")[:batch_size]
            )
            for access in batch:
                _expire_one(access)
        total += len(batch)
        if len(batch) < batch_size:
            break
    publish_access_metrics(now=moment)
    if total:
        logger.info("expired %d customer access periods", total)
    return total


def access_status_counts(*, now: datetime | None = None) -> dict[str, int]:
    """Customers with an access profile, by entitlement status; one query."""
    moment = now or timezone.now()
    live = Q(status=UserStatus.ACTIVE)
    counts = User.objects.filter(is_staff=False, access__isnull=False).aggregate(
        active=Count(
            "pk",
            filter=live & (Q(access__expires_at__isnull=True) | Q(access__expires_at__gt=moment)),
        ),
        expired=Count("pk", filter=live & Q(access__expires_at__lte=moment)),
        suspended=Count("pk", filter=Q(status=UserStatus.SUSPENDED)),
        disabled=Count("pk", filter=Q(status=UserStatus.DISABLED)),
    )
    return {status.value: int(counts[status.value]) for status in EntitlementStatus}


def publish_access_metrics(*, now: datetime | None = None) -> None:
    """`iptv_subscriptions{status}`: until subscriptions exist (commercial slice), it
    counts customer access profiles by entitlement status (ADR-0006)."""
    try:
        SUBSCRIPTIONS.publish(
            {(status,): count for status, count in access_status_counts(now=now).items()}
        )
    except Exception:  # metrics must never break the job
        logger.exception("could not publish the access metrics")


# --- Admin users and roles ---------------------------------------------------------------


def is_owner(user: User) -> bool:
    return user.is_superuser or user.roles.filter(name=OWNER_ROLE).exists()


def _active_owner_ids() -> set[UUID]:
    return set(
        User.objects.filter(
            is_staff=True, status=UserStatus.ACTIVE, roles__name=OWNER_ROLE
        ).values_list("pk", flat=True)
    )


def _roles(role_ids: Sequence[UUID | str]) -> list[Role]:
    wanted = {str(role_id) for role_id in role_ids}
    found = list(Role.objects.filter(pk__in=wanted))
    missing = wanted - {str(role.pk) for role in found}
    if missing:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "Unknown role.",
            field_errors={"role_ids": [f"Unknown role {item}." for item in sorted(missing)]},
        )
    return found


def _guard_owner_change(actor: User | None, granting_or_revoking_owner: bool) -> None:
    if granting_or_revoking_owner and (actor is None or not is_owner(actor)):
        raise ProblemError(
            ErrorCode.PERMISSION_DENIED, "Only an owner can grant or remove the owner role."
        )


def generate_admin_password() -> str:
    return secrets.token_urlsafe(ADMIN_PASSWORD_BYTES)


def create_admin(  # noqa: PLR0913 (keyword-only fields of one admin)
    *,
    username: str,
    name: str = "",
    email: str = "",
    role_ids: Sequence[UUID | str] = (),
    actor: User | None,
    ip: str | None = None,
) -> IssuedPassword:
    """A staff user with a generated password, shown once. MFA enrolment happens at
    their first sign-in."""
    roles = _roles(role_ids)
    _guard_owner_change(actor, any(role.name == OWNER_ROLE for role in roles))
    password = generate_admin_password()
    with transaction.atomic():
        user = User(username=username, name=name, email=email, is_staff=True)
        user.set_password(password)
        user.save()
        user.roles.set(roles)
        audit.record(
            "admin.create",
            actor=actor,
            target=user,
            after=admin_snapshot(user, (role.name for role in roles)),
            ip=ip,
        )
    return IssuedPassword(user=user, password=password)


def update_admin(
    target: User,
    changes: Mapping[str, Any],
    *,
    role_ids: Sequence[UUID | str] | None = None,
    actor: User | None,
    ip: str | None = None,
) -> User:
    """Change an admin's profile, status or roles. Guards: only owners touch the owner
    role, nobody disables themselves, and one active owner always remains."""
    with transaction.atomic():
        target = User.objects.select_for_update().get(pk=target.pk, is_staff=True)
        current_roles = list(target.roles.all())
        before = admin_snapshot(target, (role.name for role in current_roles))
        new_roles = current_roles if role_ids is None else _roles(role_ids)
        had_owner = any(role.name == OWNER_ROLE for role in current_roles)
        has_owner = any(role.name == OWNER_ROLE for role in new_roles)
        _guard_owner_change(actor, had_owner != has_owner)
        new_status = changes.get("status", target.status)
        if actor is not None and actor.pk == target.pk and new_status != UserStatus.ACTIVE:
            raise ProblemError(ErrorCode.CONFLICT, "You cannot disable your own account.")
        if (
            had_owner
            and target.status == UserStatus.ACTIVE
            and (not has_owner or new_status != UserStatus.ACTIVE)
            and _active_owner_ids() == {target.pk}
        ):
            raise ProblemError(ErrorCode.CONFLICT, "At least one active owner must remain.")
        changed = _apply(target, changes, ADMIN_FIELDS)
        if changed:
            target.save(update_fields=[*changed, "updated_at"])
        if role_ids is not None:
            target.roles.set(new_roles)
        old, new = _changed(before, admin_snapshot(target, (role.name for role in new_roles)))
        if new:
            audit.record("admin.update", actor=actor, target=target, before=old, after=new, ip=ip)
    return target


def role_snapshot(role: Role, codes: Iterable[str]) -> dict[str, Any]:
    return {"name": role.name, "description": role.description, "permissions": sorted(codes)}


def _permission_rows(codes: Iterable[str]) -> list[Permission]:
    wanted = set(codes)
    rows = list(Permission.objects.filter(code__in=wanted))
    missing = wanted - {row.code for row in rows}
    if missing:
        raise ProblemError(
            ErrorCode.VALIDATION_ERROR,
            "Unknown permission.",
            field_errors={
                "permissions": [f"Unknown permission {code}." for code in sorted(missing)]
            },
        )
    return rows


def create_role(
    *,
    name: str,
    description: str = "",
    permissions: Iterable[str] = (),
    actor: User | None,
    ip: str | None = None,
) -> Role:
    rows = _permission_rows(permissions)
    with transaction.atomic():
        role = Role.objects.create(name=name, description=description)
        role.permissions.set(rows)
        audit.record(
            "role.create",
            actor=actor,
            target=role,
            after=role_snapshot(role, (row.code for row in rows)),
            ip=ip,
        )
    return role


def update_role(
    role: Role,
    changes: Mapping[str, Any],
    *,
    permissions: Iterable[str] | None = None,
    actor: User | None,
    ip: str | None = None,
) -> Role:
    if role.name == OWNER_ROLE:
        raise ProblemError(ErrorCode.CONFLICT, "The owner role always holds every permission.")
    with transaction.atomic():
        role = Role.objects.select_for_update().get(pk=role.pk)
        current = list(role.permissions.values_list("code", flat=True))
        before = role_snapshot(role, current)
        changed = _apply(role, changes, ("name", "description"))
        if changed:
            role.save(update_fields=[*changed, "updated_at"])
        codes: Iterable[str] = current
        if permissions is not None:
            rows = _permission_rows(permissions)
            role.permissions.set(rows)
            codes = [row.code for row in rows]
        old, new = _changed(before, role_snapshot(role, codes))
        if new:
            audit.record("role.update", actor=actor, target=role, before=old, after=new, ip=ip)
    return role


def delete_role(role: Role, *, actor: User | None, ip: str | None = None) -> None:
    if role.name == OWNER_ROLE:
        raise ProblemError(ErrorCode.CONFLICT, "The owner role cannot be deleted.")
    with transaction.atomic():
        role = Role.objects.select_for_update().get(pk=role.pk)
        if role.users.exists():
            raise ProblemError(
                ErrorCode.CONFLICT, "Remove this role from every admin before deleting it."
            )
        snapshot = role_snapshot(role, role.permissions.values_list("code", flat=True))
        audit.record("role.delete", actor=actor, target=role, before=snapshot, ip=ip)
        role.delete()
