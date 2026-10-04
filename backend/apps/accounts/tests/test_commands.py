"""`manage.py seed_demo` (make seed) and the DEBUG-only `manage.py totp_code`."""

from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.accounts import crypto, mfa, services
from apps.accounts.management.commands import seed_demo, totp_code
from apps.accounts.models import Device, MfaTotp, User
from apps.accounts.rbac import OWNER_ROLE
from apps.audit.models import AuditLog
from apps.catalog.models import Category

pytestmark = pytest.mark.django_db


def run(*args: str) -> str:
    out = StringIO()
    call_command("seed_demo", *args, stdout=out)
    return out.getvalue()


def password_in(output: str) -> str:
    [line] = [line for line in output.splitlines() if "Password (shown once" in line]
    return line.rsplit(" ", 1)[1]


def test_seed_creates_the_demo_world_once() -> None:
    output = run()
    admin = User.objects.get(username=seed_demo.ADMIN_USERNAME)
    assert admin.is_staff
    assert admin.roles.filter(name=OWNER_ROLE).exists()
    assert admin.check_password(password_in(output))
    assert not admin.mfa_enabled

    customers = User.objects.filter(is_staff=False)
    assert customers.count() == len(seed_demo.DEMO_CUSTOMERS)
    assert Category.objects.count() == len(seed_demo.CATEGORIES)
    assert services.access_status_counts() == {
        "active": 19,
        "expired": 3,
        "suspended": 2,
        "disabled": 0,
    }
    assert customers.filter(access__expires_at__isnull=True).count() == 1
    assert Device.objects.filter(user__in=customers, credential__isnull=False).count() == sum(
        len(demo.devices) for demo in seed_demo.DEMO_CUSTOMERS
    )
    expired = AuditLog.objects.filter(action="customer.access.expire").count()
    assert expired == 3
    assert "3 access periods marked expired" in output

    # Idempotent: nothing new, the admin's password unchanged.
    again = run()
    assert "Password" not in again
    assert "0 new customers" in again
    assert User.objects.filter(is_staff=False).count() == len(seed_demo.DEMO_CUSTOMERS)
    assert AuditLog.objects.filter(action="customer.access.expire").count() == 3


def test_reset_admin_password() -> None:
    first = password_in(run())
    second = password_in(run("--reset-admin-password"))
    admin = User.objects.get(username=seed_demo.ADMIN_USERNAME)
    assert first != second
    assert admin.check_password(second)
    actions = AuditLog.objects.filter(target_id=str(admin.pk)).values_list("action", flat=True)
    assert sorted(actions) == ["admin.create", "admin.password_reset"]


def test_reset_admin_mfa() -> None:
    run()
    admin = User.objects.get(username=seed_demo.ADMIN_USERNAME)
    MfaTotp.objects.create(user=admin, secret_encrypted=crypto.encrypt(mfa.new_secret()))
    User.objects.filter(pk=admin.pk).update(mfa_enabled=True)
    output = run("--reset-admin-mfa")
    assert "MFA reset" in output
    assert not MfaTotp.objects.filter(user=admin).exists()
    admin.refresh_from_db()
    assert not admin.mfa_enabled
    assert AuditLog.objects.filter(action="admin.mfa_reset", target_id=str(admin.pk)).exists()


def test_totp_code_refuses_without_debug(owner: User) -> None:
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("totp_code", owner.username)


@override_settings(DEBUG=True)
def test_totp_code_prints_the_next_acceptable_code(
    owner: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(CommandError, match="no authenticator"):
        call_command("totp_code", owner.username)
    secret = mfa.new_secret()
    totp = MfaTotp.objects.create(user=owner, secret_encrypted=crypto.encrypt(secret))
    monkeypatch.setattr(mfa, "current_step", lambda now=None: 1000)

    def code() -> str:
        out = StringIO()
        call_command("totp_code", owner.username, stdout=out)
        return out.getvalue().strip()

    assert code() == mfa.code_at(secret, 1000)
    totp.last_used_step = 1000
    totp.save()
    assert code() == mfa.code_at(secret, 1001)  # within the drift window: no wait

    slept: list[float] = []
    fake_time = SimpleNamespace(time=lambda: 1000 * mfa.STEP_S + 5, sleep=slept.append)
    monkeypatch.setattr(totp_code, "time", fake_time)
    totp.last_used_step = 1001
    totp.save()
    assert code() == mfa.code_at(secret, 1002)
    assert slept == [pytest.approx(25.0)]


def test_create_owner_bootstraps_the_admin_without_demo_data() -> None:
    out = StringIO()
    call_command("create_owner", stdout=out)
    owner = User.objects.get(username=seed_demo.ADMIN_USERNAME)
    assert owner.is_staff
    assert owner.roles.filter(name=OWNER_ROLE).exists()
    assert "Password (shown once" in out.getvalue()
    assert not User.objects.filter(is_staff=False).exists()
    assert not Category.objects.exists()

    again = StringIO()
    call_command("create_owner", stdout=again)
    assert "already exists" in again.getvalue()
    assert "Password" not in again.getvalue()
