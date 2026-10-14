"""Admin passkeys and security keys: WebAuthn as a second factor (SPEC §11; G-40, ADR-0021).

WebAuthn sits next to TOTP. The password still comes first; then either a six-digit code
or a key signs the session in, and either one confirms sensitive actions (step-up).

- **Relying party:** the admin host (`ADMIN_HOST`), so a key registered there answers
  nowhere else; the origin is the admin's public origin (`PUBLIC_SCHEME`, `PUBLIC_PORT`).
- **Challenges** live in the session under one key per purpose, are single-use and
  expire after `CHALLENGE_TTL_S`.
- **Attestation** is not requested (`none`): any authenticator may be used, which suits a
  small team better than an allow list of models.
- **Counters:** a key that keeps a signature counter must present a higher value each
  time. A lower one means a cloned key: it is refused and audited.

The library is `webauthn` (py_webauthn, BSD-3-Clause), which verifies the signatures,
origins, RP id hashes and flags.
"""

import base64
import json
import time
from typing import Any, Final, Literal

import structlog
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.http.request import split_domain_port
from django.utils import timezone
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidCBORData,
    InvalidJSONStructure,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from apps.accounts.models import MfaTotp, User, WebAuthnCredential
from apps.audit import services as audit
from apps.core.errors import ErrorCode, ProblemError
from apps.core.http import client_ip
from apps.core.services import get_setting
from config.origins import origin

logger = structlog.get_logger(__name__)

type Purpose = Literal["register", "login", "step_up"]

CHALLENGE_TTL_S: Final = 300
MAX_CREDENTIALS: Final = 10
_SESSION_PREFIX: Final = "_webauthn_challenge_"
_TRANSPORTS: Final = frozenset(item.value for item in AuthenticatorTransport)


def rp_id() -> str:
    """The relying party id: the admin host without a port."""
    host, _port = split_domain_port(str(settings.ADMIN_HOST))
    return host or str(settings.ADMIN_HOST)


def expected_origin() -> str:
    return origin(settings.PUBLIC_SCHEME, settings.ADMIN_HOST, settings.PUBLIC_PORT)


def rp_name() -> str:
    return str(get_setting("branding.service_name_en")) or "Smart IPTV"


def _user_handle(user: User) -> bytes:
    """The opaque user handle stored on the key: the account's UUID bytes, no name."""
    return user.pk.bytes


def _descriptors(user: User) -> list[PublicKeyCredentialDescriptor]:
    descriptors = []
    for credential in WebAuthnCredential.objects.filter(user=user):
        transports = [
            AuthenticatorTransport(item) for item in credential.transports if item in _TRANSPORTS
        ]
        descriptors.append(
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(credential.credential_id), transports=transports or None
            )
        )
    return descriptors


def has_credentials(user: User) -> bool:
    return WebAuthnCredential.objects.filter(user=user).exists()


def _remember(request: HttpRequest, purpose: Purpose, challenge: bytes, user: User) -> None:
    request.session[_SESSION_PREFIX + purpose] = {
        "challenge": bytes_to_base64url(challenge),
        "user_id": str(user.pk),
        "at": int(time.time()),
    }


def _take_challenge(request: HttpRequest, purpose: Purpose, user: User) -> bytes:
    """The purpose's challenge, removed from the session: it works once."""
    stored = request.session.pop(_SESSION_PREFIX + purpose, None)
    if (
        not isinstance(stored, dict)
        or stored.get("user_id") != str(user.pk)
        or time.time() - int(stored.get("at", 0)) > CHALLENGE_TTL_S
    ):
        raise ProblemError(
            ErrorCode.MFA_INVALID, "The security key request expired. Try again.", status=400
        )
    return base64url_to_bytes(str(stored["challenge"]))


def _invalid() -> ProblemError:
    return ProblemError(ErrorCode.MFA_INVALID, "The security key could not be verified.")


def _credential_json(raw: object) -> str:
    """The browser's PublicKeyCredential as JSON text (the SPA sends an object)."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return json.dumps(raw)
    raise _invalid()


# --- Registration -----------------------------------------------------------------------------


def registration_options(request: HttpRequest, user: User) -> dict[str, Any]:
    """Options for navigator.credentials.create(); keys the admin already has are excluded."""
    if WebAuthnCredential.objects.filter(user=user).count() >= MAX_CREDENTIALS:
        raise ProblemError(
            ErrorCode.CONFLICT, f"An admin can register at most {MAX_CREDENTIALS} keys."
        )
    options = generate_registration_options(
        rp_id=rp_id(),
        rp_name=rp_name(),
        user_id=_user_handle(user),
        user_name=user.email or user.username,
        user_display_name=user.get_full_name(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=_descriptors(user),
    )
    _remember(request, "register", options.challenge, user)
    return dict(json.loads(options_to_json(options)))


def finish_registration(
    request: HttpRequest, user: User, credential: object, name: str
) -> WebAuthnCredential:
    challenge = _take_challenge(request, "register", user)
    try:
        verified = verify_registration_response(
            credential=_credential_json(credential),
            expected_challenge=challenge,
            expected_rp_id=rp_id(),
            expected_origin=expected_origin(),
        )
    except (InvalidRegistrationResponse, InvalidJSONStructure, InvalidCBORData, ValueError):
        logger.info("webauthn.registration_refused", user_id=str(user.pk))
        raise _invalid() from None
    credential_id = bytes_to_base64url(verified.credential_id)
    if WebAuthnCredential.objects.filter(credential_id=credential_id).exists():
        raise ProblemError(ErrorCode.CONFLICT, "This key is already registered.")
    transports = _transports_of(credential)
    ip = client_ip(request)
    with transaction.atomic():
        stored = WebAuthnCredential.objects.create(
            user=user,
            credential_id=credential_id,
            public_key=verified.credential_public_key,
            sign_count=verified.sign_count,
            transports=transports,
            aaguid=str(verified.aaguid or ""),
            name=name.strip()[:64] or "Security key",
            multi_device=verified.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
            backed_up=bool(verified.credential_backed_up),
        )
        if not user.mfa_enabled:
            user.mfa_enabled = True
            user.save(update_fields=["mfa_enabled", "updated_at"])
        audit.record(
            "auth.webauthn_enroll",
            actor=user,
            target=user,
            ip=ip,
            after={"key": str(stored.pk), "name": stored.name},
        )
    return stored


def _transports_of(credential: object) -> list[str]:
    try:
        data = json.loads(credential) if isinstance(credential, str) else credential
        raw = data.get("response", {}).get("transports", []) if isinstance(data, dict) else []
    except (ValueError, AttributeError):
        return []
    return [item for item in raw if isinstance(item, str) and item in _TRANSPORTS][:8]


# --- Authentication (sign-in and step-up) -----------------------------------------------------


def authentication_options(
    request: HttpRequest, user: User, purpose: Literal["login", "step_up"]
) -> dict[str, Any]:
    """Options for navigator.credentials.get(), limited to this admin's keys."""
    descriptors = _descriptors(user)
    if not descriptors:
        raise ProblemError(ErrorCode.NOT_FOUND, "No security key is registered.", status=404)
    options = generate_authentication_options(
        rp_id=rp_id(),
        allow_credentials=descriptors,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _remember(request, purpose, options.challenge, user)
    return dict(json.loads(options_to_json(options)))


def _raw_id(credential: object) -> str:
    try:
        data = json.loads(credential) if isinstance(credential, str) else credential
        raw = data.get("rawId") or data.get("id") if isinstance(data, dict) else None
    except (ValueError, AttributeError):
        raw = None
    if not isinstance(raw, str) or not raw:
        raise _invalid()
    # Normalise to unpadded base64url, whatever padding the client sent.
    try:
        return bytes_to_base64url(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError):
        raise _invalid() from None


def verify_assertion(
    request: HttpRequest,
    user: User,
    credential: object,
    purpose: Literal["login", "step_up"],
) -> WebAuthnCredential:
    """Check an assertion from one of the admin's keys. Raises MFA_INVALID."""
    challenge = _take_challenge(request, purpose, user)
    cloned: WebAuthnCredential | None = None
    with transaction.atomic():
        stored = (
            WebAuthnCredential.objects.select_for_update()
            .filter(user=user, credential_id=_raw_id(credential))
            .first()
        )
        if stored is None:
            raise _invalid()
        try:
            verified = verify_authentication_response(
                credential=_credential_json(credential),
                expected_challenge=challenge,
                expected_rp_id=rp_id(),
                expected_origin=expected_origin(),
                credential_public_key=bytes(stored.public_key),
                credential_current_sign_count=stored.sign_count,
            )
        except InvalidAuthenticationResponse as exc:
            logger.info("webauthn.assertion_refused", user_id=str(user.pk))
            if "sign count" not in str(exc).lower():
                raise _invalid() from None
            cloned = stored
        except (InvalidJSONStructure, InvalidCBORData, ValueError):
            raise _invalid() from None
        else:
            stored.sign_count = verified.new_sign_count
            stored.backed_up = bool(verified.credential_backed_up)
            stored.last_used_at = timezone.now()
            stored.save(update_fields=["sign_count", "backed_up", "last_used_at", "updated_at"])
    if cloned is not None:
        # Outside the refused transaction, so the record stays.
        audit.record(
            "auth.webauthn_clone_suspected",
            actor=user,
            target=user,
            ip=client_ip(request),
            after={"key": str(cloned.pk), "name": cloned.name},
        )
        raise _invalid()
    return stored


# --- Management -------------------------------------------------------------------------------


def has_totp(user: User) -> bool:
    return MfaTotp.objects.filter(user=user, confirmed_at__isnull=False).exists()


def remove(request: HttpRequest, user: User, key: WebAuthnCredential) -> None:
    """Remove one of the admin's keys; never the last factor they have."""
    with transaction.atomic():
        others = WebAuthnCredential.objects.filter(user=user).exclude(pk=key.pk).exists()
        if not others and not has_totp(user):
            raise ProblemError(
                ErrorCode.CONFLICT,
                "This is your only way to confirm sign-ins. Add another key or an "
                "authenticator app first.",
            )
        audit.record(
            "auth.webauthn_remove",
            actor=user,
            target=user,
            ip=client_ip(request),
            before={"key": str(key.pk), "name": key.name},
        )
        key.delete()


def rename(request: HttpRequest, user: User, key: WebAuthnCredential, name: str) -> None:
    before = key.name
    key.name = name.strip()[:64] or before
    key.save(update_fields=["name", "updated_at"])
    if before != key.name:
        audit.record(
            "auth.webauthn_rename",
            actor=user,
            target=user,
            ip=client_ip(request),
            before={"name": before},
            after={"name": key.name},
        )


def enrolment_required(user: User) -> bool:
    """`security.webauthn_required_for` covers this admin and they have no key yet: the
    admin API refuses everything until they register one. Computed on each request, not
    kept in the session, where a concurrent request could write the flag back after the
    key is added."""
    return required_for(user) and not has_credentials(user)


def required_for(user: User) -> bool:
    """Whether `security.webauthn_required_for` makes this admin sign in with a key."""
    from apps.accounts.rbac import OWNER_ROLE  # noqa: PLC0415

    policy = str(get_setting("security.webauthn_required_for"))
    if policy == "none":
        return False
    names = set(user.roles.values_list("name", flat=True))
    if policy == "owners":
        return OWNER_ROLE in names
    return bool(names & {OWNER_ROLE, "admin"})
