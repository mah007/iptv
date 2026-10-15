"""The privacy policy and terms of service (SPEC §17, ADR-0023).

The texts are registry settings (`legal.privacy_en`, ...; Markdown) that start as the
templates in `legal_templates/`, which a lawyer should review before go-live: the
admin's Settings page says so while a text is still the template; customers never see
that note. Placeholders are filled in from Branding and Billing when a page is served:
`{service}`, `{support_email}`, `{support_phone}` and `{seller_address}`. A list item
whose placeholder is empty is left out (an address that isn't set yet); elsewhere an
empty contact reads as "our support team".
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from apps.core.models import Setting
from apps.core.services import get_setting

Document = Literal["privacy", "terms"]
Language = Literal["en", "ar"]
DOCUMENTS: Final[tuple[Document, ...]] = ("privacy", "terms")
LANGUAGES: Final[tuple[Language, ...]] = ("en", "ar")

_PLACEHOLDER = re.compile(r"\{(service|support_email|support_phone|seller_address)\}")
_LIST_ITEM = re.compile(r"\s*(?:[-*]|\d+\.)\s")
_SUPPORT_TEAM: Final = {"en": "our support team", "ar": "فريق الدعم"}


@dataclass(frozen=True, slots=True)
class LegalPage:
    document: Document
    language: Language
    body: str
    updated_at: datetime | None


def setting_key(document: Document, language: Language) -> str:
    return f"legal.{document}_{language}"


def _values(language: Language) -> dict[str, str]:
    return {
        "service": str(get_setting(f"branding.service_name_{language}")),
        "support_email": str(get_setting("branding.support_email")),
        "support_phone": str(get_setting("branding.support_phone")),
        "seller_address": str(get_setting("billing.seller_address")),
    }


def fill(text: str, values: dict[str, str], language: Language) -> str:
    """The text with its placeholders filled in (see the module docstring)."""
    contact = values["support_email"] or values["support_phone"] or _SUPPORT_TEAM[language]
    lines = []
    for line in text.splitlines():
        names = _PLACEHOLDER.findall(line)
        if names and _LIST_ITEM.match(line) and not all(values[name] for name in names):
            continue
        lines.append(
            _PLACEHOLDER.sub(
                lambda match: (
                    values[match.group(1)]
                    or (contact if match.group(1).startswith("support_") else "")
                ),
                line,
            )
        )
    return "\n".join(lines).strip() + "\n"


def page(document: Document, language: Language) -> LegalPage:
    key = setting_key(document, language)
    updated_at = Setting.objects.filter(key=key).values_list("updated_at", flat=True).first()
    return LegalPage(
        document=document,
        language=language,
        body=fill(str(get_setting(key)), _values(language), language),
        updated_at=updated_at,
    )
