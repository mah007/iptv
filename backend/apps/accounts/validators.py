"""Validation and normalisation of account values: phones, time zones, rule values."""

import ipaddress
import re
import zoneinfo
from functools import cache

import phonenumbers
from django.core.exceptions import ValidationError

# Numbers typed without a country code are read as Saudi numbers (SPEC §8.3: +966 default).
DEFAULT_PHONE_REGION = "SA"

_COUNTRY = re.compile(r"[A-Z]{2}")
# ISO 3166-1 codes of places without their own telephone numbering plan.
_COUNTRIES_WITHOUT_PHONE_PLAN = frozenset({"AQ", "BV", "GS", "HM", "PN", "TF", "UM"})


def normalize_phone(value: str) -> str:
    """Return the number in E.164 (`+966501234567`), or raise ValidationError."""
    try:
        number = phonenumbers.parse(value, DEFAULT_PHONE_REGION)
    except phonenumbers.NumberParseException:
        raise ValidationError("Enter a valid phone number.", code="invalid_phone") from None
    if not phonenumbers.is_valid_number(number):
        raise ValidationError("Enter a valid phone number.", code="invalid_phone")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def validate_e164_phone(value: str) -> None:
    """Model validator: stored phones are valid numbers already in E.164 form."""
    if value and normalize_phone(value) != value:
        raise ValidationError("Store phone numbers in E.164 form.", code="invalid_phone")


@cache
def _timezones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


def validate_timezone(value: str) -> None:
    if value not in _timezones():
        raise ValidationError("Enter a valid IANA time zone, e.g. Asia/Riyadh.", code="invalid")


def normalize_country(value: str) -> str:
    code = value.strip().upper()
    if not _COUNTRY.fullmatch(code) or not (
        code in phonenumbers.SUPPORTED_REGIONS or code in _COUNTRIES_WITHOUT_PHONE_PLAN
    ):
        raise ValidationError(
            "Enter an ISO 3166-1 alpha-2 country code, e.g. SA.", code="invalid_country"
        )
    return code


def normalize_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        raise ValidationError("Enter a valid IPv4 or IPv6 address.", code="invalid_ip") from None


def normalize_network(value: str) -> str:
    """CIDR in canonical form; host bits are cleared (`10.1.2.3/8` → `10.0.0.0/8`)."""
    try:
        return str(ipaddress.ip_network(value.strip(), strict=False))
    except ValueError:
        raise ValidationError(
            "Enter a valid network in CIDR notation, e.g. 203.0.113.0/24.", code="invalid_cidr"
        ) from None
