"""Money in integer minor units (SPEC §6): exponents, display and VAT.

Amounts never pass through floats. VAT is split with Decimal and rounded half up
to the minor unit, so `net + vat == total` always holds.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: ISO 4217 minor-unit exponents that differ from 2 (amounts are stored in minor units).
_EXPONENTS = {
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
    "JPY": 0,
    "KRW": 0,
    "CLP": 0,
    "VND": 0,
    "XAF": 0,
    "XOF": 0,
}
_SYMBOLS_AR = {"SAR": "ر.س", "AED": "د.إ", "KWD": "د.ك", "BHD": "د.ب", "QAR": "ر.ق", "OMR": "ر.ع"}


def exponent(currency: str) -> int:
    return _EXPONENTS.get(currency.upper(), 2)


def to_decimal(amount: int, currency: str) -> Decimal:
    """Minor units as a decimal amount: 2900 SAR -> Decimal("29.00")."""
    places = exponent(currency)
    return (Decimal(amount) / (Decimal(10) ** places)).quantize(Decimal(1).scaleb(-places))


def display(amount: int, currency: str, locale: str = "en") -> str:
    """`29.00 SAR`, or `29.00 ر.س` in Arabic (Western digits, as Saudi invoices use)."""
    value = f"{to_decimal(amount, currency):,}"
    unit = _SYMBOLS_AR.get(currency.upper(), currency.upper()) if locale == "ar" else currency
    return f"{value} {unit}"


@dataclass(frozen=True, slots=True)
class VatSplit:
    net: int
    vat: int
    total: int
    rate: Decimal


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def split_vat(price: int, rate: Decimal, *, inclusive: bool) -> VatSplit:
    """Net, VAT and total of a price in minor units.

    `inclusive`: the price already holds VAT (Saudi consumer prices do), so VAT is
    carved out of it; otherwise VAT is added on top.
    """
    if price < 0:
        msg = "price must not be negative"
        raise ValueError(msg)
    if not Decimal(0) <= rate <= Decimal(1):
        msg = "VAT rate must be between 0 and 1"
        raise ValueError(msg)
    if inclusive:
        net = _round(Decimal(price) / (Decimal(1) + rate))
        return VatSplit(net=net, vat=price - net, total=price, rate=rate)
    vat = _round(Decimal(price) * rate)
    return VatSplit(net=price, vat=vat, total=price + vat, rate=rate)
