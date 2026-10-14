"""Admin passkeys and security keys (G-40, ADR-0021), with a software authenticator."""

from typing import Any

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts import mfa
from apps.accounts.models import MfaTotp, User, WebAuthnCredential
from apps.accounts.tests.authenticator import SoftKey
from apps.accounts.tests.test_auth_api import ADMIN, ME, enrol, login
from apps.audit.models import AuditLog
from apps.conftest import AdminFactory
from apps.core.services import set_setting

pytestmark = pytest.mark.django_db

KEYS = "/api/v1/auth/webauthn/credentials"
KEY_OPTIONS = "/api/v1/auth/webauthn/credentials/options"
SIGN_OPTIONS = "/api/v1/auth/mfa/webauthn/options"
SIGN_VERIFY = "/api/v1/auth/mfa/webauthn/verify"
STEP_UP = "/api/v1/auth/step-up"
STEP_UP_OPTIONS = "/api/v1/auth/step-up/options"


@pytest.fixture(autouse=True)
def _frozen_totp_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mfa, "current_step", lambda now=None: 60_000_000)


@pytest.fixture
def sam(make_admin: AdminFactory) -> User:
    return make_admin("admin", username="sam")


def signed_in(user: User) -> APIClient:
    client = APIClient()
    assert enrol(client, user).status_code == 200
    return client


def register(client: APIClient, key: SoftKey, name: str = "YubiKey") -> Any:
    options = client.post(KEY_OPTIONS, headers=ADMIN).json()["options"]
    return client.post(
        KEYS, {"name": name, "credential": key.register(options)}, format="json", headers=ADMIN
    )


def sign_in_with(key: SoftKey, user: User) -> tuple[APIClient, Any]:
    client = APIClient()
    assert login(client, user.username).json()["methods"] == ["webauthn", "totp"]
    options = client.post(SIGN_OPTIONS, headers=ADMIN).json()["options"]
    response = client.post(
        SIGN_VERIFY, {"credential": key.sign(options)}, format="json", headers=ADMIN
    )
    return client, response


def test_register_then_sign_in_with_the_key(sam: User) -> None:
    client = signed_in(sam)
    key = SoftKey()
    response = register(client, key)
    assert response.status_code == 201, response.json()
    assert response.json()["name"] == "YubiKey"
    assert client.get(KEYS, headers=ADMIN).json()[0]["name"] == "YubiKey"
    assert client.get(ME, headers=ADMIN).json()["mfa_methods"] == ["webauthn", "totp"]
    assert AuditLog.objects.filter(action="auth.webauthn_enroll").count() == 1

    other, verified = sign_in_with(key, sam)
    assert verified.status_code == 200, verified.json()
    assert verified.json()["username"] == "sam"
    assert other.get(ME, headers=ADMIN).status_code == 200
    login_entry = AuditLog.objects.filter(action="auth.login").latest("at")
    assert login_entry.after is not None
    assert login_entry.after["method"] == "webauthn"
    assert WebAuthnCredential.objects.get().sign_count == 1


def test_a_challenge_works_once_and_a_cloned_key_is_refused(sam: User) -> None:
    client = signed_in(sam)
    key = SoftKey()
    register(client, key)
    other = APIClient()
    login(other, "sam")
    options = other.post(SIGN_OPTIONS, headers=ADMIN).json()["options"]
    assertion = key.sign(options)
    first = other.post(SIGN_VERIFY, {"credential": assertion}, format="json", headers=ADMIN)
    assert first.status_code == 200
    # The same answer again: its challenge is gone.
    login(other, "sam")
    again = other.post(SIGN_VERIFY, {"credential": assertion}, format="json", headers=ADMIN)
    assert again.status_code == 400
    assert again.json()["code"] == "MFA_INVALID"
    # A copy of the key with an older counter: refused and audited.
    third = APIClient()
    login(third, "sam")
    options = third.post(SIGN_OPTIONS, headers=ADMIN).json()["options"]
    cloned = third.post(
        SIGN_VERIFY, {"credential": key.sign(options, counter=1)}, format="json", headers=ADMIN
    )
    assert cloned.status_code == 400
    assert AuditLog.objects.filter(action="auth.webauthn_clone_suspected").count() == 1


def test_a_key_from_another_site_is_refused(sam: User) -> None:
    client = signed_in(sam)
    key = SoftKey()
    register(client, key)
    phished = SoftKey(origin="https://admin.evil.example")
    phished.private, phished.credential_id = key.private, key.credential_id
    phished.counter = 5
    other = APIClient()
    login(other, "sam")
    options = other.post(SIGN_OPTIONS, headers=ADMIN).json()["options"]
    response = other.post(
        SIGN_VERIFY, {"credential": phished.sign(options)}, format="json", headers=ADMIN
    )
    assert response.status_code == 400


def test_rename_and_remove_but_never_the_last_factor(sam: User) -> None:
    client = signed_in(sam)
    register(client, SoftKey(), "Old name")
    key_id = client.get(KEYS, headers=ADMIN).json()[0]["id"]
    renamed = client.patch(f"{KEYS}/{key_id}", {"name": "Office key"}, headers=ADMIN)
    assert renamed.json()["name"] == "Office key"
    MfaTotp.objects.filter(user=sam).delete()  # the key is now the only factor
    refused = client.delete(f"{KEYS}/{key_id}", headers=ADMIN)
    assert refused.status_code == 409
    register(client, SoftKey(), "Spare")
    assert client.delete(f"{KEYS}/{key_id}", headers=ADMIN).status_code == 204
    assert list(WebAuthnCredential.objects.values_list("name", flat=True)) == ["Spare"]


def test_a_policy_can_require_keys_for_owners(make_admin: AdminFactory) -> None:
    olivia = make_admin("owner", username="olivia")
    set_setting("security.webauthn_required_for", "owners", actor=None)
    # Without a key yet: the code still signs in, but nothing works until one is added.
    client = signed_in(olivia)
    assert client.get(ME, headers=ADMIN).json()["webauthn_enrolment_required"] is True
    blocked = client.get("/api/v1/admin/customers", headers=ADMIN)
    assert blocked.status_code == 403
    key = SoftKey()
    assert register(client, key).status_code == 201
    assert client.get("/api/v1/admin/customers", headers=ADMIN).status_code == 200
    # With a key, only the key signs in.
    other = APIClient()
    assert login(other, "olivia").json()["methods"] == ["webauthn"]
    from apps.accounts.tests.test_auth_api import VERIFY, code_for  # noqa: PLC0415

    response = other.post(VERIFY, {"code": code_for(olivia, step_offset=1)}, headers=ADMIN)
    assert response.status_code == 400
    options = other.post(SIGN_OPTIONS, headers=ADMIN).json()["options"]
    verified = other.post(
        SIGN_VERIFY, {"credential": key.sign(options)}, format="json", headers=ADMIN
    )
    assert verified.status_code == 200


def test_another_admin_resets_a_lost_factor_and_the_sessions_end(
    sam: User, make_admin: AdminFactory
) -> None:
    session = signed_in(sam)
    register(session, SoftKey())
    owner = make_admin("owner", username="olga")
    owner_client = APIClient()
    owner_client.force_authenticate(owner)
    response = owner_client.post(f"/api/v1/admin/admins/{sam.pk}/reset-mfa", headers=ADMIN)
    assert response.status_code == 200, response.json()
    assert not MfaTotp.objects.filter(user=sam).exists()
    assert not WebAuthnCredential.objects.filter(user=sam).exists()
    assert session.get(ME, headers=ADMIN).status_code == 401  # signed out everywhere
    assert AuditLog.objects.filter(action="admin.mfa_reset", actor=owner).count() == 1
    assert login(APIClient(), "sam").json()["status"] == "mfa_setup_required"


def test_resets_are_guarded(sam: User, make_admin: AdminFactory) -> None:
    manager = make_admin(permissions=["admins.manage"], username="manny")
    owner = make_admin("owner", username="olga")
    client = APIClient()
    client.force_authenticate(manager)
    assert (
        client.post(f"/api/v1/admin/admins/{owner.pk}/reset-mfa", headers=ADMIN).status_code == 403
    )
    assert (
        client.post(f"/api/v1/admin/admins/{manager.pk}/reset-mfa", headers=ADMIN).status_code
        == 409
    )


def test_the_host_command_is_the_owners_last_resort(make_admin: AdminFactory) -> None:
    owner = make_admin("owner", username="olga")
    signed_in(owner)
    call_command("reset_admin_mfa", "olga")
    assert not MfaTotp.objects.filter(user=owner).exists()
    entry = AuditLog.objects.get(action="admin.mfa_reset")
    assert entry.actor is None
