"""The template sandbox: only whitelisted tags and filters, escaped values, no internals."""

import pytest

from apps.notifications import rendering
from apps.notifications.defaults import DEFAULTS, EVENTS


def test_variables_loops_and_conditions() -> None:
    text = rendering.render(
        "Hi {{ name }}{% if usernames %}: {{ usernames|join:', ' }}{% endif %}"
        "{% for u in usernames %}[{{ u|upper }}]{% endfor %}",
        {"name": "Sara", "usernames": ["a-1", "b-2"]},
    )
    assert text == "Hi Sara: a-1, b-2[A-1][B-2]"


def test_html_escapes_values_and_text_does_not() -> None:
    context = {"name": "<script>alert(1)</script> & co"}
    assert rendering.render("{{ name }}", context, html=True) == (
        "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co"
    )
    assert rendering.render("{{ name }}", context) == "<script>alert(1)</script> & co"


@pytest.mark.parametrize(
    "source",
    [
        "{% load static %}",
        "{% include 'billing/invoice.html' %}",
        "{% extends 'base.html' %}",
        "{% debug %}",
        "{% url 'admin-plans-list' %}",
        "{% csrf_token %}",
        "{{ name|safe }}",
        "{{ name|pprint }}",
        "{{ name|stringformat:'09999999d' }}",
        "{{ name.__class__ }}",
        "{% if %}",
    ],
)
def test_the_sandbox_refuses_everything_else(source: str) -> None:
    with pytest.raises(rendering.TemplateError):
        rendering.check(source, html=True)
    with pytest.raises(rendering.TemplateError):
        rendering.render(source, {"name": "x"})


def test_templates_are_length_limited() -> None:
    with pytest.raises(rendering.TemplateError, match="limited"):
        rendering.check("x" * (rendering.MAX_TEMPLATE_LENGTH + 1))


def test_contexts_are_plain_copies() -> None:
    payload = {"items": [{"a": 1}], "other": object()}
    assert rendering.render("{{ items.0.a }}{{ items.clear }}{{ items|length }}", payload) == (
        "11"  # read-only copies have no clear(); the caller's list is untouched
    )
    assert payload["items"] == [{"a": 1}]
    assert rendering.render("{{ other }}", payload).startswith("<object object")


def test_messages_use_the_layout_and_one_line_subjects() -> None:
    message = rendering.render_message(
        "Hello\n  {{ name }}",
        "Line one\n\nLine <two>",
        "",
        {"name": "Sara", "service_name": "Smart IPTV", "accent_color": "#123456"},
        locale="ar",
    )
    assert message.subject == "Hello Sara"
    assert message.text == "Line one\n\nLine <two>"
    assert 'dir="rtl"' in message.html
    assert "<p>Line one</p><p>Line &lt;two&gt;</p>" in message.html
    assert "#123456" in message.html


def test_every_default_renders_with_its_sample() -> None:
    for (key, locale), template in DEFAULTS.items():
        sample = {"service_name": "S", "name": "N", "support_email": "", "portal_url": "u"}
        context = {**sample, **EVENTS[key].sample}
        message = rendering.render_message(
            template.subject, template.body_text, template.body_html, context, locale=locale
        )
        assert message.subject, key
        assert "{{" not in message.text, key
        for variable in EVENTS[key].variables:
            value = EVENTS[key].sample[variable]
            if isinstance(value, str) and value:
                assert value in message.text, (key, locale, variable)
    assert {key for key, _locale in DEFAULTS} == set(EVENTS)
    assert all((key, "ar") in DEFAULTS and (key, "en") in DEFAULTS for key in EVENTS)
