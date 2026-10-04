"""Rendering notification templates in a sandbox (SPEC §7.7, ADR-0012).

Admins edit templates, so a template is untrusted input. It renders with Django's
template language through `SandboxEngine`, whose only built-ins are the
whitelist in `template_library`: no `load`, `include`, `extends`, `debug` or
`url`, and no template loaders at all, so a template reaches nothing but its
context. The context is a read-only copy of plain JSON values (strings, numbers,
tuples, read-only mappings) built for the message; Django refuses names that
start with an underscore. Templates
and their output are length-limited.

HTML bodies autoescape every value; subjects and text bodies don't (they are
not HTML).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from types import MappingProxyType
from typing import Any, Final

from django.template import Context, Engine, TemplateSyntaxError

MAX_TEMPLATE_LENGTH: Final = 20_000
MAX_SUBJECT_LENGTH: Final = 200


class SandboxEngine(Engine):
    default_builtins = ["apps.notifications.template_library"]  # noqa: RUF012 (Django's API)


_HTML = SandboxEngine(dirs=[], app_dirs=False, libraries={}, autoescape=True)
_TEXT = SandboxEngine(dirs=[], app_dirs=False, libraries={}, autoescape=False)


class TemplateError(ValueError):
    """A template that does not parse, or is too long."""


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    subject: str
    text: str
    html: str


def _source(source: str) -> str:
    if len(source) > MAX_TEMPLATE_LENGTH:
        msg = f"Templates are limited to {MAX_TEMPLATE_LENGTH} characters."
        raise TemplateError(msg)
    return source


def check(source: str, *, html: bool = False) -> None:
    """Raise TemplateError unless `source` parses in the sandbox."""
    try:
        (_HTML if html else _TEXT).from_string(_source(source))
    except TemplateSyntaxError as exc:
        raise TemplateError(str(exc)) from None


def render(source: str, context: Mapping[str, Any], *, html: bool = False) -> str:
    try:
        template = (_HTML if html else _TEXT).from_string(_source(source))
    except TemplateSyntaxError as exc:
        raise TemplateError(str(exc)) from None
    return template.render(Context(dict(_frozen(context)), autoescape=html))


def _frozen(value: Any) -> Any:
    """A deep, read-only copy of JSON-like values: templates call no-argument methods,
    so containers are tuples and read-only mappings, which have no mutating ones."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _frozen(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def render_message(
    subject: str, body_text: str, body_html: str, context: Mapping[str, Any], *, locale: str
) -> RenderedMessage:
    """Subject (one line), text body and the HTML email (the body in the layout).

    Without an HTML body, the HTML email shows the text body with its line breaks.
    """
    rendered_subject = " ".join(render(subject, context).split())[:MAX_SUBJECT_LENGTH]
    text = render(body_text, context).strip()
    if body_html.strip():
        content = render(body_html, context, html=True)
    else:
        content = "<p>" + escape(text).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    return RenderedMessage(
        subject=rendered_subject,
        text=text,
        html=wrap_html(content, title=rendered_subject, locale=locale, context=context),
    )


_LAYOUT = """<!doctype html>
<html lang="{lang}" dir="{dir}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:'Segoe UI',Tahoma,Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden">
<tr><td style="background:{accent};color:#ffffff;padding:18px 24px;font-size:18px;font-weight:600;
text-align:{align}">{service}</td></tr>
<tr><td style="padding:24px;color:#1f2933;font-size:15px;line-height:1.6;text-align:{align}">
{content}</td></tr>
</table></td></tr></table></body></html>"""


def wrap_html(content: str, *, title: str, locale: str, context: Mapping[str, Any]) -> str:
    rtl = locale == "ar"
    accent = str(context.get("accent_color") or "#0F766E")
    # Every interpolated value is escaped; `content` was rendered with autoescape.
    return _LAYOUT.format(
        lang="ar" if rtl else "en",
        dir="rtl" if rtl else "ltr",
        align="right" if rtl else "left",
        title=escape(title),
        accent=escape(accent),
        service=escape(str(context.get("service_name") or "")),
        content=content,
    )
