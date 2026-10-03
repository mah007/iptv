"""Account model rules and value validation (SPEC §6 accounts)."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts import validators
from apps.accounts.models import CustomerAccess, User, UserStatus

pytestmark = pytest.mark.django_db


def test_emails_are_lowercased_and_unique_case_insensitively() -> None:
    user = User.objects.create_user(username="a", email="  Mixed@Example.COM ")
    assert user.email == "mixed@example.com"
    with transaction.atomic(), pytest.raises(IntegrityError):
        User.objects.create_user(username="b", email="MIXED@example.com")
    # Customers may have no email at all: blanks never clash.
    User.objects.create_user(username="c")
    User.objects.create_user(username="d")


def test_status_drives_is_active() -> None:
    user = User.objects.create_user(username="a", name=" Sara ")
    assert user.is_active
    assert user.get_full_name() == "Sara"
    assert user.get_short_name() == "Sara"
    assert str(user) == "a"
    user.status = UserStatus.DISABLED
    user.save(update_fields=["status"])
    user.refresh_from_db()
    assert not user.is_active
    user.status = UserStatus.SUSPENDED
    user.save()
    assert user.is_active  # suspended accounts keep their login, not their playback
    assert User(username="x").get_full_name() == "x"


def test_access_profile_limits_are_enforced_by_the_database() -> None:
    user = User.objects.create_user(username="a")
    with transaction.atomic(), pytest.raises(IntegrityError):
        CustomerAccess.objects.create(user=user, max_streams=0)
    with transaction.atomic(), pytest.raises(IntegrityError):
        CustomerAccess.objects.create(user=user, max_quality=999)
    access = CustomerAccess.objects.create(user=user)
    assert str(access) == f"access:{user.pk}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0501234567", "+966501234567"),
        ("+966 50 123 4567", "+966501234567"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_phone_numbers_are_normalised_to_e164(raw: str, expected: str) -> None:
    assert validators.normalize_phone(raw) == expected
    validators.validate_e164_phone(expected)


@pytest.mark.parametrize("raw", ["12", "not a phone", "+966 12"])
def test_invalid_phone_numbers(raw: str) -> None:
    with pytest.raises(ValidationError):
        validators.normalize_phone(raw)


def test_stored_phones_must_already_be_e164() -> None:
    with pytest.raises(ValidationError):
        validators.validate_e164_phone("0501234567")
    validators.validate_e164_phone("")


def test_rule_values() -> None:
    assert validators.normalize_ip(" 2001:db8::1 ") == "2001:db8::1"
    assert validators.normalize_network("10.1.2.3/8") == "10.0.0.0/8"
    assert validators.normalize_country(" sa ") == "SA"
    assert validators.normalize_country("aq") == "AQ"  # no phone plan, still a country
    for bad, normalize in [
        ("10.0.0", validators.normalize_ip),
        ("10.0.0.0/33", validators.normalize_network),
        ("ZZ", validators.normalize_country),
        ("SAU", validators.normalize_country),
    ]:
        with pytest.raises(ValidationError):
            normalize(bad)


def test_time_zones() -> None:
    validators.validate_timezone("Asia/Riyadh")
    with pytest.raises(ValidationError):
        validators.validate_timezone("Mars/Base")
