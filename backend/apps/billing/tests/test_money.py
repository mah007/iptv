"""Money in minor units: VAT splits (property-based), exponents and display."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from apps.billing import money

prices = st.integers(min_value=0, max_value=10_000_000)
rates = st.decimals(min_value=0, max_value=1, places=4)


@given(price=prices, rate=rates, inclusive=st.booleans())
def test_vat_splits_always_add_up(price: int, rate: Decimal, inclusive: bool) -> None:
    split = money.split_vat(price, rate, inclusive=inclusive)
    assert split.net + split.vat == split.total
    assert split.net >= 0
    assert split.vat >= 0
    assert split.total == (price if inclusive else price + split.vat)
    assert split.net == (split.net if inclusive else price)


@given(price=prices, rate=rates)
def test_vat_is_the_rate_of_the_net_within_rounding(price: int, rate: Decimal) -> None:
    for inclusive in (True, False):
        split = money.split_vat(price, rate, inclusive=inclusive)
        assert abs(Decimal(split.vat) - Decimal(split.net) * rate) <= Decimal(1) + rate


def test_saudi_vat_examples() -> None:
    inclusive = money.split_vat(4900, Decimal("0.15"), inclusive=True)
    assert (inclusive.net, inclusive.vat, inclusive.total) == (4261, 639, 4900)
    exclusive = money.split_vat(4900, Decimal("0.15"), inclusive=False)
    assert (exclusive.net, exclusive.vat, exclusive.total) == (4900, 735, 5635)


def test_bad_splits_are_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        money.split_vat(-1, Decimal("0.15"), inclusive=True)
    with pytest.raises(ValueError, match="between 0 and 1"):
        money.split_vat(100, Decimal("1.5"), inclusive=True)


@pytest.mark.parametrize(
    ("amount", "currency", "locale", "expected"),
    [
        (4900, "SAR", "en", "49.00 SAR"),
        (4900, "SAR", "ar", "49.00 ر.س"),
        (123456, "USD", "ar", "1,234.56 USD"),
        (1500, "KWD", "en", "1.500 KWD"),
        (900, "JPY", "en", "900 JPY"),
    ],
)
def test_display(amount: int, currency: str, locale: str, expected: str) -> None:
    assert money.display(amount, currency, locale) == expected
