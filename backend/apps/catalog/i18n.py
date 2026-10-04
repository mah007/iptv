"""The language of customer API responses (SPEC §9, §10): Arabic or English.

`request_locale` picks, in order: the first of `ar`/`en` named in Accept-Language
(the portal sends its UI language), then the signed-in customer's saved locale,
then English. Views pass it to serializers as `context["locale"]`, and responses
say which one they used (`Content-Language`, `Vary: Accept-Language`).

`localized` picks a field's Arabic twin (`title_ar`, `name_ar`, ...) for Arabic
when it is filled in, else the English field: titles never come back empty.
"""

from typing import Any, Final, Literal

from django.http import HttpRequest, HttpResponseBase
from rest_framework.request import Request

type Locale = Literal["ar", "en"]

LOCALES: Final[tuple[Locale, ...]] = ("ar", "en")
DEFAULT_LOCALE: Final[Locale] = "en"
_MAX_HEADER = 256


def _from_header(header: str) -> Locale | None:
    """The best of ar/en by quality in an Accept-Language header, or None."""
    best: tuple[float, int, Locale] | None = None
    for position, part in enumerate(header[:_MAX_HEADER].split(",")):
        tag, _, params = part.strip().partition(";")
        language = tag.strip().lower().split("-", 1)[0]
        if language not in LOCALES:
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality <= 0:
            continue
        # Higher quality wins; at equal quality the earlier entry does.
        locale: Locale = "ar" if language == "ar" else "en"
        candidate = (quality, -position, locale)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best is not None else None


def request_locale(request: HttpRequest | Request) -> Locale:
    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    chosen = _from_header(header) if header else None
    if chosen is not None:
        return chosen
    user = getattr(request, "user", None)
    saved = getattr(user, "locale", None) if getattr(user, "is_authenticated", False) else None
    return "ar" if saved == "ar" else DEFAULT_LOCALE


def localized(obj: Any, field: str, locale: Locale) -> str:
    """`obj.<field>_ar` for Arabic when filled in, else `obj.<field>`."""
    if locale == "ar":
        arabic = getattr(obj, f"{field}_ar", "") or ""
        if arabic:
            return str(arabic)
    return str(getattr(obj, field, "") or "")


def mark_language(response: HttpResponseBase, locale: Locale) -> HttpResponseBase:
    response["Content-Language"] = locale
    vary = response.get("Vary", "")
    if "accept-language" not in vary.lower():
        response["Vary"] = f"{vary}, Accept-Language" if vary else "Accept-Language"
    return response
