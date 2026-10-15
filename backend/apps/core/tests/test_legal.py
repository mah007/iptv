"""The legal pages (SPEC §17, ADR-0023): templates in the registry, placeholders filled
in from Branding and Billing, a public API on the portal and app hosts."""

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client

from apps.core import legal, registry
from apps.core.services import set_setting

pytestmark = pytest.mark.django_db
PAGE = "/api/v1/legal"


def test_the_templates_are_the_defaults_in_both_languages() -> None:
    for document in legal.DOCUMENTS:
        for language in legal.LANGUAGES:
            definition = registry.get_definition(legal.setting_key(document, language))
            assert definition.widget == "text"
            assert definition.default == registry.legal_template(document, language)
            assert str(definition.default).startswith("# ")
    assert "PDPL" in registry.legal_template("privacy", "en")


def test_placeholders_are_filled_in_and_empty_contacts_left_out() -> None:
    values = {
        "service": "Lantern TV",
        "support_email": "help@example.com",
        "support_phone": "",
        "seller_address": "",
    }
    text = "\n".join(
        [
            "# {service}",
            "Write to {support_email}.",
            "- Email: {support_email}",
            "- Phone: {support_phone}",
            "- Address: {seller_address}",
        ]
    )
    filled = legal.fill(text, values, "en")
    assert filled == "# Lantern TV\nWrite to help@example.com.\n- Email: help@example.com\n"
    nobody = legal.fill("Write to {support_email}.", {**values, "support_email": ""}, "ar")
    assert nobody == "Write to فريق الدعم.\n"


@pytest.mark.parametrize("host", [settings.APP_HOST, settings.API_HOST])
def test_the_public_api_serves_both_pages(host: str) -> None:
    set_setting("branding.service_name_en", "Lantern TV", actor=None)
    set_setting("branding.support_email", "help@example.com", actor=None)
    client = Client(headers={"host": host})
    response = client.get(f"{PAGE}/privacy", {"lang": "en"})
    assert response.status_code == 200, response.content
    assert response["Cache-Control"] == "public, max-age=60"
    body = response.json()
    assert (body["document"], body["language"], body["updated_at"]) == ("privacy", "en", None)
    assert body["body"].startswith("# Privacy policy")
    assert "Lantern TV" in body["body"]
    assert "{service}" not in body["body"]
    assert "Phone:" not in body["body"]  # no phone number set
    arabic = client.get(f"{PAGE}/terms", {"lang": "ar"}).json()
    assert arabic["body"].startswith("# شروط الخدمة")
    assert client.get(f"{PAGE}/cookies").status_code == 404


def test_an_admins_text_and_its_date() -> None:
    set_setting("legal.terms_en", "# Our terms\n\nBe kind to {service}.", actor=None)
    body = Client(headers={"host": settings.APP_HOST}).get(f"{PAGE}/terms?lang=en").json()
    assert body["body"] == "# Our terms\n\nBe kind to Smart IPTV.\n"
    assert body["updated_at"] is not None


def test_the_default_language_applies_without_lang() -> None:
    set_setting("branding.default_language", "ar", actor=None)
    body = Client(headers={"host": settings.APP_HOST}).get(f"{PAGE}/privacy").json()
    assert body["language"] == "ar"


def test_texts_have_a_length_limit() -> None:
    definition = registry.get_definition("legal.privacy_en")
    with pytest.raises(ValidationError, match="under"):
        definition.clean("x" * (registry.LEGAL_MAX_CHARS + 1))
